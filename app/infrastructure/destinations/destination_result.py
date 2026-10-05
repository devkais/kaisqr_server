from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EntregaResultado:
    mensaje: str
    datos: dict[str, Any]
