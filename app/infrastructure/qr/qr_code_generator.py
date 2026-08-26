from __future__ import annotations

import base64
from io import BytesIO

import qrcode
from qrcode.image.svg import SvgPathImage


class QrCodeGenerator:
    def generar_data_uri(self, contenido: str) -> str:
        qr = qrcode.QRCode(version=None, box_size=8, border=4)
        qr.add_data(contenido)
        qr.make(fit=True)
        imagen = qr.make_image(
            image_factory=SvgPathImage,
            fill_color="black",
            back_color="white",
        )

        buffer = BytesIO()
        imagen.save(buffer)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"
