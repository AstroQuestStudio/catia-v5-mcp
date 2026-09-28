"""Exact geometry extraction from vector PDF drawings.

Requires PyMuPDF, imported lazily (``pip install "catia-v5-mcp-server[drawing]"``).
PyMuPDF is AGPL-3.0 licensed: it is an OPTIONAL extra, never imported at package import
time and never needed by the rest of the server.

Frames
------
paper : millimetres from the top-left corner of the sheet, y pointing DOWN (PDF convention).
model : ``X = (px - ox) * k``, ``Y = (oy - py) * k`` where ``(ox, oy)`` is ``origin`` (a paper
        point, default the sheet corner) and ``k`` is the drawing scale expressed as a factor
        (title block ``1:2`` means 1 mm on paper = 2 mm on the part, ``k = 2``).
        X points right, Y points UP. All returned coordinates, radii and lengths are model mm.
        Every entity also carries its ``paper`` position so a centre found on the sheet can
        be fed back as ``origin``.

Method
------
* Bezier arcs: centre from the intersection of the end normals (exact for circular Beziers),
  falling back to the 3-point circle for near-half-circles; non-circular curves become lines.
* Polylines of short chained segments (discretised arcs): Kasa least-squares circle, accepted
  only if every vertex is within a relative tolerance and the turning is smooth.
* Consecutive pieces of the same circle are fused and re-fitted (a circle drawn as four
  Beziers becomes one circle).
"""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

PT = 25.4 / 72.0  # millimetres per PDF point

INSTALL_HINT = 'pip install "catia-v5-mcp-server[drawing]"'


class DrawingDependencyError(ImportError):
    """PyMuPDF is missing."""

    def __init__(self) -> None:
        super().__init__(
            "PyMuPDF is required for drawing tools (optional, AGPL-licensed). "
            f"Install it with: {INSTALL_HINT}"
        )


def require_pymupdf():
    """Import PyMuPDF lazily; raise DrawingDependencyError if unavailable."""
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError:
        raise DrawingDependencyError() from None


def is_available() -> bool:
    try:
        require_pymupdf()
        return True
    except DrawingDependencyError:
        return False


# -- parsing helpers ---------------------------------------------------------------------
def parse_scale(scale: str | float | int | None) -> float | None:
    """'1:2' -> 2.0 (1 mm paper = 2 mm part); '2:1' -> 0.5; a number is the factor itself."""
    if scale is None or scale == "":
        return None
    if isinstance(scale, (int, float)):
        k = float(scale)
    else:
        s = str(scale).strip().replace(",", ".")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", s)
        try:
            k = float(m.group(2)) / float(m.group(1)) if m else float(s)
        except (ValueError, ZeroDivisionError):
            raise ValueError(f"invalid scale {scale!r} (expected e.g. '1:2')") from None
    if not k > 0:
        raise ValueError(f"invalid scale {scale!r}")
    return k


def format_scale(k: float) -> str:
    if k >= 1:
        return f"1:{k:g}"
    return f"{1 / k:g}:1"


def parse_box(box: Sequence[float] | str | None) -> tuple[float, float, float, float] | None:
    if box is None:
        return None
    if isinstance(box, str):
        box = [float(v) for v in box.split(",")]
    b = tuple(float(v) for v in box)
    if len(b) != 4:
        raise ValueError("region must be [x0, y0, x1, y1] in paper mm")
    return (min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3]))


def parse_point(p: Sequence[float] | str | None) -> tuple[float, float] | None:
    if p is None:
        return None
    if isinstance(p, str):
        p = [float(v) for v in p.split(",")]
    if len(p) != 2:
        raise ValueError("origin must be [x, y] in paper mm")
    return (float(p[0]), float(p[1]))


_SCALE_RE = re.compile(
    r"(?is)(?:scale|[ée]chelle)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)"
)


def detect_scale(pg) -> float | None:
    """Best-effort scale from title-block text (only works if the text is real text)."""
    try:
        m = _SCALE_RE.search(pg.get_text() or "")
    except Exception:
        return None
    if not m:
        return None
    a = float(m.group(1).replace(",", "."))
    b = float(m.group(2).replace(",", "."))
    return b / a if a > 0 and b > 0 else None


# -- circle fitting ----------------------------------------------------------------------
def circle3(p1, p2, p3):
    (x1, y1), (x2, y2), (x3, y3) = p1, p2, p3
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None
    s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
    ux = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d
    uy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d
    return ux, uy, math.dist((ux, uy), p1)


def kasa_fit(pts):
    """Least-squares circle (Kasa) through all points -> (cx, cy, r) or None."""
    n = len(pts)
    if n < 3:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    u = [p[0] - mx for p in pts]
    v = [p[1] - my for p in pts]
    suu = sum(a * a for a in u)
    svv = sum(b * b for b in v)
    suv = sum(a * b for a, b in zip(u, v))
    suuu = sum(a ** 3 for a in u)
    svvv = sum(b ** 3 for b in v)
    suvv = sum(a * b * b for a, b in zip(u, v))
    svuu = sum(b * a * a for a, b in zip(u, v))
    det = suu * svv - suv * suv
    if abs(det) < 1e-12:
        return None
    b1 = 0.5 * (suuu + suvv)
    b2 = 0.5 * (svvv + svuu)
    uc = (b1 * svv - b2 * suv) / det
    vc = (suu * b2 - suv * b1) / det
    r = math.sqrt(max(uc * uc + vc * vc + (suu + svv) / n, 0.0))
    return mx + uc, my + vc, r


def _tol(r: float) -> float:
    """Residual tolerance (paper mm), relative to the radius with an absolute floor."""
    return max(0.03, 0.002 * r)


def _bez(p, t):
    a, b, c, d = p
    m = 1 - t
    return (
        m**3 * a[0] + 3 * m * m * t * b[0] + 3 * m * t * t * c[0] + t**3 * d[0],
        m**3 * a[1] + 3 * m * m * t * b[1] + 3 * m * t * t * c[1] + t**3 * d[1],
    )


def _residual(pts, c) -> float:
    return max(abs(math.dist(q, c[:2]) - c[2]) for q in pts)


def _bezier_circle(P):
    """Circle for a cubic Bezier (4 paper points) or None if it is not a circular arc."""
    p0, p1, p2, p3 = P
    t0 = (p1[0] - p0[0], p1[1] - p0[1])
    if math.hypot(*t0) < 1e-9:
        t0 = (p2[0] - p0[0], p2[1] - p0[1])
    t1 = (p3[0] - p2[0], p3[1] - p2[1])
    if math.hypot(*t1) < 1e-9:
        t1 = (p3[0] - p1[0], p3[1] - p1[1])
    if math.hypot(*t0) < 1e-9 or math.hypot(*t1) < 1e-9:
        return None
    # centre = intersection of the normals at both ends (exact for circular Beziers)
    n0, n1 = (-t0[1], t0[0]), (-t1[1], t1[0])
    det = n0[0] * (-n1[1]) - n0[1] * (-n1[0])
    c = None
    if abs(det) > 1e-3 * math.hypot(*n0) * math.hypot(*n1):
        rx, ry = p3[0] - p0[0], p3[1] - p0[1]
        s = (rx * (-n1[1]) - ry * (-n1[0])) / det
        cx, cy = p0[0] + s * n0[0], p0[1] + s * n0[1]
        r0, r1 = math.dist((cx, cy), p0), math.dist((cx, cy), p3)
        if abs(r0 - r1) <= _tol(r0):
            c = (cx, cy, 0.5 * (r0 + r1))
    if c is None:
        c = circle3(p0, _bez(P, 0.5), p3)
    if c is None or c[2] > 2000 or c[2] < 1e-6:
        return None
    samp = [_bez(P, t / 8) for t in range(9)]
    if _residual(samp, c) > max(0.05, 0.003 * c[2]):
        return None
    return c


# -- raw extraction (paper mm) -----------------------------------------------------------
def _turning_ok(chain) -> bool:
    sign = 0
    for a, b, c in zip(chain, chain[1:], chain[2:]):
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        cr = v1[0] * v2[1] - v1[1] * v2[0]
        dt = v1[0] * v2[0] + v1[1] * v2[1]
        ang = math.atan2(cr, dt)
        if abs(ang) > math.radians(25):
            return False
        s = 1 if ang > 1e-6 else -1 if ang < -1e-6 else 0
        if s and sign and s != sign:
            return False
        sign = sign or s
    return sign != 0


def _raw_entities(pg):
    """Lines ('L', p, q) and arc dicts, all in paper mm."""
    lines: list = []
    arcs: list[dict] = []
    for path in pg.get_drawings():
        chain: list = []

        def flush():
            if len(chain) >= 4 and _turning_ok(chain):
                c = kasa_fit(chain)
                if c and c[2] < 2000 and _residual(chain, c) < _tol(c[2]):
                    arcs.append({"c": c, "fit": list(chain), "samp": list(chain)})
                    chain.clear()
                    return
            for a, b in zip(chain, chain[1:]):
                lines.append(("L", a, b))
            chain.clear()

        for it in path["items"]:
            kind = it[0]
            if kind == "l":
                a = (it[1].x * PT, it[1].y * PT)
                b = (it[2].x * PT, it[2].y * PT)
                if chain and math.dist(chain[-1], a) < 1e-3 and math.dist(a, b) < 3.0:
                    chain.append(b)
                else:
                    flush()
                    chain.extend([a, b])
            elif kind == "c":
                flush()
                P = [(q.x * PT, q.y * PT) for q in it[1:5]]
                c = _bezier_circle(P)
                samp = [_bez(P, t / 8) for t in range(9)]
                if c:
                    arcs.append({"c": c, "fit": [P[0], P[3]], "samp": samp})
                else:
                    lines.extend(("L", a, b) for a, b in zip(samp, samp[1:]))
            elif kind == "re":
                flush()
                r = it[1]
                q = [(r.x0 * PT, r.y0 * PT), (r.x1 * PT, r.y0 * PT), (r.x1 * PT, r.y1 * PT), (r.x0 * PT, r.y1 * PT)]
                lines.extend(("L", q[i], q[(i + 1) % 4]) for i in range(4))
            elif kind == "qu":
                flush()
                qd = it[1]
                q = [(p.x * PT, p.y * PT) for p in (qd.ul, qd.ur, qd.lr, qd.ll)]
                lines.extend(("L", q[i], q[(i + 1) % 4]) for i in range(4))
        flush()
    return lines, _merge_arcs(arcs)


def _merge_arcs(arcs: list[dict]) -> list[dict]:
    """Fuse touching pieces of one circle and refit on all their vertices."""
    items = [dict(a, fit=list(a["fit"]), samp=list(a["samp"])) for a in arcs]
    changed = True
    while changed:
        changed = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                m, a = items[i], items[j]
                tol = max(0.02, 0.005 * max(m["c"][2], a["c"][2]))
                if abs(m["c"][2] - a["c"][2]) > tol or math.dist(m["c"][:2], a["c"][:2]) > tol:
                    continue
                ms, me = m["samp"][0], m["samp"][-1]
                as_, ae = a["samp"][0], a["samp"][-1]
                link = 0.02 + 1e-4 * m["c"][2]
                if math.dist(me, as_) < link:
                    ns, nf = m["samp"] + a["samp"][1:], m["fit"] + a["fit"]
                elif math.dist(ms, ae) < link:
                    ns, nf = a["samp"] + m["samp"][1:], a["fit"] + m["fit"]
                elif math.dist(me, ae) < link:
                    ns, nf = m["samp"] + a["samp"][::-1][1:], m["fit"] + a["fit"][::-1]
                elif math.dist(ms, as_) < link:
                    ns, nf = a["samp"][::-1] + m["samp"][1:], a["fit"][::-1] + m["fit"]
                else:
                    continue
                f = kasa_fit(nf)
                c = f if f and _residual(ns, f) <= max(0.05, 0.003 * f[2]) else m["c"]
                items[i] = {"c": c, "fit": nf, "samp": ns}
                del items[j]
                changed = True
                break
            if changed:
                break
    return items


# -- public API --------------------------------------------------------------------------
def _in(p, box) -> bool:
    return box is None or (box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3])


def _signed_span(pts, c) -> float:
    span = 0.0
    for p, q in zip(pts, pts[1:]):
        t1 = math.atan2(p[1] - c[1], p[0] - c[0])
        t2 = math.atan2(q[1] - c[1], q[0] - c[0])
        span += (t2 - t1 + math.pi) % (2 * math.pi) - math.pi
    return math.degrees(span)


def _open(pdf: str, page: int):
    pymupdf = require_pymupdf()
    try:
        doc = pymupdf.open(pdf)
    except Exception as exc:  # pymupdf raises its own FileNotFoundError / FileDataError
        raise ValueError(f"cannot open PDF {pdf!r}: {exc}") from None
    if not (0 <= page < doc.page_count):
        n = doc.page_count
        doc.close()
        raise ValueError(f"page {page} out of range (document has {n} page(s), 0-based)")
    return doc, doc[page]


def resolve_frame(pg, scale, origin):
    """-> (k, scale_source, origin_paper_mm)."""
    k = parse_scale(scale)
    if k is not None:
        src = "argument"
    else:
        k = detect_scale(pg)
        src = "title block text" if k is not None else "default 1:1 (none found)"
        if k is None:
            k = 1.0
    o = parse_point(origin)
    return k, src, o if o is not None else (0.0, 0.0)


def extract(
    pdf: str,
    page: int = 0,
    region: Sequence[float] | None = None,
    scale: str | float | None = None,
    origin: Sequence[float] | None = None,
    min_len: float = 1.0,
    min_r: float = 0.6,
) -> dict[str, Any]:
    """Extract circles, arcs and line segments of one page of a vector PDF.

    ``region``  [x0, y0, x1, y1] in PAPER mm (top-left origin, y down) or None for the sheet.
    ``scale``   '1:2' style string or numeric factor; None = read the title block text if any
                (may be absent or wrong: always check it), else 1:1.
    ``origin``  paper point [x, y] that becomes model (0, 0); default the sheet corner.
    ``min_len`` / ``min_r``  ignore segments / arcs smaller than this, in PAPER mm.

    Returns model-mm coordinates (X right, Y up, see module docstring):
    ``circles`` [{cx, cy, r, d, kind:'circle', paper}], ``arcs`` [{cx, cy, r, kind:'arc',
    span_deg, direction, start, end, paper}], ``lines`` [{x0, y0, x1, y1, length,
    angle_deg}], ``bbox`` [xmin, ymin, xmax, ymax] (model mm) and metadata.
    """
    box = parse_box(region)
    doc, pg = _open(pdf, page)
    try:
        k, src, o = resolve_frame(pg, scale, origin)
        n_draw = len(pg.get_drawings())
        raw_lines, raw_arcs = _raw_entities(pg)
        page_w, page_h = pg.rect.width * PT, pg.rect.height * PT
    finally:
        doc.close()

    def M(p):
        return ((p[0] - o[0]) * k, (o[1] - p[1]) * k)

    warnings: list[str] = []
    if n_draw == 0:
        warnings.append("no vector drawing on this page (scanned image?): nothing to extract")
    if src.startswith("default"):
        warnings.append("scale not found: assumed 1:1. Pass scale='1:x' from the title block")

    circles: list[dict] = []
    arcs: list[dict] = []
    seen: set = set()
    for a in raw_arcs:
        cx, cy, r = a["c"]
        s = a["samp"]
        if r < min_r or not _in(s[len(s) // 2], box):
            continue
        ms = [M(p) for p in s]
        c_m = M((cx, cy))
        span = _signed_span(ms, c_m)
        closed = abs(span) >= 355 or math.dist(s[0], s[-1]) < 0.02 + 1e-4 * r
        key = (round(cx, 1), round(cy, 1), round(r, 1), 360 if closed else round(abs(span) / 5))
        if key in seen:
            continue
        seen.add(key)
        ent = {"cx": c_m[0], "cy": c_m[1], "r": r * k, "paper": [cx, cy]}
        if closed:
            circles.append({**ent, "d": 2 * r * k, "kind": "circle"})
        else:
            arcs.append({
                **ent, "kind": "arc", "span_deg": abs(span),
                "direction": "ccw" if span > 0 else "cw",
                "start": list(ms[0]), "end": list(ms[-1]),
            })

    lines: list[dict] = []
    seen = set()
    for _, p, q in raw_lines:
        if math.dist(p, q) < min_len or not (_in(p, box) and _in(q, box)):
            continue
        a, b = M(p), M(q)
        key = tuple(sorted([(round(p[0], 1), round(p[1], 1)), (round(q[0], 1), round(q[1], 1))]))
        if key in seen:
            continue
        seen.add(key)
        dx, dy = b[0] - a[0], b[1] - a[1]
        lines.append({
            "x0": a[0], "y0": a[1], "x1": b[0], "y1": b[1],
            "length": math.hypot(dx, dy), "angle_deg": math.degrees(math.atan2(dy, dx)) % 180,
        })

    circles.sort(key=lambda e: -e["r"])
    arcs.sort(key=lambda e: -e["r"])
    lines.sort(key=lambda e: -e["length"])

    xs: list[float] = []
    ys: list[float] = []
    for e in circles:
        xs += [e["cx"] - e["r"], e["cx"] + e["r"]]
        ys += [e["cy"] - e["r"], e["cy"] + e["r"]]
    for e in arcs:
        xs += [e["start"][0], e["end"][0]]
        ys += [e["start"][1], e["end"][1]]
    for e in lines:
        xs += [e["x0"], e["x1"]]
        ys += [e["y0"], e["y1"]]
    bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else None

    def rnd(v):
        if isinstance(v, float):
            return round(v, 4)
        if isinstance(v, list):
            return [rnd(x) for x in v]
        return v

    def clean(e):
        return {kk: rnd(vv) for kk, vv in e.items()}

    return {
        "frame": "model mm: X right, Y up, origin = `origin_paper`; paper = mm from top-left, y down",
        "units": "mm",
        "scale": k,
        "scale_text": format_scale(k),
        "scale_source": src,
        "origin_paper": list(o),
        "region_paper": list(box) if box else None,
        "sheet_paper_mm": [round(page_w, 2), round(page_h, 2)],
        "bbox": rnd(bbox) if bbox else None,
        "circles": [clean(e) for e in circles],
        "arcs": [clean(e) for e in arcs],
        "lines": [clean(e) for e in lines],
        "counts": {"circles": len(circles), "arcs": len(arcs), "lines": len(lines)},
        "warnings": warnings,
    }
