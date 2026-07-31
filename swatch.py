"""Render a color swatch image for the color question: the model's required
colors and the AMS slots as real color chips with labels. Returns a base64 PNG
ready for Signal's ``base64_attachments`` (None if Pillow is unavailable or
rendering fails — the text question still carries color names as a fallback)."""
import base64
import io
import logging
from functools import lru_cache

import colors

log = logging.getLogger("bambu-bot")

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - Pillow always present in the image
    Image = None


_W = 540
_PAD = 20
_MARGIN = 22       # side breathing room, so a preview crop trims paper, not text
_BAND = 58         # colour band height
_HEADER = 40       # section header height (headline weight — a thin grey label
                   # disappeared between two saturated bands)
_KICKER = 34       # "FARBTAFEL" headline above the model name
_TITLE = 30
_SUB = 26          # explainer line under the title
_BG = (250, 250, 250)
_FG = (30, 30, 30)
_MUTED = (110, 110, 110)


@lru_cache(maxsize=8)
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


def _text_on(rgb):
    """Black or white label, whichever stays readable on ``rgb`` (perceived
    luminance, so a mid yellow gets dark text and a navy gets light text)."""
    r, g, b = rgb
    return _FG if (0.2126 * r + 0.7152 * g + 0.0722 * b) > 150 else (255, 255, 255)


def _fit(d, text, font, max_w):
    """``text`` truncated with an ellipsis until it fits ``max_w`` px."""
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _swatch_rgb(hex6):
    h = (hex6 or "").lstrip("#")[:6]
    if len(h) != 6:
        return (200, 200, 200)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return (200, 200, 200)


def shrink_image(data, max_px=512, quality=82):
    """Downscale an image (e.g. a MakerWorld cover) to fit ``max_px`` and
    re-encode as JPEG → base64, so attachments stay small and upload fast.
    Returns None if Pillow is missing or the bytes aren't a valid image."""
    if Image is None or not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        log.warning("thumbnail shrink failed", exc_info=True)
        return None


def numbered_thumbnail(data, n, max_px=512, quality=82):
    """Like ``shrink_image`` but stamps the selection number ``n`` in the top-left
    corner, so a multi-image plate gallery stays unambiguous. Falls back to a
    plain shrink if drawing fails."""
    if Image is None or not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((max_px, max_px))
        d = ImageDraw.Draw(img)
        label = str(n)
        f = _font(44, bold=True)
        bbox = d.textbbox((0, 0), label, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 10
        d.rectangle([6, 6, 6 + tw + pad * 2, 6 + th + pad * 2], fill=(20, 20, 20))
        d.text((6 + pad - bbox[0], 6 + pad - bbox[1]), label, font=f, fill=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        log.warning("numbered thumbnail failed", exc_info=True)
        return shrink_image(data, max_px, quality)


def build(name, required, ams, lang="de"):
    """Required colors + AMS slots as a labeled swatch PNG (base64) or None.

    Every colour is a wide band with its label written *on* it, rather than a
    small chip beside the text: Signal previews a multi-image message as one
    cropped thumbnail, and 34 px chips simply vanished at that size — the one
    picture whose whole job is showing colours showed none.

    The bands stop short of both edges (``_MARGIN``): a preview crop that trims
    the sides then eats blank paper instead of the labels."""
    if Image is None:
        return None
    en = lang == "en"
    try:
        rows = len(required) + len(ams)
        height = (_PAD * 2 + _KICKER + _TITLE + _SUB + _HEADER * 2 + rows * _BAND
                  + 12)  # 2 × section gap
        img = Image.new("RGB", (_W, height), _BG)
        d = ImageDraw.Draw(img)
        f_kicker = _font(28, bold=True)
        f_title = _font(19)
        f_sub = _font(15)
        f_head = _font(21, bold=True)
        f_row = _font(19, bold=True)
        text_x = _MARGIN + 16
        text_w = _W - 2 * text_x

        y = _PAD
        # Say what the picture is, then what it's for — on its own it arrives as
        # an unexplained block of colour. Kicker and name on separate lines: as
        # one line the prefix ate the model name down to "Plate 2 — Varoresso XRO…".
        d.text((_MARGIN, y), "COLOR SWATCHES" if en else "FARBTAFEL",
               font=f_kicker, fill=_FG)
        y += _KICKER
        d.text((_MARGIN, y), _fit(d, name, f_title, _W - 2 * _MARGIN),
               font=f_title, fill=_FG)
        y += _TITLE
        d.text((_MARGIN, y), ("Answer with the AMS slot number per model colour."
                              if en else
                              "Antworte mit der AMS-Slot-Nummer je Modellfarbe."),
               font=f_sub, fill=_MUTED)
        y += _SUB

        def section(label, items, fmt):
            nonlocal y
            d.text((_MARGIN, y + 8), label, font=f_head, fill=_FG)
            y += _HEADER
            for it in items:
                rgb = _swatch_rgb(it["color"])
                d.rectangle([_MARGIN, y, _W - _MARGIN, y + _BAND], fill=rgb)
                # Hairline between bands so two identical colours still read as
                # two slots.
                d.line([(_MARGIN, y), (_W - _MARGIN, y)],
                       fill=tuple(max(0, c - 25) for c in rgb))
                cname = colors.color_name(it["color"], lang)
                text = _fit(d, fmt(it, cname), f_row, text_w)
                bbox = d.textbbox((0, 0), text, font=f_row)
                d.text((text_x, y + (_BAND - (bbox[3] - bbox[1])) / 2 - bbox[1]),
                       text, font=f_row, fill=_text_on(rgb))
                y += _BAND
            y += 6

        color_word = "Color" if en else "Farbe"
        section(
            ("ORIGINAL MODEL COLORS" if en else "FARBEN DES MODELLS"),
            required,
            lambda c, n: f"{color_word} {c['index'] + 1}:  {c['type']} {n}".rstrip(),
        )
        section(
            ("AVAILABLE AMS COLORS" if en else "VERFÜGBAR IM AMS"),
            ams,
            # Same label as the message text (real spool name when assigned), so
            # picture and text can't disagree about what sits in a slot.
            lambda a, n: f"{a['slot']})  {colors.slot_label(a, lang)}",
        )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        log.warning("swatch render failed", exc_info=True)
        return None
