from __future__ import annotations

import json
import logging
import math
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import UploadFile

from app.core.config import Settings
from app.domain.entities.sala import ContextoDestino, DocumentoRecibido, EstadoSala, Sala
from app.domain.exceptions import (
    AccesoSalaInvalidoError,
    DocumentoInvalidoError,
    SalaNoDisponibleError,
    SalaNoEncontradaError,
)
from app.infrastructure.destinations.kaisvm_adapter import KaisvmAdapter
from app.infrastructure.documents.pdf_creator import PdfCreator
from app.infrastructure.qr.qr_code_generator import QrCodeGenerator
from app.infrastructure.repositories.memory_sala_repository import MemorySalaRepository
from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal, TemporaryFileStorage
from app.infrastructure.websocket.connection_manager import ConnectionManager


logger = logging.getLogger(__name__)


class GestionSalas:
    def __init__(
        self,
        repository: MemorySalaRepository,
        qr_generator: QrCodeGenerator,
        temporary_storage: TemporaryFileStorage,
        destination_adapter: KaisvmAdapter,
        pdf_creator: PdfCreator,
        connection_manager: ConnectionManager,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.qr_generator = qr_generator
        self.temporary_storage = temporary_storage
        self.destination_adapter = destination_adapter
        self.pdf_creator = pdf_creator
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
        tipo_archivo: str,
        poligono: str | None,
        lote_pdf_id: str | None = None,
        nombre_pdf: str | None = None,
    ) -> dict[str, Any]:
        sala = self.obtener_sala(sala_id)
        rol = sala.validar_token(token)
        if rol != "movil":
            raise AccesoSalaInvalidoError("Solo la aplicación móvil puede enviar documentos")
        if not archivo.filename:
            raise DocumentoInvalidoError("El archivo debe tener un nombre")

        tipo_archivo_normalizado = tipo_archivo.strip().lower()
        if tipo_archivo_normalizado not in {"imagen", "pdf"}:
            raise DocumentoInvalidoError("El tipo de archivo no es válido")
        poligono_normalizado = (
            self._parsear_poligono(poligono) if tipo_archivo_normalizado == "pdf" else []
        )
        lote_pdf_id_normalizado = (
            self._normalizar_lote_pdf_id(lote_pdf_id)
            if tipo_archivo_normalizado == "pdf"
            else None
        )
        nombre_pdf_normalizado = (
            self._normalizar_nombre_pdf(nombre_pdf)
            if tipo_archivo_normalizado == "pdf"
            else None
        )
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
            tipo_archivo=tipo_archivo_normalizado,
            poligono=poligono_normalizado,
            lote_pdf_id=lote_pdf_id_normalizado,
            nombre_pdf=nombre_pdf_normalizado,
        )
        try:
            sala.registrar_documento(documento, self.settings.max_documents_per_session)
            self.repository.guardar(sala)
        except Exception:
            self.temporary_storage.eliminar_archivo(archivo_temporal)
            raise

        logger.info(
            "[DOCUMENTO] Recibido | sala=%s | documento=%s | nombre=%s | "
            "tipo=%s | lote_pdf=%s | nombre_pdf=%s | mime=%s | bytes=%d | total_sala=%d",
            sala_id,
            documento.documento_id,
            documento.nombre_original,
            documento.tipo_archivo,
            documento.lote_pdf_id or "-",
            documento.nombre_pdf or "-",
            documento.tipo_mime,
            documento.peso_bytes,
            len(sala.documentos),
        )

        await self.connection_manager.broadcast(
            sala_id,
            {
                "tipo": "documento_recibido",
                "documento_id": documento.documento_id,
                "nombre": documento.nombre_personalizado or documento.nombre_original,
                "cantidad_documentos": len(sala.documentos),
                "cantidad_pdf": self._contar_lotes_pdf(sala),
                "estado": sala.estado.value,
                "lote_pdf_id": documento.lote_pdf_id,
                "nombre_pdf": documento.nombre_pdf,
            },
        )
        return self._documento_respuesta(documento, sala)

    async def finalizar_sala(self, sala_id: str, token: str) -> dict[str, Any]:
        sala = self.obtener_sala(sala_id)
        sala.validar_token(token)
        logger.info(
            "[SALA] Finalización solicitada | sala=%s | recurso=%s | "
            "tipo_inicial=%s | documentos=%d | pdfs=%d",
            sala_id,
            sala.contexto_destino.recurso_id,
            sala.documentos[0].tipo_archivo if sala.documentos else "sin_documentos",
            len(sala.documentos),
            self._contar_lotes_pdf(sala),
        )
        sala.iniciar_finalizacion()
        self.repository.guardar(sala)
        await self.connection_manager.broadcast(sala_id, self._estado_evento(sala, "Procesando documentos"))

        archivos = [self.temporary_storage.desde_documento(documento) for documento in sala.documentos]
        try:
            archivos_para_entrega: list[ArchivoTemporal] = []
            lotes_pdf: dict[str, list[ArchivoTemporal]] = {}
            nombres_pdf: dict[str, str | None] = {}

            for documento, archivo in zip(sala.documentos, archivos):
                if documento.tipo_archivo == "pdf":
                    lote_id = documento.lote_pdf_id or "lote_principal"
                    lotes_pdf.setdefault(lote_id, []).append(archivo)
                    nombres_pdf.setdefault(lote_id, documento.nombre_pdf)
                else:
                    archivos_para_entrega.append(archivo)

            nombres_usados: set[str] = set()
            if lotes_pdf:
                logger.info(
                    "[PDF] Lotes detectados | sala=%s | cantidad_pdf=%d | "
                    "paginas_totales=%d | detalle=%s",
                    sala_id,
                    len(lotes_pdf),
                    sum(len(paginas) for paginas in lotes_pdf.values()),
                    "; ".join(
                        f"{lote_id}:paginas={len(paginas)}:nombre={nombres_pdf.get(lote_id) or '-'}"
                        for lote_id, paginas in lotes_pdf.items()
                    ),
                )

            for indice_pdf, (lote_id, paginas) in enumerate(lotes_pdf.items(), start=1):
                inicio_pdf = time.perf_counter()
                nombre_pdf = self._nombre_pdf_unico(
                    nombres_pdf.get(lote_id), sala.sala_id, lote_id, nombres_usados
                )
                logger.info(
                    "[PDF] Generación iniciada | sala=%s | lote=%s | "
                    "pdf_numero=%d/%d | paginas=%d | nombre=%s",
                    sala_id,
                    lote_id,
                    indice_pdf,
                    len(lotes_pdf),
                    len(paginas),
                    nombre_pdf,
                )
                archivo_pdf = self.pdf_creator.crear_pdf(
                    sala.sala_id,
                    paginas,
                    lote_pdf_id=lote_id,
                    nombre_pdf=nombre_pdf,
                )
                archivos_para_entrega.append(archivo_pdf)
                logger.info(
                    "[PDF] Generación completada | sala=%s | lote=%s | "
                    "nombre=%s | paginas=%d | bytes=%d | ruta=%s | duracion_ms=%.0f",
                    sala_id,
                    lote_id,
                    archivo_pdf.nombre_original,
                    len(paginas),
                    archivo_pdf.peso_bytes,
                    archivo_pdf.ruta,
                    (time.perf_counter() - inicio_pdf) * 1000,
                )

            cantidad_imagenes = sum(
                1 for documento in sala.documentos if documento.tipo_archivo == "imagen"
            )
            logger.info(
                "[SALA] Archivos preparados | sala=%s | documentos_recibidos=%d | "
                "imagenes=%d | pdfs=%d | archivos_entrega=%d",
                sala_id,
                len(sala.documentos),
                cantidad_imagenes,
                len(lotes_pdf),
                len(archivos_para_entrega),
            )

            logger.info(
                "[SALA] Entrega al destino iniciada | sala=%s | destino=%s/%s | "
                "recurso=%s | archivos=%s",
                sala_id,
                sala.contexto_destino.sistema,
                sala.contexto_destino.modulo,
                sala.contexto_destino.recurso_id,
                ",".join(archivo.nombre_original for archivo in archivos_para_entrega),
            )

            resultado = await self.destination_adapter.entregar(sala, archivos_para_entrega)
            sala.completar()
            self.repository.guardar(sala)
            logger.info(
                "[SALA] Finalización completada | sala=%s | mensaje=%s",
                sala_id,
                resultado.mensaje,
            )
            await self.connection_manager.broadcast(sala_id, self._estado_evento(sala, resultado.mensaje))
            self.temporary_storage.eliminar_sala(sala_id)
            return {
                "sala_id": sala.sala_id,
                "estado": sala.estado.value,
                "mensaje": resultado.mensaje,
                "cantidad_documentos": len(sala.documentos),
                "cantidad_imagenes": sum(
                    1 for documento in sala.documentos if documento.tipo_archivo == "imagen"
                ),
                "cantidad_pdf": len(lotes_pdf),
                "resultado_destino": resultado.datos,
            }
        except Exception as error:
            sala.marcar_error(str(error))
            self.repository.guardar(sala)
            logger.exception(
                "[SALA] Finalización fallida | sala=%s | estado=%s | "
                "tipo_error=%s | detalle=%s",
                sala_id,
                sala.estado.value,
                type(error).__name__,
                error,
            )
            await self.connection_manager.broadcast(sala_id, self._estado_evento(sala, str(error)))
            raise

    async def cerrar_sala(self, sala_id: str, token: str) -> dict[str, Any]:
        sala = self.obtener_sala(sala_id)
        rol = sala.validar_token(token)
        if rol not in {"observador", "movil"}:
            raise AccesoSalaInvalidoError("El token no permite cerrar la sala")

        if sala.estado in {
            EstadoSala.ABIERTA,
            EstadoSala.CONECTADA,
            EstadoSala.RECIBIENDO,
        }:
            sala.cerrar()
            self.repository.guardar(sala)
            logger.info(
                "[SALA] Sala cerrada | sala=%s | rol=%s | documentos=%d | pdfs=%d",
                sala_id,
                rol,
                len(sala.documentos),
                self._contar_lotes_pdf(sala),
            )
            await self.connection_manager.broadcast(
                sala_id,
                self._estado_evento(sala, "La sala fue cerrada desde el navegador"),
            )

        return self._sala_respuesta(sala)

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
            "cantidad_documentos": len(sala.documentos),
            "cantidad_pdf": GestionSalas._contar_lotes_pdf(sala),
        }

    @staticmethod
    def _normalizar_lote_pdf_id(valor: str | None) -> str:
        lote_id = (valor or "").strip() or "lote_principal"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", lote_id):
            raise DocumentoInvalidoError("El identificador del PDF no es válido")
        return lote_id

    @staticmethod
    def _contar_lotes_pdf(sala: Sala) -> int:
        return len(
            {
                documento.lote_pdf_id or "lote_principal"
                for documento in sala.documentos
                if documento.tipo_archivo == "pdf"
            }
        )

    @staticmethod
    def _normalizar_nombre_pdf(valor: str | None) -> str | None:
        if not valor or not valor.strip():
            return None

        nombre = valor.strip().replace("\\", "/").rsplit("/", 1)[-1]
        if nombre.lower().endswith(".pdf"):
            nombre = nombre[:-4]
        nombre = re.sub(r"[^\w -]", "_", nombre, flags=re.UNICODE)
        nombre = re.sub(r"\s+", "_", nombre).strip("._-")
        if not nombre:
            raise DocumentoInvalidoError("El nombre del PDF no es válido")
        return f"{nombre[:100]}.pdf"

    @classmethod
    def _nombre_pdf_unico(
        cls,
        nombre_pdf: str | None,
        sala_id: str,
        lote_id: str,
        nombres_usados: set[str],
    ) -> str:
        nombre_base = cls._normalizar_nombre_pdf(nombre_pdf)
        if nombre_base is None:
            nombre_base = cls._normalizar_nombre_pdf(
                f"documento_{sala_id}_{lote_id}.pdf"
            )
        assert nombre_base is not None

        nombre_sin_extension = nombre_base[:-4]
        candidato = nombre_base
        contador = 2
        while candidato.casefold() in nombres_usados:
            candidato = f"{nombre_sin_extension}_{contador}.pdf"
            contador += 1
        nombres_usados.add(candidato.casefold())
        return candidato

    @staticmethod
    def _documento_respuesta(documento: DocumentoRecibido, sala: Sala) -> dict[str, Any]:
        return {
            "documento_id": documento.documento_id,
            "nombre": documento.nombre_personalizado or documento.nombre_original,
            "tipo_mime": documento.tipo_mime,
            "tipo_archivo": documento.tipo_archivo,
            "peso_bytes": documento.peso_bytes,
            "cantidad_documentos": len(sala.documentos),
            "cantidad_pdf": GestionSalas._contar_lotes_pdf(sala),
            "estado": sala.estado.value,
            "lote_pdf_id": documento.lote_pdf_id,
            "nombre_pdf": documento.nombre_pdf,
        }

    @staticmethod
    def _parsear_poligono(valor: str | None) -> list[dict[str, float]]:
        if not valor:
            raise DocumentoInvalidoError("El recorte de la página es obligatorio")
        try:
            puntos = json.loads(valor)
        except json.JSONDecodeError as error:
            raise DocumentoInvalidoError("El recorte de la página no es válido") from error

        if not isinstance(puntos, list) or len(puntos) != 4:
            raise DocumentoInvalidoError("El recorte debe contener cuatro puntos")

        poligono: list[dict[str, float]] = []
        for punto in puntos:
            if not isinstance(punto, dict) or "x" not in punto or "y" not in punto:
                raise DocumentoInvalidoError("Cada punto del recorte debe tener x e y")
            try:
                x = float(punto["x"])
                y = float(punto["y"])
            except (TypeError, ValueError) as error:
                raise DocumentoInvalidoError("Las coordenadas del recorte no son válidas") from error
            if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= 1) or not (0 <= y <= 1):
                raise DocumentoInvalidoError("Las coordenadas del recorte deben estar entre 0 y 1")
            poligono.append({"x": x, "y": y})

        return poligono

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
            "cantidad_pdf": GestionSalas._contar_lotes_pdf(sala),
            "mensaje_error": sala.mensaje_error,
        }
