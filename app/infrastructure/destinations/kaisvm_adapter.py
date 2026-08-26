from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.domain.entities.sala import Sala
from app.domain.exceptions import DestinoNoConfiguradoError, EntregaDestinoError
from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal


logger = logging.getLogger(__name__)


@dataclass
class EntregaResultado:
    mensaje: str
    datos: dict[str, Any]


class KaisvmAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def entregar(self, sala: Sala, archivos: list[ArchivoTemporal]) -> EntregaResultado:
        contexto = sala.contexto_destino
        if contexto.sistema != "kaisvm" or contexto.modulo != "grupos":
            raise DestinoNoConfiguradoError(
                f"No existe un adaptador para {contexto.sistema}/{contexto.modulo}"
            )

        if not self.settings.kaisvm_forward_enabled:
            return EntregaResultado(
                mensaje="Documentos recibidos. El reenvío a kaisvm está desactivado en desarrollo.",
                datos={"reenviado": False, "cantidad": len(archivos)},
            )

        url = (
            f"{self.settings.kaisvm_base_url.rstrip('/')}/grupos/"
            f"{contexto.recurso_id}/archivos"
        )
        headers: dict[str, str] = {}
        service_token = self.settings.kaisvm_service_token.strip()
        if service_token:
            if service_token.lower().startswith("bearer "):
                service_token = service_token[7:].strip()
            headers["Authorization"] = f"Bearer {service_token}"

        try:
            with ExitStack() as stack:
                files = []
                for archivo in archivos:
                    ruta = archivo.ruta.resolve()
                    if not ruta.is_file():
                        raise EntregaDestinoError(
                            f"No se encontró el archivo temporal {archivo.documento_id}"
                        )

                    manejador = stack.enter_context(ruta.open("rb"))
                    files.append(
                        (
                            "archivos",
                            (archivo.nombre_original, manejador, archivo.tipo_mime),
                        )
                    )

                data = {
                    "nombres_personalizados": json.dumps(
                        [archivo.nombre_original for archivo in archivos]
                    )
                }
                async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                    response = await client.post(url, headers=headers, data=data, files=files)
        except EntregaDestinoError:
            raise
        except (OSError, httpx.HTTPError) as error:
            logger.exception("No fue posible entregar documentos a kaisvm")
            raise EntregaDestinoError(
                f"No fue posible comunicarse con kaisvm: {error}"
            ) from error
        except Exception as error:
            logger.exception("Error inesperado durante la entrega a kaisvm")
            raise EntregaDestinoError(
                f"Error preparando la entrega a kaisvm: {error}"
            ) from error

        if response.is_error:
            raise EntregaDestinoError(
                f"kaisvm rechazó la entrega ({response.status_code}): {response.text[:300]}"
            )

        try:
            respuesta = response.json()
        except ValueError:
            respuesta = {"respuesta": response.text[:300]}

        datos = respuesta if isinstance(respuesta, dict) else {"respuesta": respuesta}

        return EntregaResultado(
            mensaje=datos.get("msg", "Documentos reenviados correctamente"),
            datos={"reenviado": True, "respuesta": datos},
        )
