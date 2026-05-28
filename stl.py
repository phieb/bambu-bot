"""Arrange raw STL meshes onto the print bed before slicing.

The slicer sidecar does NOT auto-arrange: a raw STL is sliced at its own
coordinates. Many STLs are modeled centered on the origin (e.g. X/Y/Z around 0),
so half the part ends up below the plate and off the front-left corner → the
slicer drops it → an empty "successful" print. We translate each mesh so it sits
centered on the bed (X/Y) with its bottom on Z=0. Handles binary and ASCII STL;
returns the input unchanged if it can't be parsed (the empty-slice guard catches
anything that slips through)."""
import io
import logging
import re
import struct
import zipfile

import config

log = logging.getLogger("bambu-bot")


def _center():
    half = config.BED_SIZE_MM / 2.0
    return half, half


def _offset(xs, ys, zs):
    cx, cy = _center()
    dx = cx - (min(xs) + max(xs)) / 2.0
    dy = cy - (min(ys) + max(ys)) / 2.0
    dz = -min(zs)  # drop bottom onto the bed
    return dx, dy, dz


def _is_binary(data):
    if len(data) < 84:
        return False
    n = struct.unpack_from("<I", data, 80)[0]
    return len(data) == 84 + n * 50


def _arrange_binary(data):
    n = struct.unpack_from("<I", data, 80)[0]
    xs, ys, zs = [], [], []
    for i in range(n):
        for v in range(3):
            x, y, z = struct.unpack_from("<3f", data, 84 + i * 50 + 12 + v * 12)
            xs.append(x); ys.append(y); zs.append(z)
    if not xs:
        return data
    dx, dy, dz = _offset(xs, ys, zs)
    buf = bytearray(data)
    for i in range(n):
        for v in range(3):
            o = 84 + i * 50 + 12 + v * 12
            x, y, z = struct.unpack_from("<3f", buf, o)
            struct.pack_into("<3f", buf, o, x + dx, y + dy, z + dz)
    return bytes(buf)


_VTX = re.compile(r"(vertex\s+)(-?[\d.eE+]+)\s+(-?[\d.eE+]+)\s+(-?[\d.eE+]+)", re.I)


def _arrange_ascii(data):
    text = data.decode("utf-8", errors="ignore")
    verts = [(float(a), float(b), float(c)) for _, a, b, c in _VTX.findall(text)]
    if not verts:
        return data
    dx, dy, dz = _offset([v[0] for v in verts], [v[1] for v in verts], [v[2] for v in verts])

    def repl(m):
        x, y, z = float(m.group(2)) + dx, float(m.group(3)) + dy, float(m.group(4)) + dz
        return f"{m.group(1)}{x:.6f} {y:.6f} {z:.6f}"

    return _VTX.sub(repl, text).encode("utf-8")


def arrange(data):
    """Center an STL on the bed and drop it onto Z=0. Returns input unchanged on
    any failure (never raises)."""
    try:
        if _is_binary(data):
            return _arrange_binary(data)
        if data[:5].lstrip().lower().startswith(b"solid"):
            return _arrange_ascii(data)
    except Exception:
        log.warning("stl arrange failed; using original", exc_info=True)
    return data


def arrange_zip(data):
    """Return a copy of a zip with every .stl entry arranged; other entries
    (.3mf, …) pass through untouched."""
    try:
        src = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        log.warning("zip open failed; using original", exc_info=True)
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename.lower().endswith(".stl"):
                content = arrange(content)
            dst.writestr(info, content)
    return out.getvalue()
