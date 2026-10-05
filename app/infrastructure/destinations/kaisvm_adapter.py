from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.domain.entities.sala import Sala
from app.infrastructure.destinations.destination_result import EntregaResultado
from app.domain.exceptions import DestinoNoConfiguradoError, EntregaDestinoError
from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal


logger = logging.getLogger(__name__)


class KaisvmAdapter:
    sistema = "kaisvm"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def entregar(self, sala: Sala, archivos: list[ArchivoTemporal]) -> EntregaResultado:
        contexto = sala.contexto_destino
        if (
            contexto.sistema != "kaisvm"
            or contexto.modulo not in {"grupos", "gastos"}
            or contexto.recurso_id is None
        ):
            raise DestinoNoConfiguradoError(
                f"No existe un adaptador para {contexto.sistema}/{contexto.modulo}"
            )

        if not self.settings.kaisvm_forward_enabled:
            return EntregaResultado(
                mensaje="Documentos recibidos. El reenvío a kaisvm está desactivado en desarrollo.",
                datos={"reenviado": False, "cantidad": len(archivos)},
            )

        modulo_url = "grupos" if contexto.modulo == "grupos" else "gastos"
        url = (
            f"{self.settings.kaisvm_base_url.rstrip('/')}/{modulo_url}/"
            f"{contexto.recurso_id}/archivos"
        )
        headers: dict[str, str] = {}
        service_token = self.settings.kaisvm_service_token.strip()
        if service_token:
            if service_token.lower().startswith("bearer "):
                service_token = service_token[7:].strip()
            headers["Authorization"] = f"Bearer {service_token}"
        headers["Connection"] = "close"

        respuestas: list[dict[str, Any]] = []
        try:
            timeout = httpx.Timeout(connect=15, read=120, write=120, pool=15)
            logger.info(
                "[KAISVM] Entrega iniciada | sala=%s | recurso=%s | url=%s | "
                "archivos=%d | timeout_connect=15s | timeout_read=120s",
                sala.sala_id,
                contexto.recurso_id,
                url,
                len(archivos),
            )
            for archivo in archivos:
                ruta = archivo.ruta.resolve()
                if not ruta.is_file():
                    logger.error(
                        "[KAISVM] Archivo temporal no encontrado | sala=%s | "
                        "archivo=%s | ruta=%s",
                        sala.sala_id,
                        archivo.nombre_original,
                        ruta,
                    )
                    raise EntregaDestinoError(
                        f"No se encontró el archivo temporal {archivo.documento_id}"
                    )

                logger.info(
                    "[KAISVM] Archivo preparado | sala=%s | nombre=%s | "
                    "mime=%s | bytes=%d | ruta=%s",
                    sala.sala_id,
                    archivo.nombre_original,
                    archivo.tipo_mime,
                    archivo.peso_bytes,
                    ruta,
                )
                response: httpx.Response | None = None
                for intento in range(3):
                    inicio_intento = time.perf_counter()
                    logger.info(
                        "[KAISVM] POST iniciado | sala=%s | archivo=%s | "
                        "intento=%d/3",
                        sala.sala_id,
                        archivo.nombre_original,
                        intento + 1,
                    )
                    try:
                        # Un cliente nuevo por intento evita reutilizar un socket
                        # que el backend propietario pudo haber cerrado.
                        async with httpx.AsyncClient(
                            timeout=timeout,
                            trust_env=False,
                        ) as client:
                            with ruta.open("rb") as manejador:
                                files = {
                                    "archivos": (
                                        archivo.nombre_original,
                                        manejador,
                                        archivo.tipo_mime,
                                    )
                                }
                                data = {
                                    "nombres_personalizados": json.dumps(
                                        [archivo.nombre_original]
                                    ),
                                    "sala_id": sala.sala_id,
                                    "lote_pdf_id": archivo.lote_pdf_id or "",
                                }
                                response = await client.post(
                                    url,
                                    headers=headers,
                                    data=data,
                                    files=files,
                                )
                        logger.info(
                            "[KAISVM] Respuesta recibida | sala=%s | archivo=%s | "
                            "intento=%d/3 | status=%d | bytes_respuesta=%d | "
                            "duracion_ms=%.0f",
                            sala.sala_id,
                            archivo.nombre_original,
                            intento + 1,
                            response.status_code,
                            len(response.content),
                            (time.perf_counter() - inicio_intento) * 1000,
                        )
                        break
                    except (
                        httpx.ReadError,
                        httpx.ConnectError,
                        httpx.RemoteProtocolError,
                    ) as error:
                        espera = 0.5 * (2**intento)
                        detalle_error = str(error).strip() or (
                            "el servidor cerró la conexión antes de responder"
                        )
                        logger.warning(
                            "[KAISVM] Conexión fallida | sala=%s | archivo=%s | "
                            "intento=%d/3 | tipo=%s | duracion_ms=%.0f | detalle=%s",
                            sala.sala_id,
                            archivo.nombre_original,
                            intento + 1,
                            type(error).__name__,
                            (time.perf_counter() - inicio_intento) * 1000,
                            detalle_error,
                        )
                        if intento == 2:
                            logger.error(
                                "[KAISVM] Se agotaron los reintentos | sala=%s | "
                                "archivo=%s | intentos=3",
                                sala.sala_id,
                                archivo.nombre_original,
                            )
                            raise
                        logger.info(
                            "[KAISVM] Esperando antes de reintentar | sala=%s | "
                            "archivo=%s | espera_s=%.1f",
                            sala.sala_id,
                            archivo.nombre_original,
                            espera,
                        )
                        await asyncio.sleep(espera)

                if response is None:
                    raise EntregaDestinoError(
                        f"No se obtuvo respuesta para {archivo.nombre_original}"
                    )

                if response.is_error:
                    logger.error(
                        "[KAISVM] Backend rechazó archivo | sala=%s | archivo=%s | "
                        "status=%d | respuesta=%s",
                        sala.sala_id,
                        archivo.nombre_original,
                        response.status_code,
                        response.text[:500],
                    )
                    raise EntregaDestinoError(
                        f"kaisvm rechazó {archivo.nombre_original} "
                        f"({response.status_code}): {response.text[:300]}"
                    )

                try:
                    respuesta = response.json()
                except ValueError:
                    respuesta = {"respuesta": response.text[:300]}

                datos_archivo = (
                    respuesta
                    if isinstance(respuesta, dict)
                    else {"respuesta": respuesta}
                )
                respuestas.append(
                    {
                        "archivo": archivo.nombre_original,
                        "respuesta": datos_archivo,
                    }
                )
                logger.info(
                    "[KAISVM] Archivo entregado correctamente | sala=%s | "
                    "archivo=%s | total_entregados=%d",
                    sala.sala_id,
                    archivo.nombre_original,
                    len(respuestas),
                )
        except EntregaDestinoError:
            raise
        except (OSError, httpx.HTTPError) as error:
            detalle_error = str(error).strip() or "sin detalle"
            logger.exception(
                "[KAISVM] Entrega fallida por error de transporte | sala=%s | "
                "tipo=%s | detalle=%s",
                sala.sala_id,
                type(error).__name__,
                detalle_error,
            )
            raise EntregaDestinoError(
                "No fue posible comunicarse con kaisvm: "
                f"{type(error).__name__} - {detalle_error}"
            ) from error
        except Exception as error:
            logger.exception(
                "[KAISVM] Error inesperado durante la entrega | sala=%s | "
                "tipo=%s | detalle=%s",
                sala.sala_id,
                type(error).__name__,
                error,
            )
            raise EntregaDestinoError(
                f"Error preparando la entrega a kaisvm: {error}"
            ) from error

        return EntregaResultado(
            mensaje="Documentos reenviados correctamente",
            datos={
                "reenviado": True,
                "cantidad": len(respuestas),
                "respuestas": respuestas,
            },
        )
