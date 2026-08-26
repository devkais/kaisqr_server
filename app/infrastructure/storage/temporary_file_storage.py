from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.domain.entities.sala import DocumentoRecibido
from app.domain.exceptions import DocumentoInvalidoError


@dataclass
class ArchivoTemporal:
    documento_id: str
    nombre_original: str
    tipo_mime: str
    peso_bytes: int
    ruta: Path


class TemporaryFileStorage:
    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path
        self.root_path.mkdir(parents=True, exist_ok=True)

    async def guardar(self, sala_id: str, archivo: UploadFile, max_size_bytes: int) -> ArchivoTemporal:
        documento_id = str(uuid.uuid4())
        nombre_original = Path(archivo.filename or "documento").name
        carpeta_sala = self.root_path / sala_id
        carpeta_sala.mkdir(parents=True, exist_ok=True)
        ruta = carpeta_sala / documento_id
        peso_bytes = 0

        try:
            with ruta.open("wb") as destino:
                while bloque := await archivo.read(1024 * 1024):
                    peso_bytes += len(bloque)
                    if peso_bytes > max_size_bytes:
                        raise DocumentoInvalidoError(
                            f"El archivo supera el límite de {max_size_bytes // (1024 * 1024)} MB"
                        )
                    destino.write(bloque)
        except Exception:
            ruta.unlink(missing_ok=True)
            raise

        return ArchivoTemporal(
            documento_id=documento_id,
            nombre_original=nombre_original,
            tipo_mime=archivo.content_type or "application/octet-stream",
            peso_bytes=peso_bytes,
            ruta=ruta,
        )

    @staticmethod
    def desde_documento(documento: DocumentoRecibido) -> ArchivoTemporal:
        return ArchivoTemporal(
            documento_id=documento.documento_id,
            nombre_original=documento.nombre_original,
            tipo_mime=documento.tipo_mime,
            peso_bytes=documento.peso_bytes,
            ruta=Path(documento.ruta_temporal),
        )

    @staticmethod
    def eliminar_archivo(archivo: ArchivoTemporal) -> None:
        archivo.ruta.unlink(missing_ok=True)

    def eliminar_sala(self, sala_id: str) -> None:
        shutil.rmtree(self.root_path / sala_id, ignore_errors=True)

