"""Render a color swatch image for the color question: the model's required
colors and the AMS slots as real color chips with labels. Returns a base64 PNG
ready for Signal's ``base64_attachments`` (None if Pillow is unavailable or
rendering fails — the text question still carries color names as a fallback)."""
import base64
import io
import logging

import colors

log = logging.getLogger("bambu-bot")

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - Pillow always present in the image
    Image = None


_W = 540
_PAD = 20
_ROW = 46          # row height
_SW = 34           # swatch square size
_HEADER = 34       # section header height
_BG = (250, 250, 250)
_FG = (30, 30, 30)
_MUTED = (110, 110, 110)


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size)  # Pillow >= 10
    except TypeError:
        return ImageFont.load_default()


def _swatch_rgb(hex6):
    h = (hex6 or "").lstrip("#")[:6]
    if len(h) != 6:
        return (200, 200, 200)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return (200, 200, 200)


def build(name, required, ams):
    """Required colors + AMS slots as a labeled swatch PNG (base64) or None."""
    if Image is None:
        return None
    try:
        rows = len(required) + len(ams)
        height = _PAD * 2 + _HEADER * 2 + rows * _ROW + 30
        img = Image.new("RGB", (_W, height), _BG)
        d = ImageDraw.Draw(img)
        f_title = _font(22, bold=True)
        f_head = _font(18, bold=True)
        f_row = _font(18)

        y = _PAD
        title = name if len(name) <= 38 else name[:37] + "…"
        d.text((_PAD, y), title, font=f_title, fill=_FG)
        y += 30

        def section(label, items, fmt):
            nonlocal y
            d.text((_PAD, y), label, font=f_head, fill=_MUTED)
            y += _HEADER
            for it in items:
                rgb = _swatch_rgb(it["color"])
                d.rectangle(
                    [_PAD, y, _PAD + _SW, y + _SW],
                    fill=rgb, outline=(150, 150, 150), width=1,
                )
                cname = colors.color_name(it["color"])
                d.text((_PAD + _SW + 14, y + 6), fmt(it, cname), font=f_row, fill=_FG)
                y += _ROW

        section(
            f"Modell braucht {len(required)} Farbe(n):",
            required,
            lambda c, n: f"Farbe {c['index'] + 1}:  {c['type']} {n}".rstrip(),
        )
        section(
            "AMS Slots:",
            ams,
            lambda a, n: (f"{a['slot']})  {a['type']} {n}".rstrip()
                          + (f"  {a['sub']}" if a.get("sub") else "")),
        )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        log.warning("swatch render failed", exc_info=True)
        return None
