from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import UploadFile

from app.core.config import Settings
from app.domain.entities.sala import ContextoDestino, DocumentoRecibido, Sala
from app.domain.exceptions import (
    AccesoSalaInvalidoError,
    DocumentoInvalidoError,
    SalaNoDisponibleError,
    SalaNoEncontradaError,
)
from app.infrastructure.destinations.kaisvm_adapter import KaisvmAdapter
from app.infrastructure.qr.qr_code_generator import QrCodeGenerator
from app.infrastructure.repositories.memory_sala_repository import MemorySalaRepository
from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal, TemporaryFileStorage
from app.infrastructure.websocket.connection_manager import ConnectionManager


class GestionSalas:
    def __init__(
        self,
        repository: MemorySalaRepository,
        qr_generator: QrCodeGenerator,
        temporary_storage: TemporaryFileStorage,
        destination_adapter: KaisvmAdapter,
        connection_manager: ConnectionManager,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.qr_generator = qr_generator
        self.temporary_storage = temporary_storage
        self.destination_adapter = destination_adapter
        self.connection_manager = connection_manager
        self.settings = settings

    def crear_sala(self, contexto_destino: ContextoDestino) -> dict[str, Any]:
        ahora = datetime.now(timezone.utc)
        codigo = self._generar_codigo_unico()
        token_invitacion = secrets.token_urlsafe(32)
        token_observador = secrets.token_urlsafe(32)
        sala = Sala(
            sala_id=str(uuid.uuid4()),
            codigo=codigo,
            contexto_destino=contexto_destino,
            tokens_invitacion_hash={Sala.generar_hash_token(token_invitacion)},
            token_observador_hash=Sala.generar_hash_token(token_observador),
            creada_en=ahora,
            expira_en=ahora + timedelta(seconds=self.settings.session_ttl_seconds),
        )
        self.repository.guardar(sala)

        qr_payload = json.dumps(
            {
                "version": 1,
                "tipo": "sala_carga",
                "sala_id": sala.sala_id,
                "codigo": sala.codigo,
                "token": token_invitacion,
            },
            separators=(",", ":"),
        )
        return {
            "sala_id": sala.sala_id,
            "codigo": sala.codigo,
            "estado": sala.estado.value,
            "expira_en": sala.expira_en.isoformat(),
            "qr_payload": qr_payload,
            "qr_image_data_uri": self.qr_generator.generar_data_uri(qr_payload),
            "observador_token": token_observador,
            "websocket_url": f"{self.settings.public_websocket_url}/salas/{sala.sala_id}",
        }

    def obtener_sala(self, sala_id: str) -> Sala:
        sala = self.repository.obtener(sala_id)
        if sala is None:
            raise SalaNoEncontradaError("La sala no existe")
        sala.esta_activa()
        return sala

    def validar_acceso(self, sala_id: str, token: str) -> str:
        sala = self.obtener_sala(sala_id)
        if not sala.esta_activa():
            raise SalaNoDisponibleError("La sala ya no está disponible")
        return sala.validar_token(token)

    def conectar_movil(self, sala_id: str, token: str) -> Sala:
        sala = self.obtener_sala(sala_id)
        rol = sala.validar_token(token)
        if rol != "movil":
            raise AccesoSalaInvalidoError("El token no permite conectar la aplicación móvil")
        sala.conectar_movil()
        self.repository.guardar(sala)
        return sala

    def unirse_por_codigo(self, codigo: str) -> dict[str, Any]:
        sala = self.repository.buscar_por_codigo(codigo)
        if sala is None or not sala.esta_activa():
            raise SalaNoDisponibleError("El código no corresponde a una sala activa")

        token_invitacion = secrets.token_urlsafe(32)
        sala.tokens_invitacion_hash.add(Sala.generar_hash_token(token_invitacion))
        self.repository.guardar(sala)
        return {
            "sala_id": sala.sala_id,
            "codigo": sala.codigo,
            "estado": sala.estado.value,
            "expira_en": sala.expira_en.isoformat(),
            "token": token_invitacion,
            "websocket_url": f"{self.settings.public_websocket_url}/salas/{sala.sala_id}",
        }

    async def registrar_documento(
        self,
        sala_id: str,
        token: str,
        archivo: UploadFile,
        nombre_personalizado: str | None,
    ) -> dict[str, Any]:
        sala = self.obtener_sala(sala_id)
        rol = sala.validar_token(token)
        if rol != "movil":
            raise AccesoSalaInvalidoError("Solo la aplicación móvil puede enviar documentos")
        if not archivo.filename:
            raise DocumentoInvalidoError("El archivo debe tener un nombre")

        archivo_temporal = await self.temporary_storage.guardar(
            sala_id=sala_id,
            archivo=archivo,
            max_size_bytes=self.settings.max_file_size_bytes,
        )
        documento = DocumentoRecibido(
            documento_id=archivo_temporal.documento_id,
            nombre_original=archivo_temporal.nombre_original,
            nombre_personalizado=nombre_personalizado,
            tipo_mime=archivo_temporal.tipo_mime,
            peso_bytes=archivo_temporal.peso_bytes,
            ruta_temporal=str(archivo_temporal.ruta),
        )
        try:
            sala.registrar_documento(documento, self.settings.max_documents_per_session)
            self.repository.guardar(sala)
        except Exception:
            self.temporary_storage.eliminar_archivo(archivo_temporal)
            raise

        await self.connection_manager.broadcast(
            sala_id,
            {
                "tipo": "documento_recibido",
                "documento_id": documento.documento_id,
                "nombre": documento.nombre_personalizado or documento.nombre_original,
                "cantidad_documentos": len(sala.documentos),
                "estado": sala.estado.value,
            },
        )
        return self._documento_respuesta(documento, sala)

    async def finalizar_sala(self, sala_id: str, token: str) -> dict[str, Any]:
        sala = self.obtener_sala(sala_id)
        sala.validar_token(token)
        sala.iniciar_finalizacion()
        self.repository.guardar(sala)
        await self.connection_manager.broadcast(sala_id, self._estado_evento(sala, "Procesando documentos"))

        archivos = [self.temporary_storage.desde_documento(documento) for documento in sala.documentos]
        try:
            resultado = await self.destination_adapter.entregar(sala, archivos)
            sala.completar()
            self.repository.guardar(sala)
            await self.connection_manager.broadcast(sala_id, self._estado_evento(sala, resultado.mensaje))
            self.temporary_storage.eliminar_sala(sala_id)
            return {
                "sala_id": sala.sala_id,
                "estado": sala.estado.value,
                "mensaje": resultado.mensaje,
                "resultado_destino": resultado.datos,
            }
        except Exception as error:
            sala.marcar_error(str(error))
            self.repository.guardar(sala)
            await self.connection_manager.broadcast(sala_id, self._estado_evento(sala, str(error)))
            raise

    def estado_sala(self, sala_id: str, token: str) -> dict[str, Any]:
        sala = self.obtener_sala(sala_id)
        sala.validar_token(token)
        return self._sala_respuesta(sala)

    def _generar_codigo_unico(self) -> str:
        for _ in range(20):
            codigo = f"{secrets.randbelow(1_000_000):06d}"
            sala = self.repository.buscar_por_codigo(codigo)
            if sala is None or not sala.esta_activa():
                return codigo
        raise SalaNoDisponibleError("No fue posible generar un código de sala")

    @staticmethod
    def _estado_evento(sala: Sala, mensaje: str) -> dict[str, Any]:
        return {
            "tipo": "estado_sala",
            "sala_id": sala.sala_id,
            "estado": sala.estado.value,
            "mensaje": mensaje,
        }

    @staticmethod
    def _documento_respuesta(documento: DocumentoRecibido, sala: Sala) -> dict[str, Any]:
        return {
            "documento_id": documento.documento_id,
            "nombre": documento.nombre_personalizado or documento.nombre_original,
            "tipo_mime": documento.tipo_mime,
            "peso_bytes": documento.peso_bytes,
            "cantidad_documentos": len(sala.documentos),
            "estado": sala.estado.value,
        }

    @staticmethod
    def _sala_respuesta(sala: Sala) -> dict[str, Any]:
        return {
            "sala_id": sala.sala_id,
            "codigo": sala.codigo,
            "estado": sala.estado.value,
            "expira_en": sala.expira_en.isoformat(),
            "contexto_destino": {
                "sistema": sala.contexto_destino.sistema,
                "modulo": sala.contexto_destino.modulo,
                "recurso_id": sala.contexto_destino.recurso_id,
                "operacion": sala.contexto_destino.operacion,
            },
            "cantidad_documentos": len(sala.documentos),
            "movil_conectado": sala.movil_conectado,
            "mensaje_error": sala.mensaje_error,
        }
