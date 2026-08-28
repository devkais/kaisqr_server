from __future__ import annotations

import math
import re
from io import BytesIO
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from app.infrastructure.storage.temporary_file_storage import ArchivoTemporal


class PdfCreator:
    """Construye un PDF multipágina a partir de fotografías recortadas."""

    _max_dimension_image_pdf = 2200
    _quality_image_pdf = 82

    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path

    def crear_pdf(
        self,
        sala_id: str,
        paginas: Sequence[ArchivoTemporal],
        lote_pdf_id: str | None = None,
        nombre_pdf: str | None = None,
    ) -> ArchivoTemporal:
        if not paginas:
            raise ValueError("No existen páginas para crear el PDF")

        carpeta_sala = self.root_path / sala_id
        carpeta_sala.mkdir(parents=True, exist_ok=True)
        sufijo_lote = ""
        if lote_pdf_id:
            lote_seguro = re.sub(r"[^a-zA-Z0-9_-]", "_", lote_pdf_id)
            sufijo_lote = f"_{lote_seguro}"
        nombre_pdf_final = self._normalizar_nombre_pdf(
            nombre_pdf or f"documento_{sala_id}{sufijo_lote}.pdf"
        )
        ruta_pdf = carpeta_sala / nombre_pdf_final
        pdf = Canvas(str(ruta_pdf))

        try:
            for pagina in paginas:
                with Image.open(pagina.ruta.resolve()) as original:
                    imagen = ImageOps.exif_transpose(original).convert("RGB")
                    try:
                        recortada = self._recortar_por_poligono(imagen, pagina.poligono)
                        try:
                            self._dibujar_pagina(pdf, recortada)
                        finally:
                            recortada.close()
                    finally:
                        imagen.close()
            pdf.save()
        except Exception:
            ruta_pdf.unlink(missing_ok=True)
            raise

        return ArchivoTemporal(
            documento_id=f"{sala_id}-pdf-{lote_pdf_id or 'principal'}",
            nombre_original=nombre_pdf_final,
            tipo_mime="application/pdf",
            peso_bytes=ruta_pdf.stat().st_size,
            ruta=ruta_pdf,
            tipo_archivo="pdf",
            lote_pdf_id=lote_pdf_id,
            nombre_pdf=nombre_pdf_final,
        )

    @staticmethod
    def _normalizar_nombre_pdf(nombre: str) -> str:
        nombre_base = Path(nombre).name
        if nombre_base.lower().endswith(".pdf"):
            nombre_base = nombre_base[:-4]
        nombre_base = re.sub(r"[^\w -]", "_", nombre_base, flags=re.UNICODE)
        nombre_base = re.sub(r"\s+", "_", nombre_base).strip("._-")
        if not nombre_base:
            nombre_base = "documento"
        return f"{nombre_base[:100]}.pdf"

    @staticmethod
    def _recortar_por_poligono(
        imagen: Image.Image,
        poligono: Sequence[dict[str, float]],
    ) -> Image.Image:
        if len(poligono) != 4:
            return imagen.copy()

        ancho, alto = imagen.size
        puntos = tuple(
            (
                float(punto["x"]) * ancho,
                float(punto["y"]) * alto,
            )
            for punto in poligono
        )
        if any(
            not math.isfinite(coordenada)
            for punto in puntos
            for coordenada in punto
        ):
            raise ValueError("El polígono contiene coordenadas no válidas")

        ancho_superior = math.dist(puntos[0], puntos[1])
        ancho_inferior = math.dist(puntos[3], puntos[2])
        alto_izquierdo = math.dist(puntos[0], puntos[3])
        alto_derecho = math.dist(puntos[1], puntos[2])
        ancho_salida = max(1, round(max(ancho_superior, ancho_inferior)))
        alto_salida = max(1, round(max(alto_izquierdo, alto_derecho)))
        # Flutter envía los puntos en orden horario: superior-izquierda,
        # superior-derecha, inferior-derecha, inferior-izquierda. Pillow
        # espera: superior-izquierda, inferior-izquierda, inferior-derecha,
        # superior-derecha.
        puntos_para_pillow = (puntos[0], puntos[3], puntos[2], puntos[1])
        datos_poligono = tuple(
            coordenada for punto in puntos_para_pillow for coordenada in punto
        )

        return imagen.transform(
            (ancho_salida, alto_salida),
            Image.Transform.QUAD,
            data=datos_poligono,
            resample=Image.Resampling.BICUBIC,
        )

    @staticmethod
    def _dibujar_pagina(pdf: Canvas, imagen: Image.Image) -> None:
        imagen_pdf = imagen
        imagen_reducida = False
        if max(imagen.size) > PdfCreator._max_dimension_image_pdf:
            imagen_pdf = imagen.copy()
            imagen_pdf.thumbnail(
                (
                    PdfCreator._max_dimension_image_pdf,
                    PdfCreator._max_dimension_image_pdf,
                ),
                Image.Resampling.LANCZOS,
            )
            imagen_reducida = True

        try:
            # ImageReader recibe una imagen PIL como datos RGB sin comprimir.
            # Codificarla previamente como JPEG evita que cada página ocupe
            # varios megabytes dentro del PDF.
            buffer = BytesIO()
            imagen_pdf.save(
                buffer,
                format="JPEG",
                quality=PdfCreator._quality_image_pdf,
                optimize=True,
                progressive=True,
            )
            buffer.seek(0)

            pagina = landscape(A4) if imagen_pdf.width > imagen_pdf.height else portrait(A4)
            pdf.setPageSize(pagina)
            ancho_pagina, alto_pagina = pagina
            margen = 18
            ancho_disponible = ancho_pagina - (margen * 2)
            alto_disponible = alto_pagina - (margen * 2)
            escala = min(
                ancho_disponible / imagen_pdf.width,
                alto_disponible / imagen_pdf.height,
            )
            ancho = imagen_pdf.width * escala
            alto = imagen_pdf.height * escala
            x = (ancho_pagina - ancho) / 2
            y = (alto_pagina - alto) / 2
            pdf.drawImage(
                ImageReader(buffer),
                x,
                y,
                width=ancho,
                height=alto,
                preserveAspectRatio=True,
                mask=None,
            )
            pdf.showPage()
        finally:
            if imagen_reducida:
                imagen_pdf.close()
