import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import stl  # noqa: E402
import config  # noqa: E402


def _binary_stl(tris):
    out = b"\x00" * 80 + struct.pack("<I", len(tris))
    for tri in tris:
        out += struct.pack("<3f", 0, 0, 1)  # normal
        for vx in tri:
            out += struct.pack("<3f", *vx)
        out += b"\x00\x00"
    return out


def _bbox_binary(data):
    n = struct.unpack_from("<I", data, 80)[0]
    xs = []; ys = []; zs = []
    for i in range(n):
        for v in range(3):
            x, y, z = struct.unpack_from("<3f", data, 84 + i * 50 + 12 + v * 12)
            xs.append(x); ys.append(y); zs.append(z)
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def test_binary_stl_centered_and_dropped():
    # a triangle centered on origin, half below the bed (z -2..2)
    data = _binary_stl([[(-8, -5, -2), (8, 5, 2), (0, 5, 2)]])
    out = stl.arrange(data)
    (xmn, xmx), (ymn, ymx), (zmn, zmx) = _bbox_binary(out)
    c = config.BED_SIZE_MM / 2
    assert abs((xmn + xmx) / 2 - c) < 1e-3   # centered X
    assert abs((ymn + ymx) / 2 - c) < 1e-3   # centered Y
    assert abs(zmn) < 1e-3                    # bottom on the bed


def test_ascii_stl_centered():
    ascii_stl = (
        "solid t\nfacet normal 0 0 1\nouter loop\n"
        "vertex -8 -5 -2\nvertex 8 5 2\nvertex 0 5 2\n"
        "endloop\nendfacet\nendsolid t\n"
    ).encode()
    out = stl.arrange(ascii_stl).decode()
    import re
    vs = [(float(a), float(b), float(c)) for a, b, c in
          re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", out)]
    c = config.BED_SIZE_MM / 2
    xs = [v[0] for v in vs]; zs = [v[2] for v in vs]
    assert abs((min(xs) + max(xs)) / 2 - c) < 1e-3
    assert abs(min(zs)) < 1e-3


def test_arrange_passthrough_on_garbage():
    assert stl.arrange(b"not an stl") == b"not an stl"
