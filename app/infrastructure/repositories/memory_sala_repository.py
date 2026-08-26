from __future__ import annotations

from threading import Lock

from app.domain.entities.sala import Sala


class MemorySalaRepository:
    """Repositorio de desarrollo; se reemplazará por Redis/PostgreSQL al escalar."""

    def __init__(self) -> None:
        self._salas: dict[str, Sala] = {}
        self._lock = Lock()

    def guardar(self, sala: Sala) -> Sala:
        with self._lock:
            self._salas[sala.sala_id] = sala
        return sala

    def obtener(self, sala_id: str) -> Sala | None:
        with self._lock:
            return self._salas.get(sala_id)

    def buscar_por_codigo(self, codigo: str) -> Sala | None:
        with self._lock:
            return next((sala for sala in self._salas.values() if sala.codigo == codigo), None)

