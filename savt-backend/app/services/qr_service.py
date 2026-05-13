import io
from pathlib import Path

import qrcode
from PIL import Image

LOGO_PATH = Path("/code/app/assets/savt_logo.png")

# Отступ вокруг логотипа (в пикселях)
_LOGO_PADDING = 0
# Логотип занимает 30% от размера QR-кода (максимум с ERROR_CORRECT_H ~30%, но современные сканеры читают до 35%)
_LOGO_RATIO = 0.30


def generate_qr(data: str) -> bytes:
    qr = qrcode.QRCode(
        # ERROR_CORRECT_H — 30% избыточность, позволяет перекрыть до 30% логотипом
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    if LOGO_PATH.exists():
        _overlay_logo(img)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _overlay_logo(qr_img: Image.Image) -> None:
    logo = Image.open(LOGO_PATH).convert("RGBA")

    qr_w, qr_h = qr_img.size
    logo_size = int(qr_w * _LOGO_RATIO)
    logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

    # Белый фон с отступом вокруг логотипа
    bg_size = logo_size + _LOGO_PADDING * 2
    bg = Image.new("RGBA", (bg_size, bg_size), (255, 255, 255, 255))
    bg.paste(logo, (_LOGO_PADDING, _LOGO_PADDING), mask=logo)

    pos = ((qr_w - bg_size) // 2, (qr_h - bg_size) // 2)
    qr_img.paste(bg, pos, mask=bg)
