"""Make the symbolic icons of the program from their drawings (packaging/icons/*.svg): the device
kinds, the pages of the side panel and the tiles of an activity.

Each stroke of a drawing (stroke="currentColor") becomes the filled outline of the same line
(its width, round caps and joins), the drawing unchanged, written to
openexpress/data/<app ID>-<name>-symbolic.svg: GTK draws a symbolic icon as a mask and fills
every shape of it, so a line drawing would come out as solid shapes.

A drawing may also have, on any element:
- stroke-width: its own line width (else the drawing's);
- fill="currentColor": the shape filled (with stroke="none" only filled);
- data-gap="N": room of N around it, cut from what is drawn before it (a badge over a shape);
- data-cut="hole": the shape cut out of the whole icon (the eyes of a filled shape).
Paths take absolute M, L, A (circular), C and Z commands, several lines in one path.

Run again after a drawing changes; it needs shapely, best in a throwaway venv:
    python3 -m venv /tmp/oe-icons && /tmp/oe-icons/bin/pip install shapely
    /tmp/oe-icons/bin/python tools/symbolic-icons.py
"""

import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely.geometry import LinearRing, LineString, Point, Polygon, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "packaging" / "icons"
TARGET = ROOT / "openexpress" / "data"
APP_ID = "io.github.tomaszbojanowski.OpenExpress"
NS = "{http://www.w3.org/2000/svg}"
ARC_STEP = math.radians(2)
CURVE_STEPS = 24
ROUND = {"cap_style": "round", "join_style": "round", "quad_segs": 16}
SHAPE_KEYS = ("cx", "cy", "r", "x", "y", "width", "height", "rx")
EXTRAS = ("stroke-width", "fill", "stroke", "data-gap", "data-cut")


def _numbers(text: str) -> list[float]:
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", text)]


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


def _curve(p0, p1, p2, p3) -> list[tuple[float, float]]:
    """Points along a cubic Bézier curve, its start left out."""
    points = []
    for i in range(1, CURVE_STEPS + 1):
        t = i / CURVE_STEPS
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
        points.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                       a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return points


def _path(d: str) -> list[tuple[list[tuple[float, float]], bool]]:
    """The lines of a path of absolute M, L, A, C and Z commands: their points and whether
    each is closed."""
    if re.search(r"[^MLACZ\d\s,.\-eE]", d):
        raise ValueError(f"unsupported path: {d}")
    lines: list[tuple[list[tuple[float, float]], bool]] = []
    points: list[tuple[float, float]] = []
    for command, args in re.findall(r"([MLACZ])([^MLACZ]*)", d.strip()):
        n = _numbers(args)
        if command == "M":
            if points:
                lines.append((points, False))
            points = list(zip(n[0::2], n[1::2], strict=True))
        elif command == "L":
            points += list(zip(n[0::2], n[1::2], strict=True))
        elif command == "A":
            for i in range(0, len(n), 7):
                radius, _ry, _rotation, large, sweep, x, y = n[i:i + 7]
                points += _arc(points[-1], radius, int(large), int(sweep), (x, y))
        elif command == "C":
            for i in range(0, len(n), 6):
                x1, y1, x2, y2, x, y = n[i:i + 6]
                points += _curve(points[-1], (x1, y1), (x2, y2), (x, y))
        else:  # Z
            lines.append((points, True))
            points = []
    if points:
        lines.append((points, False))
    return lines


def _line(element):
    """The centre line of a shape: a ring for circles and rectangles, else a line."""
    tag = element.tag.removeprefix(NS)
    a = {k: float(v) for k, v in element.attrib.items() if k in SHAPE_KEYS}
    if tag == "circle":
        return Point(a["cx"], a["cy"]).buffer(a["r"], quad_segs=64).exterior
    if tag == "rect":
        x, y, w, h, rx = a["x"], a["y"], a["width"], a["height"], a.get("rx", 0)
        return LinearRing(box(x + rx, y + rx, x + w - rx, y + h - rx)
                          .buffer(rx, quad_segs=32).exterior.coords)
    if tag == "path":
        lines = _path(element.attrib["d"])
        if len(lines) == 1 and not lines[0][1]:
            return LineString(lines[0][0])
        return [LinearRing(p) if closed else LineString(p) for p, closed in lines]
    raise ValueError(f"unsupported element: {tag}")


def _area(element):
    """The inside of a shape, for a filled one."""
    line = _line(element)
    rings = line if isinstance(line, list) else [line]
    return unary_union([Polygon(r.coords).buffer(0) for r in rings])


def _shape(element, half):
    """What an element draws: its line of its own width or the drawing's, its inside when it is
    filled."""
    parts = []
    if element.get("stroke") != "none":
        width = element.get("stroke-width")
        line = _line(element)
        for each in line if isinstance(line, list) else [line]:
            parts.append(each.buffer(float(width) / 2 if width else half, **ROUND))
    if element.get("fill") == "currentColor":
        parts.append(_area(element))
    return unary_union(parts)


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
    elements = list(root)
    if not any(k in e.attrib for e in elements for k in EXTRAS):
        # Lines only, of the drawing's width.
        lines = [_line(e) for e in elements]
        shape = unary_union([each.buffer(half, **ROUND) for line in lines
                             for each in (line if isinstance(line, list) else [line])])
    else:
        shape = Polygon()
        holes = []
        for e in elements:
            if e.get("data-cut") == "hole":
                holes.append(_area(e))
                continue
            drawn = _shape(e, half)
            if e.get("data-gap"):
                shape = shape.difference(drawn.buffer(float(e.get("data-gap")), **ROUND))
            shape = unary_union([shape, drawn])
        if holes:
            shape = shape.difference(unary_union(holes))
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
