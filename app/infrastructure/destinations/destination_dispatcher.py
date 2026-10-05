from __future__ import annotations

from app.domain.entities.sala import Sala
from app.domain.exceptions import DestinoNoConfiguradoError
from app.infrastructure.destinations.destination_adapter import DestinationAdapter
from app.infrastructure.destinations.destination_result import EntregaResultado
from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal


class DestinationDispatcher:
    def __init__(self, adapters: list[DestinationAdapter]) -> None:
        self._adapters = {adapter.sistema: adapter for adapter in adapters}

    async def entregar(
        self,
        sala: Sala,
        archivos: list[ArchivoTemporal],
    ) -> EntregaResultado:
        adapter = self._adapters.get(sala.contexto_destino.sistema)
        if adapter is None:
            raise DestinoNoConfiguradoError(
                "No existe un adaptador para "
                f"{sala.contexto_destino.sistema}/{sala.contexto_destino.modulo}"
            )

        return await adapter.entregar(sala, archivos)
