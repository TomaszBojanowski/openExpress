"""Make the symbolic icons of the device kinds from their drawings (packaging/icons/*.svg).

Each stroke of a drawing (stroke="currentColor") becomes the filled outline of the same line
(its width, round caps and joins), the drawing unchanged, written to
openexpress/data/<app ID>-<kind>-symbolic.svg: GTK draws a symbolic icon as a mask and fills
every shape of it, so a line drawing would come out as solid shapes.

Run again after a drawing changes; it needs shapely, best in a throwaway venv:
    python3 -m venv /tmp/oe-icons && /tmp/oe-icons/bin/pip install shapely
    /tmp/oe-icons/bin/python tools/symbolic-icons.py
"""

import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely.geometry import LinearRing, LineString, Point, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "packaging" / "icons"
TARGET = ROOT / "openexpress" / "data"
APP_ID = "io.github.tomaszbojanowski.OpenExpress"
NS = "{http://www.w3.org/2000/svg}"
ARC_STEP = math.radians(2)
ROUND = {"cap_style": "round", "join_style": "round", "quad_segs": 16}


def _numbers(text: str) -> list[float]:
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _arc(start, radius, large, sweep, end) -> list[tuple[float, float]]:
    """Points along a circular arc of an SVG path (rx = ry, no rotation)."""
    (x0, y0), (x1, y1) = start, end
    hx, hy = (x0 - x1) / 2, (y0 - y1) / 2
    radius = max(radius, math.hypot(hx, hy))
    root = math.sqrt(max(radius * radius - hx * hx - hy * hy, 0) / (hx * hx + hy * hy))
    sign = -1 if large == sweep else 1
    cx, cy = sign * root * hy + (x0 + x1) / 2, -sign * root * hx + (y0 + y1) / 2
    a0, a1 = math.atan2(y0 - cy, x0 - cx), math.atan2(y1 - cy, x1 - cx)
    turn = a1 - a0
    if sweep and turn < 0:
        turn += 2 * math.pi
    elif not sweep and turn > 0:
        turn -= 2 * math.pi
    steps = max(2, math.ceil(abs(turn) / ARC_STEP))
    return [(cx + radius * math.cos(a0 + turn * i / steps),
             cy + radius * math.sin(a0 + turn * i / steps)) for i in range(1, steps + 1)]


def _path(d: str) -> list[tuple[float, float]]:
    """The points of a path of absolute M, L and A commands (all the drawings use)."""
    points: list[tuple[float, float]] = []
    for command, args in re.findall(r"([MLA])([^MLA]*)", d.strip()):
        n = _numbers(args)
        if command in "ML":
            points += list(zip(n[0::2], n[1::2], strict=True))
        else:
            for i in range(0, len(n), 7):
                radius, _ry, _rotation, large, sweep, x, y = n[i:i + 7]
                points += _arc(points[-1], radius, int(large), int(sweep), (x, y))
    if re.search(r"[^MLA\d\s,.\-]", d):
        raise ValueError(f"unsupported path: {d}")
    return points


def _line(element):
    """The centre line of a shape: a ring for circles and rectangles, else a line."""
    tag = element.tag.removeprefix(NS)
    a = {k: float(v) for k, v in element.attrib.items() if k != "d"}
    if tag == "circle":
        return Point(a["cx"], a["cy"]).buffer(a["r"], quad_segs=64).exterior
    if tag == "rect":
        x, y, w, h, rx = a["x"], a["y"], a["width"], a["height"], a.get("rx", 0)
        return LinearRing(box(x + rx, y + rx, x + w - rx, y + h - rx)
                          .buffer(rx, quad_segs=32).exterior.coords)
    if tag == "path":
        return LineString(_path(element.attrib["d"]))
    raise ValueError(f"unsupported element: {tag}")


def _d(geometry) -> str:
    rings = []
    for polygon in getattr(geometry, "geoms", [geometry]):
        for ring in (polygon.exterior, *polygon.interiors):
            xy = list(ring.coords)[:-1]
            rings.append("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in xy) + "Z")
    return "".join(rings)


def outline(source: Path) -> str:
    root = ET.parse(source).getroot()
    if root.get("stroke-linecap") != "round" or root.get("stroke-linejoin") != "round":
        raise ValueError(f"{source.name}: round caps and joins expected")
    half = float(root.get("stroke-width")) / 2
    shape = unary_union([_line(e).buffer(half, **ROUND) for e in root])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
            f'viewBox="{root.get("viewBox")}"><!-- made by tools/symbolic-icons.py from '
            f'packaging/icons/{source.name} --><path fill="#2e3436" fill-rule="evenodd" '
            f'd="{_d(shape)}"/></svg>\n')


def main() -> int:
    for source in sorted(SOURCES.glob("*.svg")):
        target = TARGET / f"{APP_ID}-{source.stem}-symbolic.svg"
        target.write_text(outline(source))
        print(f"{source.relative_to(ROOT)} -> {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
