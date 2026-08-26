from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, sala_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[sala_id].add(websocket)

    def disconnect(self, sala_id: str, websocket: WebSocket) -> None:
        conexiones = self._connections.get(sala_id)
        if not conexiones:
            return
        conexiones.discard(websocket)
        if not conexiones:
            self._connections.pop(sala_id, None)

    async def broadcast(self, sala_id: str, evento: dict[str, Any]) -> None:
        conexiones = list(self._connections.get(sala_id, set()))
        for websocket in conexiones:
            try:
                await websocket.send_json(evento)
            except Exception:
                self.disconnect(sala_id, websocket)

