from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from app.core.config import Settings
from app.domain.entities.sala import Sala
from app.domain.exceptions import DestinoNoConfiguradoError, EntregaDestinoError
from app.infrastructure.destinations.destination_result import EntregaResultado
from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal


logger = logging.getLogger(__name__)

_SUPPORTED_UPLOAD_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def _resolve_upload_mime_type(ruta: Path, declared_mime_type: str) -> str:
    """Use the file signature when possible instead of forwarding octet-stream."""
    with ruta.open("rb") as archivo:
        encabezado = archivo.read(12)

    if encabezado.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if encabezado.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if encabezado.startswith(b"RIFF") and encabezado[8:12] == b"WEBP":
        return "image/webp"
    if encabezado.startswith(b"%PDF-"):
        return "application/pdf"

    mime_type = declared_mime_type.split(";", 1)[0].strip().lower()
    if mime_type and mime_type != "application/octet-stream":
        return "image/jpeg" if mime_type == "image/jpg" else mime_type

    return _SUPPORTED_UPLOAD_MIME_TYPES.get(
        ruta.suffix.lower(), mime_type or "application/octet-stream"
    )


def _resolve_document_uuid(documento_id: str, carga_uuid: str) -> str:
    """Map internal document IDs to the UUID contract expected by Transporte."""
    try:
        return str(UUID(documento_id))
    except ValueError:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"kaisqr:transporte:{carga_uuid}:{documento_id}",
            )
        )


class TransporteAdapter:
    sistema = "transporte"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def entregar(
        self,
        sala: Sala,
        archivos: list[ArchivoTemporal],
    ) -> EntregaResultado:
        contexto = sala.contexto_destino
        carga_referencia = contexto.recurso_referencia
        if (
            contexto.modulo != "gastos"
            or not carga_referencia
            or contexto.recurso_id is not None
        ):
            raise DestinoNoConfiguradoError(
                "transporte/gastos requiere una referencia UUID de carga temporal"
            )

        if not self.settings.transporte_forward_enabled:
            raise DestinoNoConfiguradoError(
                "El reenvío al backend de transporte está desactivado"
            )

        service_key = self.settings.transporte_service_key.strip()
        if not service_key:
            raise DestinoNoConfiguradoError(
                "Falta configurar la credencial de servicio de transporte"
            )
        if not archivos:
            raise EntregaDestinoError(
                "La sala no contiene documentos listos para entregar a transporte"
            )

        carga_uuid = quote(carga_referencia, safe="")
        url = (
            f"{self.settings.transporte_base_url.rstrip('/')}/gastos/"
            f"integraciones/cargas-temporales/{carga_uuid}/documentos"
        )
        headers = {"X-KaisQr-Service-Key": service_key, "Connection": "close"}
        respuestas: list[dict[str, Any]] = []
        timeout = httpx.Timeout(connect=15, read=120, write=120, pool=15)

        logger.info(
            "[TRANSPORTE] Entrega iniciada | sala=%s | carga=%s | archivos=%d | url=%s",
            sala.sala_id,
            contexto.recurso_referencia,
            len(archivos),
            url,
        )

        try:
            for archivo in archivos:
                ruta = archivo.ruta.resolve()
                if not ruta.is_file():
                    raise EntregaDestinoError(
                        f"No se encontró el archivo temporal {archivo.documento_id}"
                    )

                documento_uuid = _resolve_document_uuid(
                    archivo.documento_id,
                    carga_referencia,
                )
                response: httpx.Response | None = None
                mime_type = _resolve_upload_mime_type(ruta, archivo.tipo_mime)
                logger.info(
                    "[TRANSPORTE] Archivo preparado | sala=%s | documento=%s | nombre=%s | "
                    "mime_origen=%s | mime_enviado=%s | bytes=%d",
                    sala.sala_id,
                    documento_uuid,
                    archivo.nombre_original,
                    archivo.tipo_mime,
                    mime_type,
                    archivo.peso_bytes,
                )
                for intento in range(3):
                    inicio = time.perf_counter()
                    try:
                        async with httpx.AsyncClient(
                            timeout=timeout,
                            trust_env=False,
                        ) as client:
                            with ruta.open("rb") as manejador:
                                response = await client.post(
                                    url,
                                    headers=headers,
                                    data={"documentoUuid": documento_uuid},
                                    files={
                                        "archivo": (
                                            archivo.nombre_original,
                                            manejador,
                                            mime_type,
                                        )
                                    },
                                )

                        if response.is_error:
                            logger.error(
                                "[TRANSPORTE] Backend rechazó archivo | sala=%s | "
                                "carga=%s | documento=%s | status=%d | respuesta=%s",
                                sala.sala_id,
                                contexto.recurso_referencia,
                                documento_uuid,
                                response.status_code,
                                response.text[:400],
                            )
                            raise EntregaDestinoError(
                                "transporte rechazó "
                                f"{archivo.nombre_original} ({response.status_code}): "
                                f"{response.text[:250]}"
                            )

                        logger.info(
                            "[TRANSPORTE] Archivo recibido | sala=%s | documento=%s | "
                            "status=%d | bytes=%d | duracion_ms=%.0f",
                            sala.sala_id,
                            documento_uuid,
                            response.status_code,
                            archivo.peso_bytes,
                            (time.perf_counter() - inicio) * 1000,
                        )
                        break
                    except EntregaDestinoError:
                        raise
                    except (
                        httpx.ReadError,
                        httpx.ConnectError,
                        httpx.RemoteProtocolError,
                    ) as error:
                        if intento == 2:
                            raise EntregaDestinoError(
                                "No fue posible comunicarse con transporte: "
                                f"{type(error).__name__} - {error}"
                            ) from error
                        espera = 0.5 * (2**intento)
                        logger.warning(
                            "[TRANSPORTE] Reintento de entrega | sala=%s | "
                            "documento=%s | intento=%d/3 | espera_s=%.1f | error=%s",
                            sala.sala_id,
                            documento_uuid,
                            intento + 1,
                            espera,
                            error,
                        )
                        await asyncio.sleep(espera)

                if response is None:
                    raise EntregaDestinoError(
                        f"No se obtuvo respuesta para {archivo.nombre_original}"
                    )

                try:
                    respuesta = response.json()
                except ValueError:
                    respuesta = {"respuesta": response.text[:250]}
                respuestas.append(
                    {
                        "documento": archivo.nombre_original,
                        "respuesta": respuesta,
                    }
                )

            async with httpx.AsyncClient(
                timeout=timeout,
                trust_env=False,
            ) as client:
                confirmacion = await client.post(
                    f"{url.rsplit('/', 1)[0]}/finalizar",
                    headers=headers,
                )

            if confirmacion.is_error:
                raise EntregaDestinoError(
                    "transporte no pudo confirmar la carga temporal "
                    f"({confirmacion.status_code}): {confirmacion.text[:250]}"
                )
        except EntregaDestinoError:
            raise
        except (OSError, httpx.HTTPError) as error:
            logger.exception(
                "[TRANSPORTE] Falló el envío | sala=%s | carga=%s | tipo=%s",
                sala.sala_id,
                contexto.recurso_referencia,
                type(error).__name__,
            )
            raise EntregaDestinoError(
                "No fue posible completar la entrega a transporte: "
                f"{type(error).__name__} - {error}"
            ) from error

        return EntregaResultado(
            mensaje="Documentos recibidos por transporte",
            datos={
                "reenviado": True,
                "carga_uuid": contexto.recurso_referencia,
                "cantidad": len(respuestas),
                "respuestas": respuestas,
            },
        )
