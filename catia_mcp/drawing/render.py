"""Rendering and sketch overlay for vector PDF drawings (PyMuPDF, optional and lazy).

``render_view`` writes a PNG of a page or region (optional graduated grid in model mm).
``overlay`` draws CATIA sketch entities, in model mm, on top of the drawing so you can check
visually that the coloured sketch follows the black drawing lines.

Frames are those of :mod:`catia_mcp.drawing.geometry`: paper = mm from the top-left of the
sheet (y down); model X = (px - ox) * k, Y = (oy - py) * k.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from typing import Any, Sequence

from catia_mcp.drawing.geometry import PT, _open, parse_box, require_pymupdf, resolve_frame

MAX_PIXELS = 80_000_000
_COLORS = [(0.0, 0.67, 0.0), (0.86, 0.0, 0.0), (0.0, 0.35, 1.0), (0.78, 0.47, 0.0)]


def default_out_path(pdf: str, page: int, tag: str) -> str:
    d = os.path.join(tempfile.gettempdir(), "catia_mcp_drawing")
    os.makedirs(d, exist_ok=True)
    stem = os.path.splitext(os.path.basename(pdf))[0]
    return os.path.join(d, f"{stem}.p{page}.{tag}.png")


def _clip(pymupdf, pg, box):
    if box is None:
        return None
    return pymupdf.Rect(*(v / PT for v in box)) & pg.rect


def _save(pymupdf, pg, box, dpi, out_png) -> str:
    if not 10 <= dpi <= 1200:
        raise ValueError("dpi must be between 10 and 1200")
    clip = _clip(pymupdf, pg, box)
    r = clip if clip is not None else pg.rect
    if r.is_empty:
        raise ValueError("region is outside the page")
    if (r.width * dpi / 72) * (r.height * dpi / 72) > MAX_PIXELS:
        raise ValueError("image too large: lower dpi or use a smaller region")
    pix = pg.get_pixmap(dpi=dpi, clip=clip)
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    pix.save(out_png)
    return out_png


def _draw_grid(pg, box, k, o, step: float) -> None:
    """Graduated grid every `step` model mm; red vertical / blue horizontal, origin bold."""
    r = pg.rect
    b = box or (0.0, 0.0, r.width * PT, r.height * PT)
    sp = step / k  # paper mm between lines
    sh = pg.new_shape()
    i0, i1 = math.floor((b[0] - o[0]) / sp), math.ceil((b[2] - o[0]) / sp)
    j0, j1 = math.floor((o[1] - b[3]) / sp), math.ceil((o[1] - b[1]) / sp)
    if (i1 - i0) > 400 or (j1 - j0) > 400:
        raise ValueError("grid step too small for this region")
    fs = 6
    for i in range(i0, i1 + 1):
        x = (o[0] + i * sp) / PT
        col = (1, 0, 0) if i == 0 else (1, 0.67, 0.67)
        sh.draw_line((x, b[1] / PT), (x, b[3] / PT))
        sh.finish(color=col, width=0.5 if i else 1.0)
        if i % 5 == 0:
            sh.insert_text((x + 1, b[1] / PT + fs + 1), f"{i * step:g}", fontsize=fs, color=(0.8, 0, 0))
    for j in range(j0, j1 + 1):
        y = (o[1] - j * sp) / PT
        col = (0, 0, 1) if j == 0 else (0.67, 0.67, 1)
        sh.draw_line((b[0] / PT, y), (b[2] / PT, y))
        sh.finish(color=col, width=0.5 if j else 1.0)
        if j % 5 == 0:
            sh.insert_text((b[0] / PT + 1, y - 1), f"{j * step:g}", fontsize=fs, color=(0, 0, 0.8))
    sh.commit()


def render_view(
    pdf: str,
    page: int = 0,
    region: Sequence[float] | None = None,
    out_png: str | None = None,
    dpi: int = 150,
    scale: str | float | None = None,
    origin: Sequence[float] | None = None,
    grid: float | None = None,
) -> str:
    """Render a page (or a paper-mm region) to PNG and return the path.

    ``region`` [x0, y0, x1, y1] paper mm (top-left origin, y down). With ``grid`` (model mm)
    and ``origin`` (paper point) a graduated grid in PART millimetres is drawn, so values
    can be read on the image. Dimension texts are vectorised strokes: read them by eye here.
    """
    pymupdf_box = parse_box(region)
    doc, pg = _open(pdf, page)
    try:
        pymupdf = require_pymupdf()
        out = out_png or default_out_path(pdf, page, "view")
        if grid:
            if origin is None:
                raise ValueError("grid needs origin (paper mm point)")
            k, _, o = resolve_frame(pg, scale, origin)
            _draw_grid(pg, pymupdf_box, k, o, float(grid))
        return _save(pymupdf, pg, pymupdf_box, dpi, out)
    finally:
        doc.close()


# -- sketch entities ---------------------------------------------------------------------
_HV_RE = re.compile(r"^\s*([+-]?)\s*([hv])\s*$")


def parse_hv(hv: str | Sequence[str] = "h,v"):
    """'h,v' / '-h,v' / 'v,h' ... -> function (h, v) -> (X, Y) of the view. No eval."""
    parts = hv.split(",") if isinstance(hv, str) else list(hv)
    if len(parts) != 2:
        raise ValueError("hv must look like 'h,v', '-h,v' or 'v,h'")
    spec = []
    for p in parts:
        m = _HV_RE.match(str(p))
        if not m:
            raise ValueError(f"invalid hv component {p!r} (use h, v, -h or -v)")
        spec.append((-1.0 if m.group(1) == "-" else 1.0, m.group(2)))

    def f(h: float, v: float):
        vals = {"h": h, "v": v}
        return tuple(sg * vals[nm] for sg, nm in spec)

    return f


def _pt(p) -> tuple[float, float]:
    return (float(p[0]), float(p[1]))


def _arc_points(c, a, b, ccw: bool) -> list[tuple[float, float]]:
    r = math.dist(a, c)
    a0 = math.atan2(a[1] - c[1], a[0] - c[0])
    a1 = math.atan2(b[1] - c[1], b[0] - c[0])
    if ccw:
        while a1 <= a0:
            a1 += 2 * math.pi
    else:
        while a1 >= a0:
            a1 -= 2 * math.pi
    # every ~5 degrees AND at most 0.5 mm of chord (a flat polyline on a huge radius would
    # look like an error)
    n = max(2, int(abs(a1 - a0) / math.radians(5)) + 1, int(r * abs(a1 - a0) / 0.5) + 1)
    n = min(n, 5000)
    return [(c[0] + r * math.cos(a0 + (a1 - a0) * i / n), c[1] + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


def _profile_points(prof: dict) -> list[tuple[float, float]]:
    start = _pt(prof["start"])
    segs = prof.get("segments", [])
    out: list[tuple[float, float]] = [start]
    cur = start
    for i, s in enumerate(segs):
        to = _pt(s["to"]) if "to" in s else start
        if s.get("type", "line") == "arc":
            out += _arc_points(_pt(s["center"]), cur, to, s.get("direction", "ccw") == "ccw")[1:]
        else:
            out.append(to)
        cur = to
    if prof.get("closed") and cur != start:
        out.append(start)
    return out


def _entity_polylines(ent: dict) -> list[list[tuple[float, float]]]:
    t = str(ent.get("type") or ent.get("kind") or "").lower()
    t = t.replace("catia_sketch_", "").replace("catia_", "")
    if t == "profile" or ("start" in ent and "segments" in ent):
        return [_profile_points(ent)]
    if t == "line":
        a = ent.get("from") or ent.get("start") or [ent["x1"], ent["y1"]]
        b = ent.get("to") or ent.get("end") or [ent["x2"], ent["y2"]]
        return [[_pt(a), _pt(b)]]
    if t == "circle":
        c = _pt(ent.get("center") or [ent["cx"], ent["cy"]])
        r = float(ent.get("radius", ent.get("r", 0)))
        n = max(72, min(int(r * 2 * math.pi / 0.5) + 1, 5000))
        return [[(c[0] + r * math.cos(2 * math.pi * i / n), c[1] + r * math.sin(2 * math.pi * i / n))
                 for i in range(n + 1)]]
    if t == "arc":
        if "start_angle" in ent:
            c = _pt([ent["cx"], ent["cy"]])
            r = float(ent["radius"])
            a0, a1 = math.radians(ent["start_angle"]), math.radians(ent["end_angle"])
            a = (c[0] + r * math.cos(a0), c[1] + r * math.sin(a0))
            b = (c[0] + r * math.cos(a1), c[1] + r * math.sin(a1))
            return [_arc_points(c, a, b, True)]
        c = _pt(ent["center"])
        a = _pt(ent.get("from") or ent["start"])
        b = _pt(ent.get("to") or ent["end"])
        return [_arc_points(c, a, b, ent.get("direction", "ccw") == "ccw")]
    if t == "rectangle" or ("width" in ent and "height" in ent):
        if "width" in ent:
            cx, cy, w, h = ent["cx"], ent["cy"], ent["width"], ent["height"]
            x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        else:
            x1, y1, x2, y2 = ent["x1"], ent["y1"], ent["x2"], ent["y2"]
        return [[(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]]
    raise ValueError(f"unsupported sketch entity: {json.dumps(ent)[:120]}")


def load_sketch(sketch: Any) -> list[list[tuple[float, float]]]:
    """Sketch description -> list of polylines in sketch (h, v) mm.

    Accepted: a path to a JSON file, a JSON string, or the parsed object. The object may be
    a ``catia_sketch_profile`` argument dict ({start, segments}), one entity dict
    ({type: line|circle|arc|rectangle|profile, ...}, same argument names as the
    ``catia_sketch_*`` tools), a list of those, {"profiles": [...]} / {"entities": [...]},
    or a harness scenario list of ["catia_sketch_profile", {...}] pairs.
    """
    if isinstance(sketch, (str, os.PathLike)):
        s = str(sketch)
        if s.lstrip().startswith(("{", "[")):
            sketch = json.loads(s)
        else:
            with open(s, encoding="utf-8") as fh:
                sketch = json.load(fh)
    polys: list[list[tuple[float, float]]] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for key in ("profiles", "entities"):
                if key in o and isinstance(o[key], list):
                    for x in o[key]:
                        walk(x)
                    return
            polys.extend(_entity_polylines(o))
        elif isinstance(o, (list, tuple)):
            if len(o) == 2 and isinstance(o[0], str) and isinstance(o[1], dict):
                e = dict(o[1])
                e.setdefault("type", o[0])
                if str(e["type"]).replace("catia_sketch_", "") in ("line", "circle", "arc", "profile",
                                                                   "rectangle", "centered_rectangle"):
                    if e["type"].endswith("centered_rectangle"):
                        e["type"] = "rectangle"
                    polys.extend(_entity_polylines(e))
                return
            for x in o:
                walk(x)
        else:
            raise ValueError("sketch must be JSON objects/arrays")

    walk(sketch)
    if not polys:
        raise ValueError("no drawable entity found in sketch")
    return polys


def overlay(
    pdf: str,
    page: int,
    sketch_json: Any,
    out_png: str | None = None,
    region: Sequence[float] | None = None,
    scale: str | float | None = None,
    origin: Sequence[float] | None = None,
    dpi: int = 150,
    hv: str | Sequence[str] = "h,v",
) -> str:
    """Draw sketch entities (model mm) over the drawing and write a PNG; returns the path.

    ``sketch_json`` : see :func:`load_sketch`. ``origin`` (paper mm) is where the sketch/model
    origin sits on the sheet, ``scale`` the drawing scale ('1:2'). ``hv`` maps the sketch
    (h, v) to the view's (X right, Y up): 'h,v' (default), '-h,v', 'v,h', 'v,-h', ...
    Each polyline is drawn in its own colour (green, red, blue, orange); the model origin is
    marked with a small black cross. The coloured line must follow the black drawing.
    """
    box = parse_box(region)
    polys = load_sketch(sketch_json)
    to_view = parse_hv(hv)
    doc, pg = _open(pdf, page)
    try:
        pymupdf = require_pymupdf()
        k, _, o = resolve_frame(pg, scale, origin)

        def P(h, v):
            X, Y = to_view(h, v)
            return ((o[0] + X / k) / PT, (o[1] - Y / k) / PT)

        sh = pg.new_shape()
        for n, poly in enumerate(polys):
            pts = [P(h, v) for h, v in poly]
            sh.draw_polyline([pymupdf.Point(*p) for p in pts])
            sh.finish(color=_COLORS[n % len(_COLORS)], width=1.2, closePath=False)
        ox, oy = o[0] / PT, o[1] / PT
        sh.draw_line((ox - 8, oy), (ox + 8, oy))
        sh.draw_line((ox, oy - 8), (ox, oy + 8))
        sh.finish(color=(0, 0, 0), width=0.8)
        sh.commit()
        out = out_png or default_out_path(pdf, page, "overlay")
        return _save(pymupdf, pg, box, dpi, out)
    finally:
        doc.close()
