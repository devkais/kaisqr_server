from __future__ import annotations

from typing import Protocol

from app.domain.entities.sala import Sala
from app.infrastructure.destinations.destination_result import EntregaResultado
from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal


class DestinationAdapter(Protocol):
    sistema: str

    async def entregar(
        self,
        sala: Sala,
        archivos: list[ArchivoTemporal],
    ) -> EntregaResultado: ...
