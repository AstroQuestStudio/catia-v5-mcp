"""Closed-loop check of a CATIA drawing: read the exported PDF back and compare it with the 3D model.

Pure Python (no COM). Needs PyMuPDF at call time only (optional ``drawing`` extra).

Why a second circle finder: CATIA exports large circles as polylines of hundreds of very short
chords. The vertex jitter of the PDF (about 0.01 pt) flips the sign of the tiny turning angles,
and ``catia_mcp.drawing.extract`` then rejects the chain (proven live: circles of radius 5 mm and
above were missed, radius 3 mm was found). ``polyline_circles`` fits the chain with the same
least-squares circle but judges "is it one circle" from the *total* turning angle and the fit
residual, which is insensitive to that jitter. ``find_circles`` returns the union of both readers.

Frames (same as ``extract``): ``paper`` = mm from the top-left corner of the PDF page, y down;
``model`` = ``X = (px - ox) * k``, ``Y = (oy - py) * k`` with ``k`` = model/paper (a 1:2 drawing
has k = 2). CATIA sheet coordinates are bottom-left, y up: ``py = sheet_height - y_sheet``.

The view frame: ``view.x, view.y`` is the centre of the projected 3D bounding box and
``(xAxisData, yAxisData)`` the sheet position of the projected 3D origin (proven live);
``ViewFrame`` does the conversion to the PDF frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from catia_mcp import standards as _std
from catia_mcp.drawing import geometry as _geo

PT = _geo.PT  # mm per PDF point

# ---------------------------------------------------------------------------- 3D -> view maths

# For each view kind: (right axis, up axis) of the view expressed in the coordinates of the
# FRONT view basis, i.e. with the front view defined by DefineFrontView(hx, hy, hz, vx, vy, vz):
# ``h`` = view horizontal axis, ``v`` = view vertical axis, ``n`` = h x v (towards the viewer).
# The mapping was measured live on an asymmetric bracket (see docs/DRAFTING.md).
# Each entry: (sign_h_component, ...) is computed by ``view_axes`` below; kept as data here.
VIEW_KINDS = ("front", "right", "left", "top", "bottom", "rear")


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _neg(a: Sequence[float]) -> tuple[float, float, float]:
    return (-a[0], -a[1], -a[2])


def view_axes(kind: str, h: Sequence[float], v: Sequence[float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(right, up) unit-free axes of view ``kind`` in model coordinates, given the front view axes.

    ``h``/``v`` are the two vectors passed to DefineFrontView (horizontal, vertical) and the
    viewer stands on the side of ``n = h x v``. Third-angle vs first-angle changes where the
    views are PLACED on the sheet, never what each named view shows.
    """
    n = _cross(h, v)
    table = {
        "front": (h, v),
        "rear": (_neg(h), v),
        "right": (_neg(n), v),   # seen from the right of the front view: depth axis points right
        "left": (n, v),
        "top": (h, _neg(n)),
        "bottom": (h, n),
    }
    if kind not in table:
        raise ValueError(f"unknown view kind {kind!r}; expected one of {', '.join(VIEW_KINDS)}")
    return table[kind]


def project(point: Sequence[float], right: Sequence[float], up: Sequence[float]) -> tuple[float, float]:
    """2D coordinates (u, w) of a 3D ``point`` in a view with the given axes (model mm)."""
    return (sum(p * a for p, a in zip(point, right)), sum(p * a for p, a in zip(point, up)))


@dataclass
class ViewFrame:
    """Where a generated view sits on the sheet, as CATIA reports it.

    ``x``/``y`` = view centre in sheet mm (bottom-left origin, y up); ``scale`` = paper/model
    (CATIA ``View.Scale``); ``origin`` = ``(xAxisData, yAxisData)``: where the projection of the
    3D origin lands on the sheet (proven live: it equals the view centre minus the centre of the
    projected bounding box). ``sheet_height`` converts to the PDF frame (y down).
    """

    x: float
    y: float
    scale: float
    origin: tuple[float, float]
    sheet_height: float
    right: tuple[float, ...] = (1.0, 0.0, 0.0)
    up: tuple[float, ...] = (0.0, 1.0, 0.0)

    def origin_paper(self) -> tuple[float, float]:
        """PDF-frame paper point (mm) where the model point (u, w) = (0, 0) of the view lands."""
        return self.origin[0], self.sheet_height - self.origin[1]

    def k(self) -> float:
        """model/paper factor expected by ``extract`` (1 / CATIA scale)."""
        return 1.0 / self.scale

    def region_paper(self, half_w: float, half_h: float, margin: float = 1.0) -> list[float]:
        """PDF-frame box around the view centre (paper mm), for ``extract(region=...)``."""
        cx, cy = self.x, self.sheet_height - self.y
        return [cx - half_w - margin, cy - half_h - margin, cx + half_w + margin, cy + half_h + margin]


def projected_box(points: Iterable[Sequence[float]], right: Sequence[float], up: Sequence[float]
                  ) -> tuple[float, float, float, float]:
    """(umin, wmin, umax, wmax) of 3D points projected on a view."""
    us, ws = [], []
    for p in points:
        u, w = project(p, right, up)
        us.append(u)
        ws.append(w)
    return min(us), min(ws), max(us), max(ws)


def box_corners(bbox: Sequence[float]) -> list[tuple[float, float, float]]:
    """Eight corners of a bounding box [xmin, ymin, zmin, xmax, ymax, zmax]."""
    x0, y0, z0, x1, y1, z1 = bbox
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


# ---------------------------------------------------------------------------- PDF reading

def _unwrap_span(pts: Sequence[tuple[float, float]], c: tuple[float, float]) -> float:
    """Total signed turning (degrees) of a polyline around centre ``c``."""
    span = 0.0
    for p, q in zip(pts, pts[1:]):
        a1 = math.atan2(p[1] - c[1], p[0] - c[0])
        a2 = math.atan2(q[1] - c[1], q[0] - c[0])
        span += (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
    return math.degrees(span)


def polyline_circles(pdf: str, page: int = 0, region: Sequence[float] | None = None,
                     scale: float = 1.0, origin: Sequence[float] | None = None,
                     min_r: float = 0.6, min_chain: int = 8) -> list[dict[str, Any]]:
    """Circles and arcs drawn as chained short segments. Coordinates in model mm (see module doc).

    A chain is accepted when every vertex lies within ``max(0.03, 0.002 r)`` paper mm of the
    least-squares circle and the chain turns steadily (total turning >= 30 degrees, no step above
    45 degrees). ``kind`` is ``circle`` when the total turning reaches 355 degrees or the chain
    closes on itself, else ``arc`` with ``span_deg``. ``scale`` is model/paper.
    """
    pymupdf = _geo.require_pymupdf()
    doc = pymupdf.open(pdf)
    try:
        pg = doc[page]
        ox, oy = (0.0, 0.0) if origin is None else (float(origin[0]), float(origin[1]))
        chains: list[list[tuple[float, float]]] = []
        for path in pg.get_drawings():
            chain: list[tuple[float, float]] = []
            for it in path["items"]:
                if it[0] != "l":
                    if len(chain) >= min_chain:
                        chains.append(chain)
                    chain = []
                    continue
                a = (it[1].x * PT, it[1].y * PT)
                b = (it[2].x * PT, it[2].y * PT)
                if chain and math.dist(chain[-1], a) < 1e-3 and math.dist(a, b) < 3.0:
                    chain.append(b)
                else:
                    if len(chain) >= min_chain:
                        chains.append(chain)
                    chain = [a, b]
            if len(chain) >= min_chain:
                chains.append(chain)
    finally:
        doc.close()

    out: list[dict[str, Any]] = []
    for chain in chains:
        fit = _geo.kasa_fit(chain)
        if not fit or fit[2] < min_r or fit[2] > 2000:
            continue
        cx, cy, r = fit
        if max(abs(math.dist(q, (cx, cy)) - r) for q in chain) > max(0.03, 0.002 * r):
            continue
        # every step must turn by a small angle in one direction (allow jitter, not reversals)
        span = _unwrap_span(chain, (cx, cy))
        if abs(span) < 30:
            continue
        closed = abs(span) >= 355 or math.dist(chain[0], chain[-1]) < 0.02 + 1e-4 * r
        mx, my = (cx - ox) * scale, (oy - cy) * scale
        ent: dict[str, Any] = {"cx": mx, "cy": my, "r": r * scale, "paper": [cx, cy]}
        if closed:
            ent.update(kind="circle", d=2 * r * scale)
        else:
            ent.update(kind="arc", span_deg=abs(span))
        if region is not None and not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
            continue
        out.append(ent)
    return out


def find_circles(pdf: str, page: int = 0, region: Sequence[float] | None = None,
                 scale: str | float | None = None, origin: Sequence[float] | None = None,
                 min_r: float = 0.6, include_arcs: bool = False) -> list[dict[str, Any]]:
    """Full circles of a page region in model mm: union of ``extract`` and ``polyline_circles``.
    With ``include_arcs`` the arcs are returned too (``kind='arc'``, ``d`` = twice the radius).

    ``scale`` is model/paper as for ``extract`` ("1:2" or 2.0); it must be given explicitly.
    Duplicates (same centre within 0.05 and diameter within 0.05) are merged.
    """
    if scale is None:
        raise ValueError("scale is required (model/paper, e.g. '1:2' or 2.0): never read it from the sheet text")
    k = _geo.parse_scale(scale)
    if k is None:
        raise ValueError(f"cannot read scale {scale!r}")
    res = _geo.extract(pdf, page, region, scale, origin)
    found = [dict(c) for c in res["circles"] if c["r"] >= min_r]
    if include_arcs:
        found += [dict(a, d=2 * a["r"]) for a in res["arcs"] if a["r"] >= min_r]
    for c in polyline_circles(pdf, page, region, k, origin, min_r=min_r / k if k else min_r):
        if c["kind"] != "circle" and not include_arcs:
            continue
        c = dict(c, d=2 * c["r"])
        if not any(math.hypot(c["cx"] - f["cx"], c["cy"] - f["cy"]) < 0.05 and abs(c["d"] - f["d"]) < 0.05
                   for f in found):
            found.append(c)
    found.sort(key=lambda e: -e["d"])
    return found


# ---------------------------------------------------------------------------- comparison

@dataclass
class Match:
    expected: dict[str, Any]
    measured: dict[str, Any] | None
    d_error: float | None = None
    centre_error: float | None = None
    ok: bool = False


@dataclass
class CircleReport:
    matches: list[Match] = field(default_factory=list)
    unexpected: list[dict[str, Any]] = field(default_factory=list)   # measured, matching nothing

    @property
    def ok(self) -> bool:
        return all(m.ok for m in self.matches)

    def worst(self) -> tuple[float, float]:
        d = [abs(m.d_error) for m in self.matches if m.d_error is not None]
        c = [m.centre_error for m in self.matches if m.centre_error is not None]
        return (max(d) if d else float("nan"), max(c) if c else float("nan"))


def match_circles(expected: Sequence[dict[str, Any]], measured: Sequence[dict[str, Any]],
                  d_tol: float = 0.02, centre_tol: float = 0.05, search: float = 5.0) -> CircleReport:
    """One-to-one nearest-neighbour matching on the centre, then tolerance test.

    ``expected`` items: ``{"cx", "cy", "d"}`` (model mm). A pair is formed by increasing centre
    distance (only within ``search`` mm); the diameter is NOT used to pair, so a wrong diameter is
    reported as an error instead of as a missing circle.
    """
    pairs = sorted(((math.hypot(e["cx"] - m["cx"], e["cy"] - m["cy"]), i, j)
                    for i, e in enumerate(expected) for j, m in enumerate(measured)), key=lambda t: t[0])
    used_e: set[int] = set()
    used_m: set[int] = set()
    rep = CircleReport(matches=[Match(e, None) for e in expected])
    for dist, i, j in pairs:
        if dist > search:
            break
        if i in used_e or j in used_m:
            continue
        used_e.add(i)
        used_m.add(j)
        m = measured[j]
        derr = m["d"] - expected[i]["d"]
        rep.matches[i] = Match(expected[i], m, derr, dist, abs(derr) <= d_tol and dist <= centre_tol)
    rep.unexpected = [m for j, m in enumerate(measured) if j not in used_m]
    return rep


def pitch_errors(rep: CircleReport) -> list[dict[str, Any]]:
    """Errors of the distances between all pairs of matched circle centres (entraxes)."""
    got = [m for m in rep.matches if m.measured is not None]
    out = []
    for a in range(len(got)):
        for b in range(a + 1, len(got)):
            ea, eb = got[a].expected, got[b].expected
            ma, mb = got[a].measured, got[b].measured
            de = math.hypot(ea["cx"] - eb["cx"], ea["cy"] - eb["cy"])
            dm = math.hypot(ma["cx"] - mb["cx"], ma["cy"] - mb["cy"])  # type: ignore[index]
            out.append({"between": [ea.get("label", a), eb.get("label", b)], "expected": de,
                        "measured": dm, "error": dm - de})
    return out


# ---------------------------------------------------------------------------- view definition

# ``view_from`` names the side the viewer stands on for the FRONT view; each entry gives the two
# vectors passed to DefineFrontView (horizontal axis, vertical axis of the view). With the
# viewer on -Y (CATIA's usual front) X points right and Z up. n = h x v points at the viewer.
VIEW_FROM: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "-Y": ((1, 0, 0), (0, 0, 1)),
    "+Y": ((-1, 0, 0), (0, 0, 1)),
    "+Z": ((1, 0, 0), (0, 1, 0)),
    "-Z": ((1, 0, 0), (0, -1, 0)),
    "+X": ((0, 1, 0), (0, 0, 1)),
    "-X": ((0, -1, 0), (0, 0, 1)),
}


def front_axes(view_from: str = "-Y") -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The two DefineFrontView vectors (horizontal, vertical) for a viewer standing on ``view_from``."""
    key = str(view_from).strip().upper().replace(" ", "")
    if key in ("X", "Y", "Z"):
        key = "+" + key
    if key not in VIEW_FROM:
        raise ValueError(f"view_from must be one of {', '.join(VIEW_FROM)} (the side the viewer stands on), "
                         f"got {view_from!r}")
    return VIEW_FROM[key]


def _unit(a: Sequence[float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(x * x for x in a))
    if n < 1e-12:
        raise ValueError("null vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def isometric_axes(h: Sequence[float], v: Sequence[float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(right, up) of the isometric view seen from the front-right-top corner of the front frame
    (viewer along h + v + n). They are passed to DefineIsometricView like the front vectors."""
    n = _cross(h, v)
    d = _unit([h[i] + v[i] + n[i] for i in range(3)])
    dv = sum(v[i] * d[i] for i in range(3))
    up = _unit([v[i] - dv * d[i] for i in range(3)])
    right = _unit(_cross(up, d))
    return right, up


def view_size_from_bbox(bbox: Sequence[float], right: Sequence[float], up: Sequence[float]) -> tuple[float, float]:
    """(width, height) in model mm of the projection of a 3D bounding box on a view."""
    u0, w0, u1, w1 = projected_box(box_corners(bbox), right, up)
    return u1 - u0, w1 - w0


# ---------------------------------------------------------------------------- layout planning

_SIDE_VECTOR = {"left": (-1, 0), "right": (1, 0), "above": (0, 1), "below": (0, -1)}


def plan_positions(sizes: dict[str, tuple[float, float]], projection: str, scale: float, gap_mm: float = 25.0
                   ) -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    """Centres (paper mm, y up, composite centred on 0,0) and the paper size of the composite.

    ``sizes`` maps a view kind (front, top, bottom, left, right, rear) to its model size (w, h);
    ``scale`` = paper/model. Placement follows the projection method (ISO 5456-2:1996 5.1): first
    angle puts the top view BELOW the front view and the right view on its LEFT; third angle
    mirrors that. The rear view goes beyond the rightmost view of the front row.
    """
    if "front" not in sizes:
        raise ValueError("a layout needs a front view")
    if projection not in _std.PROJECTION_METHODS:
        raise ValueError(f"projection must be one of {_std.PROJECTION_METHODS}")
    fw, fh = sizes["front"][0] * scale, sizes["front"][1] * scale
    pos: dict[str, tuple[float, float]] = {"front": (0.0, 0.0)}
    for kind, (w, h) in sizes.items():
        if kind in ("front", "rear"):
            continue
        w, h = w * scale, h * scale
        side = _std.expected_view_side(projection, kind)
        dx, dy = _SIDE_VECTOR[side]  # type: ignore[index]
        if dx:
            pos[kind] = (dx * (fw / 2 + gap_mm + w / 2), 0.0)
        else:
            pos[kind] = (0.0, dy * (fh / 2 + gap_mm + h / 2))
    if "rear" in sizes:
        w = sizes["rear"][0] * scale
        xmax = max(p[0] + sizes[k][0] * scale / 2 for k, p in pos.items() if p[1] == 0.0)
        pos["rear"] = (xmax + gap_mm + w / 2, 0.0)
    x0 = min(pos[k][0] - sizes[k][0] * scale / 2 for k in pos)
    x1 = max(pos[k][0] + sizes[k][0] * scale / 2 for k in pos)
    y0 = min(pos[k][1] - sizes[k][1] * scale / 2 for k in pos)
    y1 = max(pos[k][1] + sizes[k][1] * scale / 2 for k in pos)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return {k: (p[0] - cx, p[1] - cy) for k, p in pos.items()}, (x1 - x0, y1 - y0)


@dataclass
class LayoutPlan:
    sheet: str
    orientation: str
    scale: str
    ratio: float
    positions: dict[str, tuple[float, float]]      # sheet mm, bottom-left origin, view centres
    free_area: tuple[float, float, float, float]   # x0, y0, x1, y1 usable for views
    composite_mm: tuple[float, float]
    fits: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"sheet": self.sheet, "orientation": self.orientation, "scale": self.scale,
                "positions": {k: [round(v[0], 2), round(v[1], 2)] for k, v in self.positions.items()},
                "free_area": [round(v, 2) for v in self.free_area],
                "composite_mm": [round(v, 2) for v in self.composite_mm], "fits": self.fits, "notes": self.notes}


def free_area(sheet: str, orientation: str | None, title_block_height_mm: float, margin_mm: float = 10.0
              ) -> tuple[float, float, float, float]:
    """Frame minus the title block strip minus a margin (paper mm, sheet coordinates y up)."""
    x0, y0, x1, y1 = _std.frame_rect(sheet, orientation)
    return (x0 + margin_mm, y0 + title_block_height_mm + margin_mm, x1 - margin_mm, y1 - margin_mm)


def plan_layout(sizes: dict[str, tuple[float, float]], projection: str = "first_angle", *,
                sheet: str = "auto", scale: Any = "auto", orientation: str | None = None,
                title_block_height_mm: float = 45.0, gap_mm: float = 25.0, margin_mm: float = 10.0,
                min_scale: Any = "1:2", max_scale: Any = "1:1",
                sheets: Sequence[str] = ("A4", "A3", "A2", "A1", "A0")) -> LayoutPlan:
    """Choose sheet and scale (when ``auto``) so that the projected views fit, and place them.

    ``sizes``: model size (w, h) of every view of the arrangement (see ``plan_positions``).
    Sheets are tried from small to large, scales from ``max_scale`` down (recommended series only,
    ISO 5455:1979 5.1); the first pair with scale >= ``min_scale`` wins, else the best of the
    largest sheet is returned with a note. A fixed ``sheet`` and/or ``scale`` restrict the search;
    ``fits`` is False when a fixed choice does not hold the views (nothing is silently shrunk).
    """
    fixed_scale = None if str(scale).lower() == "auto" else _std.parse_scale(scale)
    min_f = _std.parse_scale(min_scale).fraction
    max_f = _std.parse_scale(max_scale).fraction
    names = list(sheets) if str(sheet).lower() == "auto" else [str(sheet).upper()]
    cands = sorted((_std.get_sheet(n) for n in names), key=lambda s: s.short * s.long)
    ladder = [s for s in _std.PREFERRED_SCALES if min_f <= s.fraction <= max_f]
    if fixed_scale is not None:
        ladder = [fixed_scale]
    if not ladder:
        raise ValueError("no recommended scale between min_scale and max_scale")
    best: LayoutPlan | None = None
    for sh in cands:
        orient = orientation or sh.orientation
        area = free_area(sh.name, orient, title_block_height_mm, margin_mm)
        aw, ah = area[2] - area[0], area[3] - area[1]
        for sc in ladder:
            rel, (cw, ch) = plan_positions(sizes, projection, sc.ratio, gap_mm)
            fits = cw <= aw + 1e-9 and ch <= ah + 1e-9
            cx, cy = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
            plan = LayoutPlan(sh.name, orient, sc.label, sc.ratio,
                              {k: (cx + p[0], cy + p[1]) for k, p in rel.items()}, area, (cw, ch), fits)
            if fits:
                best = plan
                break
            if best is None or not best.fits:
                best = plan
        if best is not None and best.fits:
            return best
    assert best is not None
    if str(sheet).lower() == "auto" and fixed_scale is None:
        best.notes.append("the views do not fit at the requested minimum scale even on the largest allowed sheet; "
                          "drop a view, use a section or detail, or allow a smaller minimum scale")
    else:
        best.notes.append(f"the views need {best.composite_mm[0]:.0f} x {best.composite_mm[1]:.0f} mm at "
                          f"{best.scale} but only {best.free_area[2] - best.free_area[0]:.0f} x "
                          f"{best.free_area[3] - best.free_area[1]:.0f} mm are free on {best.sheet}")
    return best


# ---------------------------------------------------------------------------- expected data from 3D

def infer_cylinder_axes(faces: Iterable[dict[str, Any]], tol: float = 0.05) -> list[dict[str, Any]]:
    """Give an ``axis`` to full cylinders that ``catia_list_faces`` reports without one (seen live on
    360-degree bosses and bores, where GetAxis returns nothing).

    A full cylinder has its centre of gravity on its axis and a length ``L = area / (2 pi r)``. Among the three
    principal directions the axis is the one for which planar faces perpendicular to it exist at both
    ``c.d -/+ L/2`` (the two ends of the boss or bore). Exactly one candidate is accepted, otherwise the
    cylinder is left without axis (and ignored by ``cylinder_circles``). Inferred axes carry
    ``"inferred": True``. Faces are returned as new dicts.
    """
    out = [dict(f) for f in faces]
    normals = {"yz": (1, 0, 0), "zx": (0, 1, 0), "xy": (0, 0, 1)}
    offsets: dict[str, list[float]] = {k: [] for k in normals}
    for f in out:
        par = f.get("parallel_to")
        if f.get("type") == "plane" and par and par.get("plane") in offsets:
            offsets[par["plane"]].append(float(par["offset"]))
    for f in out:
        if f.get("type") != "cylinder" or f.get("axis") or not f.get("radius") or not f.get("area_mm2"):
            continue
        length = float(f["area_mm2"]) / (2 * math.pi * float(f["radius"]))
        c = f["cog"]
        hits = []
        for plane, n in normals.items():
            s = sum(a * b for a, b in zip(c, n))
            if all(any(abs(o - (s + sign * length / 2)) <= tol for o in offsets[plane]) for sign in (-1, 1)):
                hits.append(n)
        if len(hits) == 1:
            f["axis"] = {"direction": list(map(float, hits[0])), "point": list(map(float, c)), "inferred": True}
    return out


def cylinder_circles(faces: Iterable[dict[str, Any]], right: Sequence[float], up: Sequence[float],
                     min_dot: float = 1.0 - 1e-6) -> list[dict[str, Any]]:
    """Circles (model mm in the view axes) expected from the cylindrical faces of ``catia_list_faces``.

    Only cylinders whose axis is parallel to the view direction appear as circles (or arcs, when the
    face is only a part of the cylinder: fillets, half bores). ``faces`` are the dicts under
    ``faces`` in the tool result: ``{"type": "cylinder", "radius", "axis": {"direction", "point"}}``.
    Identical circles (same centre and radius, e.g. both ends of a hole) are merged.
    """
    n = _unit(_cross(right, up))
    out: list[dict[str, Any]] = []
    for f in infer_cylinder_axes(faces):
        if f.get("type") != "cylinder" or not f.get("axis") or not f.get("radius"):
            continue
        d = f["axis"]["direction"]
        nd = math.sqrt(sum(x * x for x in d))
        if nd < 1e-9 or abs(sum(a * b for a, b in zip(d, n)) / nd) < min_dot:
            continue
        u, w = project(f["axis"]["point"], right, up)
        r = float(f["radius"])
        if any(math.hypot(u - c["cx"], w - c["cy"]) < 1e-3 and abs(2 * r - c["d"]) < 1e-3 for c in out):
            continue
        out.append({"cx": u, "cy": w, "d": 2 * r, "label": f"face{f.get('index', len(out) + 1)}"})
    return out


def edge_presence(measured_lines: Sequence[dict[str, Any]], measured_round: Sequence[dict[str, Any]],
                  box: Sequence[float], tol: float = 0.05) -> dict[str, Any]:
    """Is each side of the expected outline box (umin, wmin, umax, wmax) drawn?

    A side is present when a horizontal/vertical line segment lies on it (within ``tol``) or a
    circle/arc touches it (centre -/+ radius). Extra entities elsewhere (dimension lines, text)
    do not matter, unlike a bounding-box comparison.
    """
    umin, wmin, umax, wmax = box
    res: dict[str, Any] = {}
    for side, coord, axis in (("left", umin, "x"), ("right", umax, "x"), ("bottom", wmin, "y"), ("top", wmax, "y")):
        found = None
        for ln in measured_lines:
            if axis == "x" and abs(ln["x0"] - coord) <= tol and abs(ln["x1"] - coord) <= tol:
                found = ("line", ln["x0"])
                break
            if axis == "y" and abs(ln["y0"] - coord) <= tol and abs(ln["y1"] - coord) <= tol:
                found = ("line", ln["y0"])
                break
        if found is None:
            for c in measured_round:
                ext = (c["cx"] - c["r"] if side == "left" else c["cx"] + c["r"] if side == "right"
                       else c["cy"] - c["r"] if side == "bottom" else c["cy"] + c["r"])
                if abs(ext - coord) <= tol:
                    found = ("circle", ext)
                    break
        res[side] = {"expected": round(coord, 4), "found": found is not None,
                     "measured": None if found is None else round(found[1], 4), "by": None if found is None else found[0]}
    res["ok"] = all(v["found"] for v in res.values() if isinstance(v, dict))
    return res


def check_view_pdf(pdf: str, frame: ViewFrame, *, page: int = 0, expected_circles: Sequence[dict[str, Any]] = (),
                   expected_box: Sequence[float] | None = None, d_tol: float = 0.02, centre_tol: float = 0.05,
                   edge_tol: float = 0.05, min_diameter: float = 2.0, pad_mm: float = 20.0) -> dict[str, Any]:
    """Closed-loop check of ONE view of an exported PDF against 3D-derived expectations.

    ``frame``: the view as CATIA reports it (position, scale, projected origin). Circles are matched
    one-to-one by centre (diameter tolerance ``d_tol`` = 0.02 mm, centre ``centre_tol`` = 0.05 mm);
    an arc drawn with the right centre and radius satisfies an expected cylinder that is only a part
    of a circle. ``min_diameter`` (default 2 mm) skips glyph-sized circles (text is drawn as
    strokes). ``expected_box`` = projected 3D bounding box (umin, wmin, umax, wmax) in view mm; the
    PDF region examined is that box (on paper) grown by ``pad_mm``.
    """
    k = frame.k()
    origin = frame.origin_paper()
    if expected_box is not None:
        px0 = origin[0] + expected_box[0] * frame.scale
        px1 = origin[0] + expected_box[2] * frame.scale
        py0 = origin[1] - expected_box[3] * frame.scale
        py1 = origin[1] - expected_box[1] * frame.scale
        region = [px0 - pad_mm, py0 - pad_mm, px1 + pad_mm, py1 + pad_mm]
    else:
        region = frame.region_paper(150.0, 150.0)
    res = _geo.extract(pdf, page, region, k, origin)
    rounds: list[dict[str, Any]] = [c for c in res["circles"] if c["d"] >= min_diameter]
    rounds += [dict(a, d=2 * a["r"]) for a in res["arcs"] if 2 * a["r"] >= min_diameter]
    for c in polyline_circles(pdf, page, region, k, origin, min_r=min_diameter / 2 / k):
        c = dict(c, d=2 * c["r"])
        if c["d"] < min_diameter:
            continue
        if not any(math.hypot(c["cx"] - f["cx"], c["cy"] - f["cy"]) < 0.05 and abs(c["r"] - f["r"]) < 0.05
                   for f in rounds):
            rounds.append(c)
    rep = match_circles(expected_circles, rounds, d_tol=d_tol, centre_tol=centre_tol)
    worst_d, worst_c = rep.worst()
    out: dict[str, Any] = {
        "circles": {
            "ok": rep.ok,
            "expected": len(expected_circles),
            "matched": sum(1 for m in rep.matches if m.ok),
            "worst_diameter_error_mm": None if not rep.matches or worst_d != worst_d else round(worst_d, 4),
            "worst_centre_error_mm": None if not rep.matches or worst_c != worst_c else round(worst_c, 4),
            "details": [{"label": m.expected.get("label"),
                         "expected": [round(m.expected["cx"], 3), round(m.expected["cy"], 3), round(m.expected["d"], 3)],
                         "measured": None if m.measured is None else
                         [round(m.measured["cx"], 3), round(m.measured["cy"], 3), round(m.measured["d"], 3)],
                         "ok": m.ok} for m in rep.matches],
            "unexpected": [[round(m["cx"], 3), round(m["cy"], 3), round(m["d"], 3)] for m in rep.unexpected],
        },
        "pitches": [{"between": p["between"], "expected": round(p["expected"], 4),
                     "measured": round(p["measured"], 4), "error": round(p["error"], 4)} for p in pitch_errors(rep)],
    }
    out["pitches_ok"] = all(abs(p["error"]) <= 2 * centre_tol for p in out["pitches"])
    if expected_box is not None:
        out["outline"] = edge_presence(res["lines"], rounds, expected_box, edge_tol)
    out["ok"] = bool(out["circles"]["ok"] and out["pitches_ok"] and out.get("outline", {"ok": True})["ok"])
    return out
