from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from app.domain.exceptions import (
    AccesoSalaInvalidoError,
    LimiteDocumentosError,
    SalaNoDisponibleError,
)


class EstadoSala(StrEnum):
    ABIERTA = "abierta"
    CONECTADA = "conectada"
    RECIBIENDO = "recibiendo"
    FINALIZANDO = "finalizando"
    COMPLETADA = "completada"
    CERRADA = "cerrada"
    EXPIRADA = "expirada"
    ERROR = "error"


@dataclass(frozen=True)
class ContextoDestino:
    sistema: str
    modulo: str
    recurso_id: int
    operacion: str = "subir_archivos"


@dataclass
class DocumentoRecibido:
    documento_id: str
    nombre_original: str
    nombre_personalizado: str | None
    tipo_mime: str
    peso_bytes: int
    ruta_temporal: str
    tipo_archivo: str = "imagen"
    poligono: list[dict[str, float]] = field(default_factory=list)
    lote_pdf_id: str | None = None
    nombre_pdf: str | None = None
    recibido_en: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Sala:
    sala_id: str
    codigo: str
    contexto_destino: ContextoDestino
    tokens_invitacion_hash: set[str]
    token_observador_hash: str
    creada_en: datetime
    expira_en: datetime
    estado: EstadoSala = EstadoSala.ABIERTA
    movil_conectado: bool = False
    documentos: list[DocumentoRecibido] = field(default_factory=list)
    mensaje_error: str | None = None

    @staticmethod
    def generar_hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def esta_activa(self, ahora: datetime | None = None) -> bool:
        momento = ahora or datetime.now(timezone.utc)
        if momento >= self.expira_en and self.estado not in {
            EstadoSala.COMPLETADA,
            EstadoSala.CERRADA,
            EstadoSala.EXPIRADA,
        }:
            self.estado = EstadoSala.EXPIRADA
            return False

        return self.estado in {
            EstadoSala.ABIERTA,
            EstadoSala.CONECTADA,
            EstadoSala.RECIBIENDO,
        }

    def validar_token(self, token: str) -> str:
        if not token:
            raise AccesoSalaInvalidoError("El token de la sala es obligatorio")

        token_hash = self.generar_hash_token(token)
        if any(hmac.compare_digest(token_hash, token_hash_guardado) for token_hash_guardado in self.tokens_invitacion_hash):
            return "movil"
        if hmac.compare_digest(token_hash, self.token_observador_hash):
            return "observador"
        raise AccesoSalaInvalidoError("El token de la sala no es válido")

    def conectar_movil(self) -> None:
        if not self.esta_activa():
            raise SalaNoDisponibleError("La sala ya no está disponible")
        self.movil_conectado = True
        self.estado = EstadoSala.CONECTADA

    def registrar_documento(self, documento: DocumentoRecibido, limite: int) -> None:
        if not self.esta_activa():
            raise SalaNoDisponibleError("La sala ya no acepta documentos")
        if len(self.documentos) >= limite:
            raise LimiteDocumentosError("La sala alcanzó el límite de documentos")

        self.documentos.append(documento)
        self.estado = EstadoSala.RECIBIENDO

    def iniciar_finalizacion(self) -> None:
        if not self.documentos:
            raise SalaNoDisponibleError("La sala no tiene documentos para enviar")
        if not self.esta_activa():
            raise SalaNoDisponibleError("La sala ya no está disponible")
        self.estado = EstadoSala.FINALIZANDO

    def completar(self) -> None:
        self.estado = EstadoSala.COMPLETADA

    def cerrar(self) -> None:
        self.estado = EstadoSala.CERRADA
        self.movil_conectado = False

    def marcar_error(self, mensaje: str) -> None:
        self.estado = EstadoSala.ERROR
        self.mensaje_error = mensaje
