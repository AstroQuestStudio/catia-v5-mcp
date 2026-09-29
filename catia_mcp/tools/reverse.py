"""Reverse engineering of a CATIA model: describe it (replayable spec + script), audit it, measure it.

Three read-only tools, working on the ACTIVE document:

* ``catia_describe_model``: walks a CATPart (bodies, features in tree order, sketches with exact
  geometry and constraints, parameters, measurements) or a CATProduct (components, poses,
  constraints, statuses) and returns a JSON specification. For a part it also returns the source of a
  ``PartScript`` that rebuilds the part. Anything the reader does not understand is reported as an
  explicit *opaque* feature (name, type, what is known, why): it is never guessed and never silently
  dropped, and a spec with opaque solid features is never presented as complete.
* ``catia_audit_model``: rule-based review of the tree WITHOUT modifying it, findings ranked by
  severity, each with the exact tool calls that fix it (or ``manual`` when no tool can).
* ``catia_measure_model``: volume, area, centre of gravity, bounding box and inertia of each body,
  to compare an original with its replay.

Layers, from the pure ones to the COM ones (everything above ``ModelReader`` runs without CATIA):

1. geometry helpers: sketch frame -> origin-plane frame, profile chaining, name sanitising;
2. ``build_spec``: raw reader output -> spec + replay operations (``ops``) + opaque list;
3. ``render_script`` / ``apply_ops``: operations -> PartScript source / real PartScript object;
4. ``audit_part`` / ``audit_product``: rules on the raw reader output;
5. ``ModelReader``: the only code that talks to CATIA (COM properties + VBScript for the ByRef arrays
   that pywin32 cannot read).

Every property name and enumeration value used here was read from the CATIA V5 R19 type libraries
(``MECMOD``, ``PARTITF``, ``KnowledgewareTypeLib``, ``SPATypeLib``) and proven on a live session; the
few remaining assumptions are marked ``UNVERIFIED-LIVE``.
"""

from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from catia_mcp import geometry
from catia_mcp.connection import CATIAConnection
from catia_mcp.naming import is_default_name
from catia_mcp.scripting import PartScript, ScriptError, check_name

FORMAT = "catia-mcp-model-spec/1"
PLANES = ("xy", "yz", "zx")
# Origin plane frames CATIA uses for sketches: (H axis, V axis, normal).
STD = {
    "xy": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "yz": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "zx": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
}
SEVERITIES = ("error", "warning", "info")
TOL_MM = 1e-5          # two sketch points closer than this are the same vertex
_STRIDE = 15           # values per sketch element in _SKETCH_VBS

# CatConstraintType (MECMOD type library)
CST_TYPES = {
    0: "reference", 1: "distance", 2: "on", 3: "concentricity", 4: "tangency", 5: "length", 6: "angle",
    7: "planar_angle", 8: "parallelism", 9: "axis_parallelism", 10: "horizontality", 11: "perpendicularity",
    12: "axis_perpendicularity", 13: "verticality", 14: "radius", 15: "symmetry", 16: "midpoint",
    17: "equidistance", 18: "major_radius", 19: "minor_radius", 20: "surface_contact", 21: "line_contact",
    22: "point_contact", 23: "chamfer", 24: "chamfer_perpendicular", 25: "annular_contact",
    26: "cylinder_radius", 27: "st_continuity", 28: "st_distance", 29: "sd_continuity", 30: "sd_shape",
}
CST_STATUS = {0: "OK", 1: "not satisfied", 2: "wrong orientation or side", 3: "wrong value",
              4: "wrong geometry type", 5: "broken"}
CST_MODE = {0: "driving", 1: "driven"}

# interface name (as returned by the type library) -> kind used in the spec
KIND_OF_TYPE = {
    "Pad": "pad", "Pocket": "pocket", "Shaft": "shaft", "Groove": "groove", "Hole": "hole",
    "ConstRadEdgeFillet": "fillet", "Chamfer": "chamfer", "Assemble": "boolean", "Add": "boolean",
    "Remove": "boolean", "Intersect": "boolean", "Trim": "boolean", "Mirror": "mirror",
    "RectPattern": "rect_pattern", "CircPattern": "circ_pattern", "Shell": "shell", "Draft": "draft",
    "Thickness": "thickness", "Thread": "thread",
}
BOOLEAN_OPS = {"Assemble": "assemble", "Add": "add", "Remove": "remove", "Intersect": "intersect"}
DRESS_UP = {"fillet", "chamfer", "shell", "draft", "thickness"}
ADDS_MATERIAL = {"pad", "shaft"}
SKETCH_BASED = {"pad", "pocket", "shaft", "groove"}

# --------------------------------------------------------------------------------------------
# VBScript snippets. Every ByRef array output (coordinates, matrices) comes back as zeros when read
# from Python through pywin32 (lesson L006), so they are read inside CATIA, like geometry.py does.
# --------------------------------------------------------------------------------------------

_SKETCH_VBS = """
Function CATMain(sk)
  ' out(0..8) = absolute axis data (origin, H, V); then 15 values per geometric element:
  ' type, construction, 12 numbers (depend on the type), name.
  Dim ax(8)
  sk.GetAbsoluteAxisData ax
  Dim geo: Set geo = sk.GeometricElements
  Dim n: n = geo.Count
  Dim out(): ReDim out(9 + 15 * n - 1)
  Dim k
  For k = 0 To UBound(out)
    out(k) = 0
  Next
  For k = 0 To 8
    out(k) = ax(k)
  Next
  Dim q, e, t, base, c(1), s(1), en(1), pe(1), m(1)
  For q = 1 To n
    Set e = geo.Item(q)
    base = 9 + 15 * (q - 1)
    t = e.GeometricType
    out(base) = t
    On Error Resume Next
    out(base + 14) = e.Name
    out(base + 1) = 0
    If e.Construction Then out(base + 1) = 1
    If t = 2 Or t = 4 Then
      e.GetCoordinates c
      out(base + 2) = c(0): out(base + 3) = c(1)
    ElseIf t = 3 Then
      e.StartPoint.GetCoordinates s
      e.EndPoint.GetCoordinates en
      out(base + 2) = s(0): out(base + 3) = s(1): out(base + 4) = en(0): out(base + 5) = en(1)
    ElseIf t = 5 Then
      e.GetCenter c
      out(base + 2) = c(0): out(base + 3) = c(1): out(base + 4) = e.Radius
      e.GetParamExtents pe
      out(base + 5) = pe(0): out(base + 6) = pe(1)
      e.StartPoint.GetCoordinates s
      e.EndPoint.GetCoordinates en
      out(base + 7) = s(0): out(base + 8) = s(1): out(base + 9) = en(0): out(base + 10) = en(1)
    ElseIf t = 8 Then
      e.GetCenter c
      out(base + 2) = c(0): out(base + 3) = c(1)
      out(base + 4) = e.MajorRadius: out(base + 5) = e.MinorRadius
      e.GetMajorAxis m
      out(base + 6) = m(0): out(base + 7) = m(1)
      e.GetParamExtents pe
      out(base + 8) = pe(0): out(base + 9) = pe(1)
    ElseIf t = 9 Then
      out(base + 2) = e.GetNumberOfControlPoints
    End If
    Err.Clear
    On Error GoTo 0
  Next
  CATMain = out
End Function
"""

_INERTIA_VBS = """
Function CATMain(doc, body)
  ' out: 0-8 inertia matrix at the centre of gravity (kg.m2), 9-11 COG (m), 12-14 principal moments,
  ' 15-23 principal axes, 24 mass (kg). The Inertia object is removed again (nothing is left behind).
  Dim spa: Set spa = doc.GetWorkbench("SPAWorkbench")
  Dim ine: Set ine = spa.Inertias.Add(body)
  Dim m(8), c(2), pm(2), ax(8), k
  Dim out(24)
  For k = 0 To 24
    out(k) = 0
  Next
  On Error Resume Next
  ine.GetInertiaMatrix m
  ine.GetCOGPosition c
  ine.GetPrincipalMoments pm
  ine.GetPrincipalAxes ax
  For k = 0 To 8: out(k) = m(k): Next
  For k = 0 To 2: out(9 + k) = c(k): Next
  For k = 0 To 2: out(12 + k) = pm(k): Next
  For k = 0 To 8: out(15 + k) = ax(k): Next
  out(24) = ine.Mass
  Err.Clear
  spa.Inertias.Remove spa.Inertias.Count
  CATMain = out
End Function
"""

_SHOW_VBS = """
Function CATMain(doc, obj)
  ' 0 = shown, 1 = hidden (CatVisPropertyShow)
  Dim sel: Set sel = doc.Selection
  sel.Clear
  sel.Add obj
  Dim s: s = 99
  sel.VisProperties.GetShow s
  sel.Clear
  CATMain = Array(s)
End Function
"""

_EDGE_VBS = """
Function CATMain(doc, ref)
  ' length, then start / middle / end point of an edge (mm)
  Dim spa: Set spa = doc.GetWorkbench("SPAWorkbench")
  Dim m: Set m = spa.GetMeasurable(ref)
  Dim p(8), k
  m.GetPointsOnCurve p
  Dim out(9)
  out(0) = m.Length
  For k = 0 To 8: out(1 + k) = p(k): Next
  CATMain = out
End Function
"""

_HOLE_VBS = """
Function CATMain(h)
  ' origin (3) and direction (3) of a hole, in mm
  Dim o(2), d(2), k
  Dim out(5)
  For k = 0 To 5
    out(k) = 0
  Next
  h.GetOrigin o
  For k = 0 To 2: out(k) = o(k): Next
  On Error Resume Next
  h.GetDirection d
  For k = 0 To 2: out(3 + k) = d(k): Next
  CATMain = out
End Function
"""

_POSITION_VBS = """
Function CATMain(prod)
  ' 12 numbers: 3 axes (columns of the rotation) then the origin (mm)
  Dim a(11)
  prod.Position.GetComponents a
  CATMain = a
End Function
"""


# ============================================================================================
# 1. Pure helpers
# ============================================================================================


def _r(x: float, nd: int = 6) -> float:
    """Round for output; never -0.0."""
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return default if x is None else float(x)
    except (TypeError, ValueError):
        return default


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cross(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(v: Sequence[float]) -> list[float]:
    n = math.sqrt(_dot(v, v))
    return [c / n for c in v] if n > 1e-12 else [0.0, 0.0, 0.0]


def frame_to_plane(axis_data: Sequence[float], tol: float = 1e-6) -> dict[str, Any] | None:
    """Express a sketch frame (origin, H, V) on an origin plane, or None if it is tilted.

    Returns ``{"plane", "offset", "normal_sign", "matrix"}``: the origin plane whose normal is parallel to
    the sketch normal (H x V), the signed offset of the sketch origin along that normal, +1/-1 when the
    sketch normal is parallel/anti-parallel to the plane normal, and the affine map from sketch (x, y) to
    the (x', y') of a sketch drawn on ``plane`` offset by ``offset`` with CATIA's default axes:
    ``x' = mx[0] + mx[1]*x + mx[2]*y`` and ``y' = my[0] + my[1]*x + my[2]*y``.
    """
    o = [_f(v) for v in axis_data[0:3]]
    h = _unit([_f(v) for v in axis_data[3:6]])
    v = _unit([_f(x) for x in axis_data[6:9]])
    n = _cross(h, v)
    if _dot(n, n) < 0.5:
        return None
    for plane, (hs, vs, ns) in STD.items():
        s = _dot(n, ns)
        if abs(abs(s) - 1.0) < tol:
            return {
                "plane": plane,
                "offset": _r(_dot(o, ns)),
                "normal_sign": 1 if s > 0 else -1,
                "matrix": {
                    "x": [_dot(o, hs), _dot(h, hs), _dot(v, hs)],
                    "y": [_dot(o, vs), _dot(h, vs), _dot(v, vs)],
                },
            }
    return None


def map_point(m: dict[str, Any], x: float, y: float) -> list[float]:
    mx, my = m["x"], m["y"]
    return [_r(mx[0] + mx[1] * x + mx[2] * y), _r(my[0] + my[1] * x + my[2] * y)]


def is_reflection(m: dict[str, Any]) -> bool:
    """True when the sketch map flips orientation (arc directions must then be swapped)."""
    return (m["x"][1] * m["y"][2] - m["x"][2] * m["y"][1]) < 0


def _same(p: Sequence[float], q: Sequence[float], tol: float = TOL_MM * 100) -> bool:
    return math.dist(p, q) <= tol


def sanitize_name(name: str) -> str:
    """ASCII, letters/digits/'_.-' only (what check_name accepts), never empty."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    out = re.sub(r"[^A-Za-z0-9_.\-]+", "_", ascii_name).strip("_.-")
    return out or "Item"


def replay_name(orig: str, used: set[str], what: str = "name") -> str:
    """A name the DSL accepts (explicit, not a CATIA default, unique), derived from ``orig``.

    A name that is already fine is kept as is; otherwise ``_Rebuilt`` (and a counter) is appended, so
    the mapping stays readable ('Pad.1' -> 'Pad.1_Rebuilt', 'PartBody' -> 'PartBody_Rebuilt').
    """
    base = sanitize_name(orig)
    candidates = [base] + [f"{base}_Rebuilt" + (f"_{k}" if k > 1 else "") for k in range(1, 50)]
    for cand in candidates:
        try:
            check_name(cand, what, used)
            used.add(cand)
            return cand
        except ScriptError:
            continue
    raise ScriptError(f"cannot derive a replay name from {orig!r}")


# ---- sketch elements -> replay geometry ----------------------------------------------------


def classify_elements(elements: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group raw sketch elements: curves that can be replayed vs. what cannot.

    ``curves`` (line/arc/circle, not construction), ``construction`` (any construction curve),
    ``points`` (free points), ``unsupported`` (ellipse, spline, conics...). Vertices of curves (their end
    and centre points, which CATIA lists as construction Point2D) are dropped as implicit.
    """
    els = list(elements)
    curves = [e for e in els if e["kind"] in ("line", "circle", "arc")]
    anchors: list[list[float]] = []
    for e in curves:
        if e["kind"] == "line":
            anchors += [e["start"], e["end"]]
        elif e["kind"] == "arc":
            anchors += [e["start"], e["end"], e["center"]]
        else:
            anchors.append(e["center"])
    for e in els:
        if e["kind"] in ("ellipse", "spline"):
            anchors.append(e.get("center") or [0.0, 0.0])
    out: dict[str, list[dict[str, Any]]] = {"curves": [], "construction": [], "points": [], "unsupported": [],
                                            "implicit_points": []}
    for e in els:
        k = e["kind"]
        if k in ("line", "circle", "arc"):
            (out["construction"] if e["construction"] else out["curves"]).append(e)
        elif k == "point":
            # a Point2D that sits on a curve end / centre is that vertex; a free one is real content
            if any(_same(e["at"], a) for a in anchors) and e["construction"]:
                out["implicit_points"].append(e)
            else:
                out["points"].append(e)
        elif k in ("ellipse", "spline", "other"):
            out["unsupported"].append(e)
    return out


def _map_curves(curves: Iterable[dict[str, Any]], m: dict[str, Any]) -> list[dict[str, Any]]:
    """Curves expressed in the replay sketch frame. Arcs become (start, end, centre, ccw) with the
    orientation corrected when the map is a reflection."""
    flip = is_reflection(m)
    out: list[dict[str, Any]] = []
    for e in curves:
        if e["kind"] == "line":
            out.append({"kind": "line", "start": map_point(m, *e["start"]), "end": map_point(m, *e["end"]),
                        "src": e["name"]})
        elif e["kind"] == "circle":
            out.append({"kind": "circle", "center": map_point(m, *e["center"]), "radius": _r(e["radius"]),
                        "construction": e["construction"], "src": e["name"]})
        elif e["kind"] == "arc":
            out.append({"kind": "arc", "start": map_point(m, *e["start"]), "end": map_point(m, *e["end"]),
                        "center": map_point(m, *e["center"]), "radius": _r(e["radius"]),
                        "ccw": not flip, "src": e["name"]})
    return out


def chain_profiles(curves: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Chain lines and arcs (replay frame) into connected profiles.

    Returns ``(chains, leftovers)``. A chain is ``{"start", "segments", "closed"}`` (segments in the
    ``catia_sketch_profile`` format). ``leftovers`` are the curves that could not be chained without
    guessing (a vertex shared by three or more curves); they are replayed one by one.
    """
    segs = [c for c in curves if c["kind"] in ("line", "arc")]
    nodes: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def key(p: Sequence[float]) -> tuple[int, int]:
        return (round(p[0] / 1e-4), round(p[1] / 1e-4))

    for i, s in enumerate(segs):
        nodes.setdefault(key(s["start"]), []).append((i, 0))
        nodes.setdefault(key(s["end"]), []).append((i, 1))
    branch = {k for k, v in nodes.items() if len(v) > 2}
    bad = {i for k in branch for (i, _) in nodes[k]}
    used: set[int] = set(bad)
    chains: list[dict[str, Any]] = []

    def other_end(i: int, end: int) -> list[float]:
        return segs[i]["end" if end == 0 else "start"]

    def build(i0: int, start_end: int) -> dict[str, Any]:
        """Walk from segment i0 entered at ``start_end`` (0 = its start point)."""
        order: list[tuple[int, int]] = []
        i, e = i0, start_end
        while True:
            used.add(i)
            order.append((i, e))
            nxt_pt = other_end(i, e)
            cands = [(j, k) for (j, k) in nodes.get(key(nxt_pt), []) if j not in used]
            if not cands:
                closing = _same(nxt_pt, segs[i0]["start" if start_end == 0 else "end"])
                break
            i, e = cands[0]
        first = segs[order[0][0]]
        start = first["start" if order[0][1] == 0 else "end"]
        out_segs: list[dict[str, Any]] = []
        for (j, ent) in order:
            s = segs[j]
            to = s["end" if ent == 0 else "start"]
            if s["kind"] == "line":
                out_segs.append({"type": "line", "to": list(to)})
            else:
                ccw = s["ccw"] if ent == 0 else not s["ccw"]
                out_segs.append({"type": "arc", "to": list(to), "center": list(s["center"]),
                                 "direction": "ccw" if ccw else "cw"})
        return {"start": list(start), "segments": out_segs, "closed": closing, "src": [segs[j]["src"] for j, _ in order]}

    # closed loops and open chains: start open chains at a dangling end first
    dangling = [(i, e) for k, v in nodes.items() if len(v) == 1 and k not in branch for (i, e) in v]
    for (i, e) in dangling:
        if i not in used:
            chains.append(build(i, e))
    for i in range(len(segs)):
        if i not in used:
            chains.append(build(i, 0))
    leftovers = [segs[i] for i in sorted(bad)]
    return chains, leftovers


def open_ends(elements: Iterable[dict[str, Any]]) -> list[list[float]]:
    """Vertices of a sketch's (non-construction) lines and arcs that belong to only one curve: an
    empty list means every profile is closed (circles are closed by nature)."""
    ends: list[tuple[list[float], int]] = []

    def add(p: Sequence[float]) -> None:
        for k, (q, n) in enumerate(ends):
            if _same(p, q, 1e-4):
                ends[k] = (q, n + 1)
                return
        ends.append((list(p), 1))

    for e in elements:
        if e.get("construction"):
            continue
        if e["kind"] == "line":
            add(e["start"]); add(e["end"])
        elif e["kind"] == "arc":
            add(e["start"]); add(e["end"])
    return [p for p, n in ends if n == 1]


# ============================================================================================
# 2. Spec building (pure): raw reader output -> spec, replay operations, opaque list
# ============================================================================================


class _Ctx:
    """State of one ``build_spec`` run."""

    def __init__(self, raw: dict[str, Any], replay_part_name: str) -> None:
        self.raw = raw
        self.used: set[str] = set()
        self.names: dict[str, str] = {}          # original name -> replay name (per kind:name key)
        self.ops: list[dict[str, Any]] = []
        self.opaque: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.spec_bodies: list[dict[str, Any]] = []
        self.sketch_specs: dict[str, dict[str, Any]] = {}
        self.emitted_sketch_names: set[str] = set()
        self.hole_internal: set[str] = set()
        self.part_name = replay_part_name
        self.unmerged: list[str] = []
        self.material_bodies: set[str] = set()


def _sketch_spec(sk: dict[str, Any]) -> dict[str, Any]:
    """The sketch as written in the spec: exact geometry in its own axes, constraints by element id."""
    ids = {e["name"]: i + 1 for i, e in enumerate(sk["elements"])}
    csts = []
    for c in sk["constraints"]:
        csts.append({
            "name": c["name"], "type": c["type_name"], "type_code": c["type"], "mode": c["mode"],
            "status": c["status"], "value": c.get("value"),
            "elements": [ids.get(n, n) for n in c["elements"]],
            "element_names": c["elements"],
        })
    frame = sk["axis"]
    return {
        "name": sk["name"], "visible": sk.get("visible"),
        "frame": {"origin": [_r(v) for v in frame[0:3]], "h": [_r(v) for v in frame[3:6]],
                  "v": [_r(v) for v in frame[6:9]]},
        "geometry": _geometry_list(sk, ids),
        "constraints": csts,
        "center_line": sk.get("center_line"),
        "dof": {"state": "unknown",
                "reason": "the Automation API exposes no degrees-of-freedom analysis of a sketch",
                "constraint_count": len(sk["constraints"]),
                "broken_constraints": sk.get("broken_constraints"),
                "unupdated_constraints": sk.get("unupdated_constraints")},
    }


def _geometry_list(sk: dict[str, Any], ids: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for e in sk["elements"]:
        g: dict[str, Any] = {"id": ids[e["name"]], "name": e["name"], "type": e["kind"],
                             "construction": e["construction"]}
        if e["kind"] == "line":
            g.update(start=[_r(v) for v in e["start"]], end=[_r(v) for v in e["end"]])
        elif e["kind"] == "circle":
            g.update(center=[_r(v) for v in e["center"]], radius=_r(e["radius"]))
        elif e["kind"] == "arc":
            g.update(center=[_r(v) for v in e["center"]], radius=_r(e["radius"]),
                     start=[_r(v) for v in e["start"]], end=[_r(v) for v in e["end"]],
                     start_angle_deg=_r(math.degrees(e["a0"]), 4), end_angle_deg=_r(math.degrees(e["a1"]), 4),
                     direction="ccw")
        elif e["kind"] == "point":
            g.update(at=[_r(v) for v in e["at"]])
        elif e["kind"] == "ellipse":
            g.update(center=[_r(v) for v in e["center"]], major_radius=_r(e["major_radius"]),
                     minor_radius=_r(e["minor_radius"]), major_axis=[_r(v) for v in e["major_axis"]],
                     recognised=False, reason="no sketch tool draws an ellipse")
        elif e["kind"] == "spline":
            g.update(control_points=e.get("n_control_points"), recognised=False,
                     reason="control points cannot be read back exactly through Automation")
        out.append(g)
    return out


class _Unrecognised(Exception):
    """A feature cannot be replayed faithfully: carries the reason (goes to the opaque list)."""


def _sketch_ops(ctx: _Ctx, sk: dict[str, Any], axis: dict[str, Any] | None = None,
                purpose: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """Operations that redraw sketch ``sk`` on an origin plane. Returns (op, plane info).

    ``axis`` (shaft/groove) = the revolution axis to draw. Raises ``_Unrecognised`` when the sketch
    cannot be reproduced without guessing.
    """
    plane = frame_to_plane(sk["axis"])
    if plane is None:
        raise _Unrecognised(f"sketch '{sk['name']}' lies on a plane that is not parallel to an origin plane "
                            "(the DSL only sketches on origin planes and offsets of them)")
    groups = classify_elements(sk["elements"])
    if groups["unsupported"]:
        kinds = sorted({e["kind"] for e in groups["unsupported"]})
        raise _Unrecognised(f"sketch '{sk['name']}' contains unsupported geometry: {', '.join(kinds)} "
                            "(no sketch tool reproduces it exactly)")
    if not groups["curves"] and not groups["points"] and not any(
            c["kind"] == "circle" for c in groups["construction"]):
        raise _Unrecognised(f"sketch '{sk['name']}' has no replayable profile geometry")
    m = plane["matrix"]
    mapped = _map_curves(groups["curves"], m)
    circles = [c for c in mapped if c["kind"] == "circle"]
    chains, leftovers = chain_profiles([c for c in mapped if c["kind"] in ("line", "arc")])
    elements: list[dict[str, Any]] = []
    for c in circles:
        elements.append({"kind": "circle", "cx": c["center"][0], "cy": c["center"][1], "r": c["radius"],
                         "construction": False})
    for ch in chains:
        segs = ch["segments"]
        if ch["closed"]:
            elements.append({"kind": "profile", "start": ch["start"], "segments": segs, "closed": True})
        elif len(segs) == 1 and segs[0]["type"] == "line":
            elements.append({"kind": "line", "x1": ch["start"][0], "y1": ch["start"][1],
                             "x2": segs[0]["to"][0], "y2": segs[0]["to"][1], "axis": False})
        else:
            elements.append({"kind": "profile", "start": ch["start"], "segments": segs, "closed": False})
    for c in leftovers:
        if c["kind"] == "line":
            elements.append({"kind": "line", "x1": c["start"][0], "y1": c["start"][1],
                             "x2": c["end"][0], "y2": c["end"][1], "axis": False})
        else:
            a0 = math.degrees(math.atan2(c["start"][1] - c["center"][1], c["start"][0] - c["center"][0]))
            a1 = math.degrees(math.atan2(c["end"][1] - c["center"][1], c["end"][0] - c["center"][0]))
            if not c["ccw"]:
                a0, a1 = a1, a0
            elements.append({"kind": "arc", "cx": c["center"][0], "cy": c["center"][1], "r": c["radius"],
                             "a0": _r(a0, 6), "a1": _r(a1, 6)})
    for cc in groups["construction"]:
        if cc["kind"] == "circle":
            mc = _map_curves([cc], m)[0]
            elements.append({"kind": "circle", "cx": mc["center"][0], "cy": mc["center"][1], "r": mc["radius"],
                             "construction": True})
    dropped = [c for c in groups["construction"] if c["kind"] != "circle"]
    if dropped:
        ctx.notes.append(f"sketch '{sk['name']}': {len(dropped)} construction line/arc element(s) are not replayed "
                         "(no tool draws construction lines); they do not shape the solid")
    for p in groups["points"]:
        elements.append({"kind": "point", "x": map_point(m, *p["at"])[0], "y": map_point(m, *p["at"])[1]})
    if axis is not None:
        elements.append(axis)
    base = sanitize_name(sk["name"])
    name = replay_name(base if not purpose else f"{base}", ctx.used, "sketch name")
    ctx.emitted_sketch_names.add(sk["name"])
    ctx.names["sketch:" + sk["name"]] = name
    op = {"op": "sketch", "name": name, "plane": plane["plane"],
          "offset": plane["offset"] if abs(plane["offset"]) > 1e-9 else None,
          "elements": elements, "source": sk["name"]}
    return op, plane


def _revolve_axis(sk: dict[str, Any], plane: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """How to reproduce the revolution axis: ("h"|"v", None) or ("sketch_line", axis line element)."""
    cl = sk.get("center_line")
    if not cl or cl.get("kind") not in ("h", "v", "line"):
        raise _Unrecognised(f"sketch '{sk['name']}' has no readable revolution axis")
    m = plane["matrix"]
    if cl["kind"] in ("h", "v"):
        # the absolute axis of the ORIGINAL sketch: origin (0, 0), direction (1, 0) or (0, 1)
        p0 = map_point(m, 0.0, 0.0)
        p1 = map_point(m, 1.0, 0.0) if cl["kind"] == "h" else map_point(m, 0.0, 1.0)
        d = [p1[0] - p0[0], p1[1] - p0[1]]
    else:
        el = next((e for e in sk["elements"] if e["name"] == cl["name"] and e["kind"] == "line"), None)
        if el is None:
            raise _Unrecognised(f"revolution axis '{cl['name']}' of sketch '{sk['name']}' is not a line")
        p0, p1 = map_point(m, *el["start"]), map_point(m, *el["end"])
        d = [p1[0] - p0[0], p1[1] - p0[1]]
    n = math.hypot(*d)
    if n < 1e-9:
        raise _Unrecognised("revolution axis has zero length")
    d = [d[0] / n, d[1] / n]
    if abs(d[1]) < 1e-9 and abs(p0[1]) < 1e-6:
        return "h", None
    if abs(d[0]) < 1e-9 and abs(p0[0]) < 1e-6:
        return "v", None
    length = max(10.0, 1.0)
    q1 = [_r(p0[0] + d[0] * length), _r(p0[1] + d[1] * length)]
    return "sketch_line", {"kind": "line", "x1": _r(p0[0]), "y1": _r(p0[1]), "x2": q1[0], "y2": q1[1], "axis": True}


def _get_sketch(body: dict[str, Any], name: str, ctx: _Ctx) -> dict[str, Any]:
    for src in (body,) + tuple(ctx.raw.get("_all_bodies", ())):
        for sk in src["sketches"]:
            if sk["name"] == name:
                return sk
    raise _Unrecognised(f"sketch '{name}' not found in the body")


def _pad_ops(ctx: _Ctx, feat: dict[str, Any], sk_op: dict[str, Any], plane: dict[str, Any]) -> dict[str, Any]:
    d = feat["data"]
    if d.get("thin"):
        raise _Unrecognised("thin pad (thickness) has no equivalent in the tools")
    if d.get("direction_type") not in (0, None):
        raise _Unrecognised("extrusion direction is not normal to the sketch")
    if d.get("l1_mode") != 0:
        raise _Unrecognised(f"first limit mode {d.get('l1_mode')} (not a plain length) has no equivalent in catia_pad")
    length = _f(d.get("l1_value"))
    second = _f(d.get("l2_value"))
    sym = bool(d.get("symmetric"))
    if second > 1e-9 and d.get("l2_mode") not in (0, None):
        raise _Unrecognised(f"second limit mode {d.get('l2_mode')} has no equivalent in catia_pad")
    # extrusion vector = sketch normal * (+1 regular, -1 inverse); replay direction relative to the replay normal
    sign = plane["normal_sign"] * (1 if d.get("orientation", 0) == 0 else -1)
    op: dict[str, Any] = {"op": "pad", "sketch": sk_op["name"]}
    if sym:
        op.update(height=_r(2 * length), symmetric=True)
    elif second > 1e-9:
        if sign < 0:
            raise _Unrecognised("two-limit pad extruded against the sketch normal: catia_pad ignores the direction "
                                "when a second height is given")
        op.update(height=_r(length), second_height=_r(second))
    else:
        op.update(height=_r(length))
        if sign < 0:
            op["direction"] = "reverse"
    if length <= 0:
        raise _Unrecognised("pad length is zero")
    return op


def _pocket_ops(ctx: _Ctx, feat: dict[str, Any], sk_op: dict[str, Any], plane: dict[str, Any]) -> dict[str, Any]:
    d = feat["data"]
    if d.get("thin"):
        raise _Unrecognised("thin pocket has no equivalent in the tools")
    if d.get("direction_type") not in (0, None):
        raise _Unrecognised("cutting direction is not normal to the sketch")
    if d.get("symmetric"):
        raise _Unrecognised("symmetric pocket has no equivalent in catia_pocket")
    mode = d.get("l1_mode")
    limit = {0: "dimension", 1: "up_to_next", 2: "up_to_last"}.get(mode)
    if limit is None:
        raise _Unrecognised(f"first limit mode {mode} has no equivalent in catia_pocket")
    if _f(d.get("l2_value")) > 1e-9:
        raise _Unrecognised("pocket with a second limit has no equivalent in catia_pocket")
    op: dict[str, Any] = {"op": "pocket", "sketch": sk_op["name"], "limit": limit}
    if limit == "dimension":
        depth = _f(d.get("l1_value"))
        if depth <= 0:
            raise _Unrecognised("pocket depth is zero")
        op["depth"] = _r(depth)
    # DirectionOrientation is relative to the sketch normal (0 along, 1 against); catia_pocket's
    # "normal" keeps CATIA's default (1) and "reverse" flips it. UNVERIFIED-LIVE for offset/flipped planes.
    orientation = d.get("orientation", 1)
    physical = (1 if orientation == 0 else -1) * plane["normal_sign"]   # cut direction vs the replay normal
    replay_orientation = 0 if physical > 0 else 1
    op["direction"] = "normal" if replay_orientation == 1 else "reverse"
    return op


def _hole_op(feat: dict[str, Any]) -> dict[str, Any]:
    d = feat["data"]
    if d.get("anchor_mode") not in (0, None):
        raise _Unrecognised("hole anchored at its middle point: the origin is not on the entry face")
    if d.get("bottom_type") not in (0, None):
        raise _Unrecognised("hole with a V or trimmed bottom has no equivalent in catia_hole")
    mode = d.get("bottom_limit_mode")
    if mode not in (0, 3):
        raise _Unrecognised(f"hole limit mode {mode} (neither a depth nor 'up to plane') has no equivalent in "
                            "catia_hole")
    if mode == 3 and not d.get("up_to_point"):
        raise _Unrecognised("hole goes up to a face whose position could not be read (only planar limits are "
                            "designated by a point inside the face)")
    if not d.get("origin"):
        raise _Unrecognised("hole origin could not be read")
    typ = d.get("type")
    op: dict[str, Any] = {"op": "hole", "point": [_r(v) for v in d["origin"]], "diameter": _r(d["diameter"])}
    if mode == 3:
        op["up_to_point"] = [_r(v) for v in d["up_to_point"]]
    else:
        op["depth"] = _r(d["depth"])
    if typ == 0:
        op["kind"] = "simple"
    elif typ == 1:
        if d.get("head_angle") is None:
            raise _Unrecognised("tapered hole angle could not be read")
        op.update(kind="tapered", taper_angle=_r(d["head_angle"]))
    elif typ == 2:
        if d.get("head_diameter") is None or d.get("head_depth") is None:
            raise _Unrecognised("counterbore dimensions could not be read")
        op.update(kind="counterbored", head_diameter=_r(d["head_diameter"]), head_depth=_r(d["head_depth"]))
    elif typ == 3:
        ang, hd = d.get("head_angle"), d.get("head_depth")
        if ang is None or hd is None:
            raise _Unrecognised("countersink dimensions could not be read")
        head_d = d["diameter"] + 2 * hd * math.tan(math.radians(ang / 2))
        op.update(kind="countersunk", head_diameter=_r(head_d), head_angle=_r(ang))
    else:
        raise _Unrecognised(f"hole type {typ} (counterdrilled) has no equivalent in catia_hole")
    if d.get("threading_mode") == 0:
        op["threaded"] = True
    return op


def _dress_op(feat: dict[str, Any], kind: str) -> dict[str, Any]:
    d = feat["data"]
    pts = d.get("points") or []
    if d.get("unreadable_edges"):
        raise _Unrecognised(f"{d['unreadable_edges']} selected element(s) are not edges or could not be located "
                            "(a face selection or a consumed edge has no stable position)")
    if not pts:
        raise _Unrecognised("no edge position could be read")
    if kind == "fillet":
        if d.get("propagation") != 1:
            raise _Unrecognised(f"edge propagation {d.get('propagation')} (the tool always propagates by tangency)")
        return {"op": "fillet", "radius": _r(d["radius"]), "edge_points": [[_r(v) for v in p] for p in pts]}
    if d.get("propagation") != 0:
        raise _Unrecognised(f"chamfer propagation {d.get('propagation')} (the tool always uses tangency)")
    if d.get("orientation") not in (0, None):
        raise _Unrecognised("reversed chamfer orientation has no equivalent in catia_chamfer")
    op = {"op": "chamfer", "length": _r(d["length1"]), "edge_points": [[_r(v) for v in p] for p in pts]}
    if d.get("mode") == 0:
        op["length2"] = _r(d["length2"])
    elif d.get("mode") == 1:
        if abs(_f(d.get("angle")) - 45.0) > 1e-9:
            op["angle"] = _r(d["angle"])
    else:
        raise _Unrecognised(f"chamfer mode {d.get('mode')}")
    return op


def _known(feat: dict[str, Any]) -> dict[str, Any]:
    """What is known about a feature the replay cannot reproduce (for the opaque entry)."""
    known: dict[str, Any] = {"type": feat["type"], "up_to_date": feat.get("up_to_date")}
    if feat.get("inactive"):
        known["inactive"] = True
    known.update({k: v for k, v in (feat.get("data") or {}).items() if v is not None and k != "points"})
    if feat.get("sketch"):
        known["sketch"] = feat["sketch"]
    return known


def _record_opaque(ctx: _Ctx, feat: dict[str, Any], where: str, reason: str, solid: bool = True) -> dict[str, Any]:
    entry = {"name": feat["name"], "type": feat["type"], "where": where, "reason": reason,
             "affects_solid": solid, "known": _known(feat)}
    ctx.opaque.append(entry)
    return {"name": feat["name"], "kind": feat.get("kind") or "unknown", "recognised": False, "reason": reason,
            "type": feat["type"], "known": entry["known"]}


def _feature_spec(feat: dict[str, Any], kind: str, op: dict[str, Any] | None) -> dict[str, Any]:
    d = feat["data"]
    spec: dict[str, Any] = {"name": feat["name"], "kind": kind, "type": feat["type"], "recognised": True,
                            "up_to_date": feat.get("up_to_date")}
    if op is not None:
        spec["replay_name"] = op.get("name")
    if kind in ("pad", "pocket"):
        spec.update(sketch=feat.get("sketch"), limit={"mode": d.get("l1_mode"), "length": d.get("l1_value")},
                    second_limit={"mode": d.get("l2_mode"), "length": d.get("l2_value")},
                    symmetric=d.get("symmetric"), orientation=d.get("orientation"))
    elif kind in ("shaft", "groove"):
        spec.update(sketch=feat.get("sketch"), angle_deg=d.get("angle1"), axis=(feat.get("axis") or None))
    elif kind == "hole":
        spec.update({k: d.get(k) for k in ("type", "diameter", "depth", "origin", "direction", "head_diameter",
                                           "head_depth", "head_angle", "threading_mode")})
    elif kind == "fillet":
        spec.update(radius=d.get("radius"), edge_points=d.get("points"))
    elif kind == "chamfer":
        spec.update(mode=d.get("mode"), length1=d.get("length1"), length2=d.get("length2"), angle=d.get("angle"),
                    edge_points=d.get("points"))
    elif kind == "boolean":
        spec["operation"] = d.get("operation")
    elif kind == "mirror":
        spec["plane"] = d.get("plane")
    return spec


def _replay_emit_feature(ctx: _Ctx, body: dict[str, Any], feat: dict[str, Any], path: str) -> tuple[
        dict[str, Any], list[dict[str, Any]]]:
    """Translate one feature. Returns (spec entry, replay ops). Raises nothing: failures become opaque."""
    kind = KIND_OF_TYPE.get(feat["type"])
    feat["kind"] = kind or "unknown"
    ops: list[dict[str, Any]] = []
    try:
        if kind is None:
            raise _Unrecognised(f"feature type '{feat['type']}' is not handled by the reader")
        if feat.get("inactive"):
            raise _Unrecognised("feature is deactivated in the tree")
        if feat.get("up_to_date") is False:
            ctx.notes.append(f"feature '{feat['name']}' is reported not up to date: the stored values are described "
                             "(an update may change them)")
        if feat.get("read_errors") and kind not in ("boolean", "mirror"):
            raise _Unrecognised("some parameters could not be read: " + "; ".join(feat["read_errors"][:3]))
        if kind in SKETCH_BASED:
            sk = _get_sketch(body, feat["sketch"], ctx) if feat.get("sketch") else None
            if sk is None:
                raise _Unrecognised("the sketch of this feature could not be read")
            axis_mode = axis_el = None
            plane = frame_to_plane(sk["axis"])
            if kind in ("shaft", "groove"):
                if plane is None:
                    raise _Unrecognised(f"sketch '{sk['name']}' is not on an origin-plane frame")
                if _f(feat["data"].get("angle2")) > 1e-9:
                    raise _Unrecognised("second angle is not zero (the tool only sets the first angle)")
                if feat["data"].get("thin"):
                    raise _Unrecognised("thin revolution has no equivalent in the tools")
                if not 0 < _f(feat["data"].get("angle1")) <= 360:
                    raise _Unrecognised(f"revolution angle {feat['data'].get('angle1')} out of range")
                axis_mode, axis_el = _revolve_axis(sk, plane)
                feat["axis"] = {"mode": axis_mode}
            sk_op, plane = _sketch_ops(ctx, sk, axis_el)
            if kind == "pad":
                fop = _pad_ops(ctx, feat, sk_op, plane)
            elif kind == "pocket":
                fop = _pocket_ops(ctx, feat, sk_op, plane)
            else:
                fop = {"op": kind, "sketch": sk_op["name"], "axis": axis_mode,
                       "angle": _r(feat["data"]["angle1"])}
            fop["name"] = replay_name(feat["name"], ctx.used, "feature name")
            ops = [sk_op, fop]
        elif kind == "hole":
            fop = _hole_op(feat)
            fop["name"] = replay_name(feat["name"], ctx.used, "feature name")
            ops = [fop]
        elif kind in ("fillet", "chamfer"):
            fop = _dress_op(feat, kind)
            fop["name"] = replay_name(feat["name"], ctx.used, "feature name")
            ops = [fop]
        elif kind == "mirror":
            plane = feat["data"].get("plane")
            if plane not in PLANES:
                raise _Unrecognised("mirroring element is not an origin plane")
            fop = {"op": "mirror", "plane": plane, "name": replay_name(feat["name"], ctx.used, "feature name")}
            ops = [fop]
        elif kind == "boolean":
            op_name = BOOLEAN_OPS.get(feat["type"])
            if op_name is None:
                raise _Unrecognised("Trim (union trim) needs the faces to keep or remove, which have no stable position")
            operand = feat.get("operand")
            if operand is None:
                raise _Unrecognised("the operand body of this boolean operation could not be read")
            feat["data"]["operation"] = op_name
            return _emit_boolean(ctx, body, feat, operand, op_name, path)
        else:
            raise _Unrecognised(_opaque_reason(kind, feat))
        return _feature_spec(feat, kind, ops[-1]), ops
    except (_Unrecognised, ScriptError) as e:
        return _record_opaque(ctx, feat, path, str(e)), []


def _opaque_reason(kind: str, feat: dict[str, Any]) -> str:
    return {
        "rect_pattern": "rectangular pattern: direction and reference of the pattern cannot be read back reliably "
                        "(Automation returns a direction that does not match the one used to create it)",
        "circ_pattern": "circular pattern: rotation axis sign convention and axis references are not proven",
        "shell": "shell: the faces to remove cannot be designated by a stable point",
        "draft": "draft: faces, neutral element and pulling direction cannot be described by stable points",
        "thickness": "thickness: the faces cannot be designated by a stable point",
        "thread": "thread: cosmetic feature, lateral and limit faces cannot be designated by a stable point",
    }.get(kind, f"feature kind '{kind}' has no replay")


def _emit_boolean(ctx: _Ctx, body: dict[str, Any], feat: dict[str, Any], operand: dict[str, Any], op_name: str,
                  path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Boolean feature: replay the operand body (own ops) then the combine."""
    # Build the operand into a scratch context state so that a failure leaves nothing half-written.
    saved_ops, saved_used = ctx.ops, set(ctx.used)
    ctx.ops = []
    tool_name = replay_name(operand["name"], ctx.used, "body name")
    sub_spec = _emit_body(ctx, operand, tool_name, f"{path}/{feat['name']}", nested=True)
    operand_ops = ctx.ops
    ctx.ops = saved_ops
    bool_name = replay_name(feat["name"], ctx.used, "boolean name")
    body_ops = [{"op": "body", "name": tool_name}] + operand_ops + [
        {"op": "combine", "operation": op_name, "tool": tool_name, "target": None, "name": bool_name}]
    spec = {"name": feat["name"], "kind": "boolean", "type": feat["type"], "recognised": True,
            "operation": op_name, "replay_name": bool_name, "operand_body": sub_spec,
            "up_to_date": feat.get("up_to_date")}
    if not any(sub_spec_f.get("recognised") for sub_spec_f in sub_spec["features"]):
        ctx.used = saved_used
        raise _Unrecognised(f"operand body '{operand['name']}' has no replayable solid feature")
    return spec, body_ops


def _emit_body(ctx: _Ctx, body: dict[str, Any], replay_body_name: str, path: str, nested: bool) -> dict[str, Any]:
    """Translate a body's features in tree order. Appends replay ops to ``ctx.ops``.

    Every recognised feature is also applied to a scratch ``PartScript`` right away: a construction the
    DSL refuses (profile crossing itself, axis crossing the profile...) turns that feature into an opaque
    one, with the DSL's message as the reason, instead of breaking the whole script."""
    spec_feats: list[dict[str, Any]] = []
    used_sketches: set[str] = set()
    hole_sketch_names = _hole_internal_sketches(body)
    ctx.hole_internal |= hole_sketch_names
    material = False
    scratch = PartScript("Scratch_Check", "_scratch", result=replay_body_name, close_all=False)
    for feat in body["shapes"]:
        here = f"{path}/{feat['name']}" if path else feat["name"]
        spec, ops = _replay_emit_feature_transactional(ctx, body, feat, here)
        if spec["recognised"]:
            group = _retarget(ops, replay_body_name)
            snap = copy.deepcopy(scratch)
            try:
                apply_ops(scratch, group)
            except ScriptError as e:
                scratch = snap
                spec = _record_opaque(ctx, feat, here, f"the scripting kit refuses it: {e}")
                group = []
            if group:
                ctx.ops.extend(group)
                if spec["kind"] in ADDS_MATERIAL or spec["kind"] == "boolean":
                    material = True
        spec_feats.append(spec)
        if feat.get("sketch"):
            used_sketches.add(feat["sketch"])
    if material:
        ctx.material_bodies.add(replay_body_name)
    # sketches no feature uses: still part of the tree, replayed (kept last) unless a hole owns them
    leftovers = []
    for sk in body["sketches"]:
        if sk["name"] in used_sketches or sk["name"] in hole_sketch_names:
            continue
        try:
            op, _ = _sketch_ops(ctx, sk)
            apply_ops(copy.deepcopy(scratch), [op])
            ctx.ops.append(op)
            apply_ops(scratch, [op])
            leftovers.append({"name": sk["name"], "recognised": True})
        except (_Unrecognised, ScriptError) as e:
            leftovers.append({"name": sk["name"], "recognised": False, "reason": str(e)})
            ctx.opaque.append({"name": sk["name"], "type": "Sketch", "where": path, "reason": str(e),
                               "affects_solid": False, "known": {"elements": len(sk["elements"])}})
    return {"name": body["name"], "replay_name": replay_body_name, "features": spec_feats,
            "unused_sketches": leftovers, "hole_internal_sketches": sorted(hole_sketch_names)}


def _retarget(ops: list[dict[str, Any]], body_name: str) -> list[dict[str, Any]]:
    """Booleans are emitted without their target: it is the body being built."""
    out = []
    for op in ops:
        if op.get("op") == "combine" and op.get("target") is None:
            op = {**op, "target": body_name}
        out.append(op)
    return out


def _replay_emit_feature_transactional(ctx: _Ctx, body: dict[str, Any], feat: dict[str, Any], path: str):
    """Emit one feature; a failure restores the name registry so nothing is half-registered."""
    saved_used, saved_notes = set(ctx.used), len(ctx.notes)
    saved_sketches = set(ctx.emitted_sketch_names)
    saved_opaque = len(ctx.opaque)
    try:
        spec, ops = _replay_emit_feature(ctx, body, feat, path)
    except _Unrecognised as e:
        ctx.used, ctx.emitted_sketch_names = saved_used, saved_sketches
        del ctx.notes[saved_notes:]
        del ctx.opaque[saved_opaque:]
        return _record_opaque(ctx, feat, path, str(e)), []
    if not spec["recognised"]:
        ctx.used, ctx.emitted_sketch_names = saved_used, saved_sketches
    return spec, ops


def _hole_internal_sketches(body: dict[str, Any]) -> set[str]:
    """Sketches created by Hole features (position sketch + the profile sketch CATIA adds at update).

    The position sketch is ``Hole.Sketch``; the profile sketch is not exposed by the API, it is
    the sketch listed right after it (proven live on holes made by catia_hole). UNVERIFIED-LIVE for
    holes made in the GUI with a user-positioned sketch."""
    names = [s["name"] for s in body["sketches"]]
    out: set[str] = set()
    for feat in body["shapes"]:
        if feat["type"] != "Hole" or not feat.get("sketch"):
            continue
        out.add(feat["sketch"])
        if feat["sketch"] in names:
            i = names.index(feat["sketch"])
            if i + 1 < len(names):
                nxt = body["sketches"][i + 1]
                if not any(e["kind"] == "circle" for e in nxt["elements"]) and nxt["name"] != feat["sketch"]:
                    out.add(nxt["name"])
    return out


def build_spec(raw: dict[str, Any], replay_part_name: str | None = None) -> dict[str, Any]:
    """Turn the raw reader output of a CATPart into the spec, the replay operations and the opaque list.

    Pure function: no COM. ``raw`` is what ``ModelReader.read_part`` returns (tests feed hand-made dicts).
    """
    part_name = raw["part"]["name"]
    rname = replay_part_name or sanitize_name(f"{part_name}_Replay")
    ctx = _Ctx(raw, rname)
    ctx.used.add(rname)
    bodies = raw["bodies"]
    raw["_all_bodies"] = tuple(bodies)
    main = next((b for b in bodies if b.get("main")), bodies[0] if bodies else None)
    result_name = replay_name(main["name"], ctx.used, "result body name") if main else None
    spec_bodies: list[dict[str, Any]] = []
    orphan_names: list[str] = []
    all_ops_head: list[dict[str, Any]] = []
    if main is not None:
        ctx.ops = []
        sb = _emit_body(ctx, main, result_name, "", nested=False)
        sb.update(role="result", order=0, visible=main.get("visible"))
        spec_bodies.append(sb)
        all_ops_head = ctx.ops
    tail_ops: list[dict[str, Any]] = []
    for i, b in enumerate([x for x in bodies if x is not main], 1):
        bn = replay_name(b["name"], ctx.used, "body name")
        ctx.ops = [{"op": "body", "name": bn}]
        sb = _emit_body(ctx, b, bn, "", nested=False)
        sb.update(role="unmerged body", order=i, visible=b.get("visible"))
        spec_bodies.append(sb)
        orphan_names.append(bn)
        tail_ops += ctx.ops
    ops = all_ops_head + tail_ops
    # geometrical sets and wireframe elements: listed, never replayed
    wire: list[dict[str, Any]] = []
    for b in bodies:
        for h in b.get("hybrid_shapes", []):
            wire.append({"name": h["name"], "type": h["type"], "in": b["name"]})
    for hb in raw.get("hybrid_bodies", []):
        for h in hb["elements"]:
            wire.append({"name": h["name"], "type": h["type"], "in": hb["name"]})
    for w in wire:
        ctx.opaque.append({"name": w["name"], "type": w["type"], "where": w["in"], "affects_solid": False,
                           "reason": "wireframe/surface element: not replayed (a sketch placed on a plane is "
                                     "replayed on an equivalent origin-plane offset)",
                           "known": {"type": w["type"]}})
    sketches = {}
    for b in bodies:
        for sk in _all_sketches(b):
            sketches[sk["name"]] = _sketch_spec(sk)
    solid_opaque = [o for o in ctx.opaque if o.get("affects_solid", True)]
    sketch_opaque = [o for o in ctx.opaque if o["type"] == "Sketch"]
    measures = raw.get("measures") or {}
    spec: dict[str, Any] = {
        "format": FORMAT,
        "kind": "part",
        "part": {"name": part_name, "units": "mm", "density_kg_m3": raw["part"].get("density")},
        "parameters": raw.get("parameters", []),
        "relations": raw.get("relations", []),
        "bodies": spec_bodies,
        "sketches": sketches,
        "wireframe": wire,
        "checks": measures,
        "opaque": ctx.opaque,
        "names": dict(ctx.names),
        "notes": ctx.notes,
        "replay": {
            "part_name": rname, "result_body": result_name, "unmerged_bodies": orphan_names,
            "complete": not solid_opaque and not sketch_opaque and bool(ops),
            "opaque_solid_features": len(solid_opaque),
            "sketch_constraints_replayed": False,
            "constraint_note": "constraints are described in 'sketches' but not replayed: the sketch tools address "
                               "elements by index and the replay draws each profile as connected segments",
        },
    }
    spec["_ops"] = ops
    return spec


def _all_sketches(body: dict[str, Any]) -> list[dict[str, Any]]:
    out = list(body["sketches"])
    for f in body["shapes"]:
        if f.get("operand"):
            out += _all_sketches(f["operand"])
    return out


# ============================================================================================
# 3. Replay operations -> PartScript object / source text
# ============================================================================================


def apply_ops(p: PartScript, ops: Sequence[dict[str, Any]]) -> None:
    """Drive a real ``PartScript`` with the operations (raises ScriptError on any DSL violation)."""
    from catia_mcp.scripting import arc_seg, line_seg

    for op in ops:
        kind = op["op"]
        if kind == "sketch":
            plane = p.plane(op["plane"], offset=op["offset"]) if op.get("offset") else op["plane"]
            with p.sketch(plane, op["name"]) as sk:
                for el in op["elements"]:
                    k = el["kind"]
                    if k == "circle":
                        sk.circle(el["cx"], el["cy"], el["r"], construction=el.get("construction", False))
                    elif k == "profile":
                        segs = [line_seg(s["to"]) if s["type"] == "line" and "to" in s else
                                (arc_seg(s["to"], s["center"], s.get("direction", "ccw")) if s["type"] == "arc" else
                                 {"type": "line"}) for s in el["segments"]]
                        sk.profile(el["start"], segs, closed=el["closed"])
                    elif k == "line":
                        sk.line(el["x1"], el["y1"], el["x2"], el["y2"], axis=el.get("axis", False))
                    elif k == "arc":
                        sk.arc(el["cx"], el["cy"], el["r"], el["a0"], el["a1"])
                    elif k == "point":
                        sk.point(el["x"], el["y"])
        elif kind == "pad":
            p.pad(op["name"], op["height"], symmetric=op.get("symmetric", False),
                  direction=op.get("direction", "normal"), second_height=op.get("second_height"),
                  sketch=op["sketch"])
        elif kind == "pocket":
            p.pocket(op["name"], op.get("depth"), limit=op["limit"], direction=op["direction"], sketch=op["sketch"])
        elif kind in ("shaft", "groove"):
            getattr(p, kind)(op["name"], op["axis"], op["angle"], sketch=op["sketch"])
        elif kind == "hole":
            kw = {k: op[k] for k in ("head_diameter", "head_depth", "head_angle", "taper_angle", "threaded") if k in op}
            p.hole(op["name"], op["point"], op["diameter"], op.get("depth"),
                   up_to_point=op.get("up_to_point"), kind=op.get("kind", "simple"), **kw)
        elif kind == "fillet":
            p.fillet(op["name"], op["radius"], op["edge_points"])
        elif kind == "chamfer":
            kw = {k: op[k] for k in ("angle", "length2") if k in op}
            p.chamfer(op["name"], op["length"], op["edge_points"], **kw)
        elif kind == "mirror":
            p.mirror(op["name"], plane=op["plane"])
        elif kind == "body":
            p.body(op["name"])
        elif kind == "combine":
            p.combine(op["target"], op["tool"], op["operation"], op["name"])
        else:  # pragma: no cover - internal consistency
            raise ScriptError(f"unknown replay operation {kind!r}")


def _num(x: float) -> str:
    return repr(float(_r(x)))


def _pt(p: Sequence[float]) -> str:
    return "(" + ", ".join(_num(v) for v in p) + ")"


def render_script(spec: dict[str, Any], ops: Sequence[dict[str, Any]] | None = None) -> str:
    """Source text of a PartScript that replays ``ops`` (defaults to the ones stored in the spec)."""
    ops = list(spec["_ops"] if ops is None else ops)
    rp = spec["replay"]
    checks = spec.get("checks") or {}
    L: list[str] = []
    w = L.append
    w('"""Replay script generated by catia_describe_model.')
    w("")
    w(f"Rebuilds a part described in the spec (format {FORMAT}). Run it with:")
    w("")
    w("    python <this file> --dry-run   # validate every step against the tool schemas, no CATIA")
    w("    python <this file>             # build it in a NEW document and run the built-in checks")
    w("")
    if rp["complete"]:
        w("Every feature of the original was recognised.")
    else:
        w(f"INCOMPLETE: {rp['opaque_solid_features']} solid feature(s) or some sketches could not be reproduced")
        w("(see 'opaque' in the spec). The result will differ from the original.")
    w("Sketch constraints are not replayed (geometry is exact, dimensions are values).")
    w('"""')
    w("import os")
    w("import sys")
    w("from pathlib import Path")
    w("")
    w("from catia_mcp.scripting import PartScript, arc_seg, cli, line_seg  # noqa: F401")
    w("")
    w('OUT = Path(os.environ.get("CATIA_OUT_DIR", Path(__file__).resolve().parent / "out"))')
    w(f"NAME = {rp['part_name']!r}")
    dens = spec["part"].get("density_kg_m3")
    dens_arg = f", density={_num(dens)}" if dens and dens > 0 else ""
    w(f"p = PartScript(NAME, OUT / NAME, result={rp['result_body']!r}{dens_arg}, close_all=False)")
    w("")
    for op in ops:
        k = op["op"]
        if k == "sketch":
            plane = (f"p.plane({op['plane']!r}, offset={_num(op['offset'])})" if op.get("offset")
                     else repr(op["plane"]))
            w(f"with p.sketch({plane}, {op['name']!r}) as sk:")
            body = 0
            for el in op["elements"]:
                kk = el["kind"]
                if kk == "circle":
                    extra = ", construction=True" if el.get("construction") else ""
                    w(f"    sk.circle({_num(el['cx'])}, {_num(el['cy'])}, {_num(el['r'])}{extra})")
                elif kk == "profile":
                    segs = []
                    for s in el["segments"]:
                        if s["type"] == "line" and "to" in s:
                            segs.append(f"line_seg({_pt(s['to'])})")
                        elif s["type"] == "arc":
                            segs.append(f"arc_seg({_pt(s['to'])}, {_pt(s['center'])}, {s.get('direction', 'ccw')!r})")
                        else:
                            segs.append("{'type': 'line'}")
                    w(f"    sk.profile({_pt(el['start'])}, [")
                    for s in segs:
                        w(f"        {s},")
                    w(f"    ], closed={el['closed']})")
                elif kk == "line":
                    extra = ", axis=True" if el.get("axis") else ""
                    w(f"    sk.line({_num(el['x1'])}, {_num(el['y1'])}, {_num(el['x2'])}, {_num(el['y2'])}{extra})")
                elif kk == "arc":
                    w(f"    sk.arc({_num(el['cx'])}, {_num(el['cy'])}, {_num(el['r'])}, {_num(el['a0'])}, {_num(el['a1'])})")
                elif kk == "point":
                    w(f"    sk.point({_num(el['x'])}, {_num(el['y'])})")
                body += 1
            if not body:
                w("    pass")
        elif k == "pad":
            extra = ""
            if op.get("symmetric"):
                extra += ", symmetric=True"
            if op.get("direction", "normal") != "normal":
                extra += f", direction={op['direction']!r}"
            if op.get("second_height") is not None:
                extra += f", second_height={_num(op['second_height'])}"
            w(f"p.pad({op['name']!r}, {_num(op['height'])}{extra}, sketch={op['sketch']!r})")
        elif k == "pocket":
            depth = f", {_num(op['depth'])}" if op.get("depth") is not None else ""
            w(f"p.pocket({op['name']!r}{depth}, limit={op['limit']!r}, direction={op['direction']!r}, "
              f"sketch={op['sketch']!r})")
        elif k in ("shaft", "groove"):
            w(f"p.{k}({op['name']!r}, {op['axis']!r}, {_num(op['angle'])}, sketch={op['sketch']!r})")
        elif k == "hole":
            extra = "".join(f", {key}={(_num(op[key]) if key != 'threaded' else 'True')}"
                            for key in ("head_diameter", "head_depth", "head_angle", "taper_angle", "threaded")
                            if key in op)
            limit = f"depth={_num(op['depth'])}" if "depth" in op else f"up_to_point={_pt(op['up_to_point'])}"
            w(f"p.hole({op['name']!r}, {_pt(op['point'])}, {_num(op['diameter'])}, {limit}, "
              f"kind={op.get('kind', 'simple')!r}{extra})")
        elif k == "fillet":
            pts = ", ".join(_pt(pp) for pp in op["edge_points"])
            w(f"p.fillet({op['name']!r}, {_num(op['radius'])}, [{pts}])")
        elif k == "chamfer":
            pts = ", ".join(_pt(pp) for pp in op["edge_points"])
            extra = "".join(f", {key}={_num(op[key])}" for key in ("angle", "length2") if key in op)
            w(f"p.chamfer({op['name']!r}, {_num(op['length'])}, [{pts}]{extra})")
        elif k == "mirror":
            w(f"p.mirror({op['name']!r}, plane={op['plane']!r})")
        elif k == "body":
            w(f"p.body({op['name']!r})")
        elif k == "combine":
            w(f"p.combine({op['target']!r}, {op['tool']!r}, {op['operation']!r}, {op['name']!r})")
    w("")
    if rp.get("unmerged_bodies"):
        w("p.save(allow_unmerged=True)  # the original also has unmerged bodies")
    else:
        w("p.save()")
    vol = (checks.get("result") or {}).get("volume_mm3")
    bb = (checks.get("result") or {}).get("bbox")
    if vol and vol > 0:
        size = [max(0.001, _r(s, 3)) for s in (bb["size"] if bb else (None, None, None))] if bb else None
        args = f"volume={_num(vol)}"
        if size:
            args += f", bbox={tuple(size)!r}"
        w(f"p.checks({args}, tol=0.001, bbox_tol=0.05)   # measured on the original")
    w("")
    w('if __name__ == "__main__":')
    w("    sys.exit(cli(p))")
    return "\n".join(L) + "\n"


def validate_replay(spec: dict[str, Any], schemas: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the operations through a real PartScript (no CATIA) and validate every step against the schemas.

    Returns ``{"valid": bool, "steps": n, "problems": [...]}``; never raises."""
    ops = spec["_ops"]
    if not ops:
        return {"valid": False, "steps": 0, "problems": ["nothing to replay"]}
    try:
        rp = spec["replay"]
        p = PartScript(rp["part_name"], Path("_replay_validation"), result=rp["result_body"], close_all=False)
        apply_ops(p, ops)
        p.save(allow_unmerged=bool(rp.get("unmerged_bodies")))
        steps = p.steps()
        problems: list[str] = []
        if schemas is not None:
            problems = p.validate(schemas)
        return {"valid": not problems, "steps": len(steps), "problems": problems,
                "warnings": list(p.warnings)}
    except ScriptError as e:
        return {"valid": False, "steps": 0, "problems": [str(e)]}
    except Exception as e:  # pragma: no cover - defensive
        return {"valid": False, "steps": 0, "problems": [f"{type(e).__name__}: {e}"]}


def public_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec without internal keys (what is returned and written)."""
    return {k: v for k, v in spec.items() if not k.startswith("_")}


def compare_measures(orig: dict[str, Any], replay: dict[str, Any], vol_tol: float = 1e-3,
                     bbox_tol_mm: float = 0.05, inertia_tol: float = 5e-3) -> dict[str, Any]:
    """Compare two ``measure`` results (result body): volume (relative), bounding box (mm), COG (mm) and
    the mass-normalised principal moments (relative). Pure."""
    out: dict[str, Any] = {"checks": []}

    def add(name: str, delta: float, tol: float, unit: str) -> None:
        out["checks"].append({"name": name, "delta": _r(delta, 6), "tolerance": tol, "unit": unit,
                              "ok": abs(delta) <= tol})

    va, vb = orig.get("volume_mm3"), replay.get("volume_mm3")
    if va and vb is not None:
        add("volume", (vb - va) / va, vol_tol, "relative")
    ba, bb = orig.get("bbox"), replay.get("bbox")
    if ba and bb:
        for i, ax in enumerate("xyz"):
            add(f"bbox_min_{ax}", bb[ax][0] - ba[ax][0], bbox_tol_mm, "mm")
            add(f"bbox_max_{ax}", bb[ax][1] - ba[ax][1], bbox_tol_mm, "mm")
    ca, cb = orig.get("cog_mm"), replay.get("cog_mm")
    if ca and cb:
        add("cog_distance", math.dist(ca, cb), 0.05, "mm")
    ia, ib = orig.get("inertia"), replay.get("inertia")
    if ia and ib and ia.get("mass_kg") and ib.get("mass_kg"):
        for k in range(3):
            a = ia["principal_moments_kg_mm2"][k] / ia["mass_kg"]
            b = ib["principal_moments_kg_mm2"][k] / ib["mass_kg"]
            if a:
                add(f"principal_moment_{k + 1}_per_mass", (b - a) / a, inertia_tol, "relative")
    out["ok"] = all(c["ok"] for c in out["checks"]) and bool(out["checks"])
    return out


# ============================================================================================
# 4. Audit (pure)
# ============================================================================================


def _issue(sev: str, code: str, where: str, message: str, fix: list[dict[str, Any]] | None = None,
           manual: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"severity": sev, "code": code, "where": where, "message": message}
    if fix:
        out["fix"] = fix
    if manual:
        out["manual"] = manual
    return out


def suggest_name(kind: str, feat: dict[str, Any] | None, taken: set[str], fallback: str = "Item") -> str:
    """A role-based name for a default-named object, unique among ``taken``."""
    d = (feat or {}).get("data", {}) if feat else {}

    def g(x: Any) -> str:
        return f"{float(x):g}" if isinstance(x, (int, float)) else ""

    base = {
        "pad": f"Pad_H{g(d.get('l1_value'))}" if d.get("l1_value") else "Pad_Base",
        "pocket": f"Pocket_D{g(d.get('l1_value'))}" if d.get("l1_value") else "Pocket_Cut",
        "shaft": "Shaft_Revolved", "groove": "Groove_Revolved",
        "hole": f"Hole_D{g(d.get('diameter'))}" if d.get("diameter") else "Hole_Round",
        "fillet": f"Round_R{g(d.get('radius'))}" if d.get("radius") else "Round_Edges",
        "chamfer": f"Chamfer_L{g(d.get('length1'))}" if d.get("length1") else "Chamfer_Edges",
        "boolean": "Combine_Bodies", "mirror": "Mirror_Body", "sketch": "Sk_Profile", "body": "Body_Zone",
    }.get(kind, f"{fallback}_Role")
    cand, i = base, 2
    while cand in taken:
        cand, i = f"{base}_{i}", i + 1
    taken.add(cand)
    return cand


def _sketch_users(body: dict[str, Any]) -> dict[str, list[str]]:
    users: dict[str, list[str]] = {}
    for f in body["shapes"]:
        if f.get("sketch"):
            users.setdefault(f["sketch"], []).append(f["name"])
    return users


def _walk_bodies(raw: dict[str, Any]) -> Iterable[tuple[dict[str, Any], str, bool, dict[str, Any] | None]]:
    """(body, path, reachable, top_level_body): ``reachable`` = the body is in Part.Bodies (can be activated)."""
    def rec(b: dict[str, Any], path: str, reachable: bool, top: dict[str, Any]):
        yield b, path, reachable, top
        for f in b["shapes"]:
            if f.get("operand"):
                yield from rec(f["operand"], f"{path}/{f['name']}/{f['operand']['name']}", False, top)

    for b in raw["bodies"]:
        yield from rec(b, b["name"], True, b)


def audit_part(raw: dict[str, Any]) -> dict[str, Any]:
    """Rules on the raw reader output. Returns ``{"issues": [...], "stats": {...}, "not_checked": [...]}``,
    issues ranked error > warning > info."""
    issues: list[dict[str, Any]] = []
    taken: set[str] = set()
    for b, _p, _r_, _t in _walk_bodies(raw):
        taken.add(b["name"])
        taken |= {f["name"] for f in b["shapes"]} | {s["name"] for s in b["sketches"]}
    part_name = raw["part"]["name"]
    n_features = n_sketches = 0

    for body, path, reachable, top in _walk_bodies(raw):
        act = [{"tool": "catia_activate_body", "args": {"body_name": top["name"]}}] if reachable else None
        # -- bodies
        if is_default_name(body["name"]):
            new = suggest_name("body", None, taken) if not body.get("main") else f"{sanitize_name(part_name)}_Resultat"
            taken.add(new)
            issues.append(_issue("warning", "tree.default_name", path, f"body '{body['name']}' has a default name",
                                 [{"tool": "catia_rename_body", "args": {"old_name": body["name"], "new_name": new}}]
                                 if reachable else None,
                                 None if reachable else "absorbed body: rename it in the tree (it cannot be activated)"))
        n_solid = len(body["shapes"])
        if n_solid == 0:
            sev = "error"
            code = "tree.body_without_result" if body.get("main") else "tree.empty_body"
            issues.append(_issue(sev, code, path,
                                 f"body '{body['name']}' has no feature: it produces no solid",
                                 manual="model a solid in it (catia_activate_body, sketch, catia_pad) or remove the "
                                        "empty body from the tree in CATIA (no tool deletes a body)"))
        elif reachable and top is body and not any(
                KIND_OF_TYPE.get(f["type"]) in ADDS_MATERIAL | {"boolean"} for f in body["shapes"]) \
                and not any(f["type"] in ("Pocket", "Groove", "Hole") for f in body["shapes"]):
            issues.append(_issue("error", "tree.body_without_result", path,
                                 f"body '{body['name']}' has features but none that creates or removes material"))
        vol = body.get("volume_mm3")
        if reachable and body["shapes"] and vol is not None and vol <= 1e-6 and body.get("main"):
            issues.append(_issue("error", "tree.body_without_result", path,
                                 f"body '{body['name']}' measures a volume of 0: its features produce no solid"))
        if reachable and body.get("visible") is False:
            issues.append(_issue("warning", "tree.hidden_body", path, f"body '{body['name']}' is hidden",
                                 [{"tool": "catia_hide_show_body", "args": {"body_name": body["name"], "visible": True}}]))
        if reachable and not body.get("main") and body["shapes"]:
            issues.append(_issue("warning", "tree.unmerged_body", path,
                                 f"body '{body['name']}' holds features but is not merged into the main body "
                                 "(it does not contribute to the part result)",
                                 manual="merge it with catia_boolean_operation (assemble/add/remove/intersect) or "
                                        "delete it if it is a leftover"))
        # -- features
        users = _sketch_users(body)
        hole_owned = _hole_internal_sketches(body)
        seen_material = False
        for idx, f in enumerate(body["shapes"]):
            n_features += 1
            kind = KIND_OF_TYPE.get(f["type"], "unknown")
            if is_default_name(f["name"]):
                new = suggest_name(kind, f, taken)
                issues.append(_issue("warning", "tree.default_name", f"{path}/{f['name']}",
                                     f"feature '{f['name']}' ({f['type']}) has a default name",
                                     (act or []) + [{"tool": "catia_rename_feature",
                                                     "args": {"old_name": f["name"], "new_name": new,
                                                              "kind": "feature"}}] if reachable else None,
                                     None if reachable else "feature of an absorbed body: rename it in the tree"))
            if f.get("up_to_date") is False:
                vol_top = top.get("volume_mm3")
                failing = bool(top["shapes"]) and (vol_top is None or vol_top <= 1e-6)
                issues.append(_issue("error" if failing else "warning", "feature.not_up_to_date",
                                     f"{path}/{f['name']}",
                                     f"feature '{f['name']}' is not up to date (update pending or failed"
                                     + ("; its body measures no solid: probably failed)" if failing else ")"),
                                     [{"tool": "catia_update_part", "args": {}}],
                                     "if the update still fails, delete the feature (catia_delete_feature) or fix its "
                                     "sketch/references"))
            if f.get("inactive"):
                issues.append(_issue("warning", "feature.inactive", f"{path}/{f['name']}",
                                     f"feature '{f['name']}' is deactivated",
                                     manual="reactivate it in CATIA (right click, Activate) or delete it"))
            if f.get("visible") is False:
                issues.append(_issue("info", "tree.hidden_element", f"{path}/{f['name']}",
                                     f"solid feature '{f['name']}' is hidden"))
            # order: dress-up before any material, hole before any material in its own body
            if kind in ADDS_MATERIAL or kind == "boolean":
                seen_material = True
            elif kind in DRESS_UP and not seen_material:
                issues.append(_issue("warning", "tree.order", f"{path}/{f['name']}",
                                     f"dress-up feature '{f['name']}' comes before any feature that creates material "
                                     "in this body (it has nothing to work on)",
                                     manual="move it after the pad/shaft it depends on (Reorder in CATIA)"))
            elif kind == "hole" and not seen_material and reachable:
                issues.append(_issue("warning", "tree.order", f"{path}/{f['name']}",
                                     f"hole '{f['name']}' is created before any pad/shaft in its body",
                                     manual="move it after the feature that creates the material it drills"))
        # -- sketches
        for sk in body["sketches"]:
            n_sketches += 1
            sp = f"{path}/{sk['name']}"
            if is_default_name(sk["name"]):
                new = suggest_name("sketch", None, taken)
                issues.append(_issue("warning", "tree.default_name", sp, f"sketch '{sk['name']}' has a default name",
                                     (act or []) + [{"tool": "catia_rename_feature",
                                                     "args": {"old_name": sk["name"], "new_name": new,
                                                              "kind": "sketch"}}] if reachable else None,
                                     None if reachable else "sketch of an absorbed body: rename it in the tree"))
            used_by = users.get(sk["name"], [])
            if sk["name"] not in hole_owned and not used_by:
                issues.append(_issue("warning", "sketch.unused", sp,
                                     f"sketch '{sk['name']}' is not used by any feature",
                                     (act or []) + [{"tool": "catia_delete_feature",
                                                     "args": {"name": sk["name"], "kind": "sketch"}}]
                                     if reachable else None,
                                     "delete it in the tree, or build the feature it was meant for"
                                     if not reachable else None))
            if sk.get("unreadable"):
                issues.append(_issue("warning", "sketch.unreadable", sp,
                                     f"sketch '{sk['name']}' could not be read ({sk['unreadable']}); its checks were "
                                     "skipped", manual="run the audit again; if it persists open the sketch in CATIA"))
                continue
            if not sk["elements"] or not any(e["kind"] != "point" or not e["construction"] for e in sk["elements"]):
                if sk["name"] not in hole_owned:
                    issues.append(_issue("warning", "sketch.empty", sp, f"sketch '{sk['name']}' has no geometry"))
            if used_by and sk["name"] not in hole_owned:
                ends = open_ends(sk["elements"])
                # a revolution axis line is not part of the profile
                cl = (sk.get("center_line") or {}).get("name")
                if cl:
                    ends = [p for p in ends if not any(
                        e["name"] == cl and (_same(e["start"], p, 1e-4) or _same(e["end"], p, 1e-4))
                        for e in sk["elements"] if e["kind"] == "line")]
                if ends:
                    issues.append(_issue("error", "sketch.not_closed", sp,
                                         f"sketch '{sk['name']}' feeds {', '.join(used_by)} but its profile is open: "
                                         f"{len(ends)} free end(s) at {[[_r(v, 3) for v in p] for p in ends[:4]]}",
                                         manual="reopen the sketch in CATIA and join the free ends (or add the closing "
                                                "segment); no tool re-opens an existing sketch"))
            bad = [c for c in sk["constraints"] if c["status"] != "OK"]
            if bad:
                issues.append(_issue("error", "sketch.constraint_error", sp,
                                     f"sketch '{sk['name']}': {len(bad)} constraint(s) not satisfied "
                                     f"({', '.join(c['name'] + ': ' + c['status'] for c in bad[:4])}): over-constrained "
                                     "or contradictory",
                                     manual="delete or relax one of the listed constraints in CATIA (Sketch analysis)"))
            curves = [e for e in sk["elements"] if e["kind"] in ("line", "circle", "arc") and not e["construction"]]
            if curves and not sk["constraints"] and sk["name"] not in hole_owned:
                issues.append(_issue("info", "sketch.unconstrained", sp,
                                     f"sketch '{sk['name']}' has {len(curves)} curve(s) and no constraint at all "
                                     "(fully free geometry)",
                                     manual="dimension and constrain it in CATIA; 'iso-constrained' cannot be proven "
                                            "for sketches that do have constraints (see not_checked)"))
    for hb in raw.get("hybrid_bodies", []):
        if is_default_name(hb["name"]):
            issues.append(_issue("warning", "tree.default_name", hb["name"], f"geometrical set '{hb['name']}' has a "
                                 "default name", manual="rename it in the tree"))
        if hb.get("visible") is False:
            issues.append(_issue("info", "tree.hidden_element", hb["name"], f"geometrical set '{hb['name']}' is hidden"))
    order = {s: i for i, s in enumerate(SEVERITIES)}
    issues.sort(key=lambda i: (order[i["severity"]], i["code"], i["where"]))
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in SEVERITIES}
    return {
        "issues": issues,
        "stats": {"bodies": len(raw["bodies"]), "features": n_features, "sketches": n_sketches, **counts},
        "not_checked": [
            {"code": "sketch.iso_constrained",
             "reason": "the Automation API has no degrees-of-freedom analysis (the Sketch interface exposes none); "
                       "only 'no constraint at all' and unsatisfied constraints are detected"},
            {"code": "tree.feature_without_effect",
             "reason": "would need the volume before and after each feature; not measurable without modifying the model"},
            {"code": "param.unused", "reason": "references between parameters and relations are not analysed"},
            {"code": "model.units", "reason": "document unit settings are not read"},
        ],
    }


# ---- products --------------------------------------------------------------------------------

_DEFAULT_PN = re.compile(r"^(Part|Product|Produit|Piece)\d*$", re.IGNORECASE)


def _instance_of(display_name: str, root_name: str) -> str | None:
    """Instance path ('Sub.1/Part.1') of the component a constraint reference points to, from its
    DisplayName ('Root/Sub.1/Part.1/!Face...')."""
    if not display_name:
        return None
    head = display_name.split("!")[0].strip("/")
    parts = [p for p in head.split("/") if p]
    if parts and parts[0] == root_name:
        parts = parts[1:]
    return "/".join(parts) if parts else None


def audit_product(raw: dict[str, Any]) -> dict[str, Any]:
    """Audit of an assembly: constraint statuses, default names, unconstrained components."""
    issues: list[dict[str, Any]] = []
    comps = raw["components"]
    csts = raw["constraints"]
    fixed: set[str] = set()
    touched: dict[str, int] = {}
    for c in csts:
        insts = [i for i in (c.get("instances") or []) if i]
        if c["type_name"] == "reference":
            fixed.update(insts)
        for i in insts:
            touched[i] = touched.get(i, 0) + 1
        if c["status"] != "OK":
            issues.append(_issue("error", "constraint.not_ok", c["name"],
                                 f"constraint '{c['name']}' ({c['type_name']}) status: {c['status']}",
                                 [{"tool": "catia_update_assembly", "args": {}}],
                                 "if it stays not OK, the geometry is inconsistent: check orientation/side, pre-position "
                                 "the components (catia_move_component) and recreate the constraint"))
        if c.get("inactive"):
            issues.append(_issue("warning", "constraint.inactive", c["name"], f"constraint '{c['name']}' is deactivated",
                                 manual="reactivate it in CATIA or delete it"))
        if is_default_name(c["name"] or ""):
            issues.append(_issue("warning", "constraint.default_name", c["name"],
                                 f"constraint '{c['name']}' has a default name",
                                 manual="no tool renames a constraint: recreate it with an explicit name "
                                        "(Coax_A_B, Contact_A_B, Fix_A...)"))
    for comp in comps:
        if not comp.get("is_assembly") and comp.get("file") is None:
            issues.append(_issue("error", "component.unresolved", comp["path"],
                                 f"component '{comp['path']}' has no readable file (broken link)"))
        if comp.get("part_number") and _DEFAULT_PN.match(comp["part_number"]):
            issues.append(_issue("warning", "component.default_part_number", comp["path"],
                                 f"component '{comp['path']}' has the default part number '{comp['part_number']}'",
                                 manual="set a meaningful part number in the part (Properties)"))
    leaves = [c for c in comps if not c.get("is_assembly")]
    if leaves and not fixed:
        first = leaves[0]
        issues.append(_issue("warning", "assembly.no_fixed_component", raw["product"]["name"],
                             "no component is fixed: the whole assembly floats",
                             [{"tool": "catia_fix_constraint", "args": {"component": first["path"],
                                                                        "name": f"Fix_{sanitize_name(first['instance'])}"}}],
                             "fix the reference component (usually the largest one) instead of the first if it is not"))
    for comp in leaves:
        if comp["path"] in fixed:
            continue
        if not touched.get(comp["path"]):
            issues.append(_issue("warning", "component.unconstrained", comp["path"],
                                 f"component '{comp['path']}' is not constrained and not fixed (it can move freely)",
                                 manual="constrain it with contact/coincidence on real faces "
                                        "(catia_contact_constraint / catia_coincidence_constraint) after pre-positioning it"))
    order = {s: i for i, s in enumerate(SEVERITIES)}
    issues.sort(key=lambda i: (order[i["severity"]], i["code"], i["where"]))
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in SEVERITIES}
    return {"issues": issues,
            "stats": {"components": len(leaves), "assemblies": len(comps) - len(leaves), "constraints": len(csts),
                      "fixed": sorted(fixed), **counts},
            "not_checked": [{"code": "constraint.redundant",
                             "reason": "redundant but consistent constraints are not reported by the API"}]}



# ============================================================================================
# 5. COM reader
# ============================================================================================


def type_name(obj: Any) -> str:
    """Interface name of a COM object as the type library spells it ('Pad', 'Sketch', 'Body'...)."""
    fake = getattr(obj, "_fake_type", None)
    if fake:
        return fake
    try:
        return obj._oleobj_.GetTypeInfo().GetDocumentation(-1)[0]
    except Exception:
        return "Unknown"


class ModelReader:
    """Reads the active document into plain dicts. The only place that talks to CATIA."""

    def __init__(self, app: Any, doc: Any, run_vbs: Callable[[str, list[Any]], Any] | None = None,
                 measure: bool = True, bbox_max_faces: int = 400) -> None:
        self.app = app
        self.doc = doc
        self._run = run_vbs or (lambda code, args: geometry.run_vbs(app, code, args))
        self.measure = measure
        self.bbox_max_faces = bbox_max_faces
        self.part: Any = None
        self.errors: list[str] = []

    # -- small safe helpers
    def _safe(self, fn: Callable[[], Any], errs: list[str] | None = None, what: str = "", default: Any = None) -> Any:
        try:
            return fn()
        except Exception as e:  # COM errors carry the failing member in their text
            if errs is not None:
                errs.append(f"{what}: {str(e)[:100]}")
            return default

    def _show(self, obj: Any) -> bool | None:
        r = self._safe(lambda: self._run(_SHOW_VBS, [self.doc, obj]))
        if r is None:
            return None
        return int(list(r)[0]) == 0

    # -- part
    def read_part(self) -> dict[str, Any]:
        part = self.part = self.doc.Part
        raw: dict[str, Any] = {
            "document": {"name": self._safe(lambda: self.doc.Name), "full_name": self._safe(lambda: self.doc.FullName),
                         "type": "Part"},
            "part": {"name": part.Name, "density": self._safe(lambda: float(part.Density))},
            "bodies": [], "hybrid_bodies": [], "parameters": [], "relations": [], "errors": self.errors,
        }
        main_name = self._safe(lambda: part.MainBody.Name)
        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            b = bodies.Item(i)
            raw["bodies"].append(self._read_body(b, b.Name == main_name))
        hbs = self._safe(lambda: part.HybridBodies)
        if hbs is not None:
            for i in range(1, hbs.Count + 1):
                hb = hbs.Item(i)
                els = []
                hs = self._safe(lambda: hb.HybridShapes)
                for j in range(1, (hs.Count if hs is not None else 0) + 1):
                    h = hs.Item(j)
                    els.append({"name": h.Name, "type": type_name(h)})
                raw["hybrid_bodies"].append({"name": hb.Name, "elements": els, "visible": self._show(hb)})
        raw["parameters"] = self._read_parameters()
        raw["relations"] = self._read_relations()
        if self.measure:
            raw["measures"] = self.measures(raw)
        return raw

    def _read_parameters(self) -> list[dict[str, Any]]:
        """User parameters: the ones of the root parameter set (feature-internal ones are not listed)."""
        out: list[dict[str, Any]] = []
        errs: list[str] = []
        ps = self._safe(lambda: self.part.Parameters.RootParameterSet.AllParameters, errs, "parameters")
        if ps is None:
            self.errors += errs
            return out
        for i in range(1, ps.Count + 1):
            p = ps.Item(i)
            name = self._safe(lambda: p.Name)
            entry: dict[str, Any] = {"name": name, "type": type_name(p)}
            entry["value"] = self._safe(lambda: p.Value)
            entry["value_text"] = self._safe(lambda: p.ValueAsString())
            entry["comment"] = self._safe(lambda: p.Comment) or None
            rel = self._safe(lambda: p.OptionalRelation)
            entry["formula"] = self._safe(lambda: rel.Value) if rel is not None else None
            out.append(entry)
        return out

    def _read_relations(self) -> list[dict[str, Any]]:
        out = []
        rels = self._safe(lambda: self.part.Relations)
        for i in range(1, (rels.Count if rels is not None else 0) + 1):
            r = rels.Item(i)
            out.append({"name": self._safe(lambda: r.Name), "type": type_name(r),
                        "text": self._safe(lambda: r.Value), "comment": self._safe(lambda: r.Comment) or None,
                        "active": self._safe(lambda: bool(r.Activated))})
        return out

    def _operand(self, shape: Any) -> Any | None:
        try:
            b = shape.Body
            _ = b.Shapes
            return b
        except Exception:
            return None

    def _read_body(self, body: Any, is_main: bool) -> dict[str, Any]:
        shapes = body.Shapes
        shape_objs = [shapes.Item(i) for i in range(1, shapes.Count + 1)]
        shape_names = {s.Name for s in shape_objs}
        operands = {s.Name: self._operand(s) for s in shape_objs}
        # Body.Sketches also lists the sketches of absorbed operand bodies (proven live)
        own_sketches = {body.Sketches.Item(i).Name for i in range(1, body.Sketches.Count + 1)}
        for op in operands.values():
            if op is not None:
                own_sketches -= {op.Sketches.Item(j).Name for j in range(1, op.Sketches.Count + 1)}
        sketches = []
        for i in range(1, body.Sketches.Count + 1):
            sk = body.Sketches.Item(i)
            if sk.Name in own_sketches:
                sketches.append(self._read_sketch(sk))
        hybrid = []
        hs = self._safe(lambda: body.HybridShapes)
        for i in range(1, (hs.Count if hs is not None else 0) + 1):
            h = hs.Item(i)
            if h.Name not in shape_names:
                hybrid.append({"name": h.Name, "type": type_name(h)})
        features = []
        for idx, s in enumerate(shape_objs, 1):
            f = self._read_feature(s, idx)
            op = operands.get(s.Name)
            if op is not None:
                f["operand"] = self._read_body(op, False)
            features.append(f)
        info: dict[str, Any] = {"name": body.Name, "main": is_main, "shapes": features, "sketches": sketches,
                                "hybrid_shapes": hybrid, "visible": self._show(body)}
        if self.measure and shapes.Count:
            info["volume_mm3"] = self._safe(lambda: self._body_volume(body))
        return info

    def _body_volume(self, body: Any) -> float:
        spa = self.doc.GetWorkbench("SPAWorkbench")
        return round(spa.GetMeasurable(self.part.CreateReferenceFromObject(body)).Volume * 1e9, 3)

    # -- sketches
    def _read_sketch(self, sk: Any) -> dict[str, Any]:
        errs: list[str] = []
        try:
            data = list(self._run(_SKETCH_VBS, [sk]))
        except Exception as e:
            reason = f"sketch geometry could not be read: {str(e)[:120]}"
            self.errors.append(f"{sk.Name}: {reason}")
            return {"name": sk.Name, "axis": [0.0] * 9, "elements": [], "constraints": [], "center_line": None,
                    "visible": self._show(sk), "errors": [reason], "unreadable": reason}
        axis = [_f(v) for v in data[:9]]
        elements: list[dict[str, Any]] = []
        n = (len(data) - 9) // _STRIDE
        for q in range(n):
            rec = data[9 + _STRIDE * q: 9 + _STRIDE * (q + 1)]
            elements.append(self._element(rec))
        elements = [e for e in elements if e["kind"] != "axis"]
        out: dict[str, Any] = {"name": sk.Name, "axis": axis, "elements": elements, "constraints": [],
                               "center_line": None, "visible": self._show(sk), "errors": errs}
        try:
            csts = sk.Constraints
            out["broken_constraints"] = self._safe(lambda: csts.BrokenConstraintsCount)
            out["unupdated_constraints"] = self._safe(lambda: csts.UnUpdatedConstraintsCount)
            for i in range(1, csts.Count + 1):
                out["constraints"].append(self._read_sketch_constraint(csts.Item(i)))
        except Exception as e:
            errs.append(f"constraints: {str(e)[:100]}")
        out["center_line"] = self._read_center_line(sk, elements)
        return out

    def _element(self, rec: list[Any]) -> dict[str, Any]:
        gt = int(_f(rec[0]))
        name = str(rec[14]) if rec[14] not in (None, 0) else f"element_{gt}"
        e: dict[str, Any] = {"name": name, "construction": bool(_f(rec[1])), "kind": "other"}
        v = [_f(x) for x in rec[2:14]]
        if gt == 1:
            e["kind"] = "axis"
        elif gt in (2, 4):
            e.update(kind="point", at=[v[0], v[1]])
        elif gt == 3:
            e.update(kind="line", start=[v[0], v[1]], end=[v[2], v[3]])
        elif gt == 5:
            r = v[2]
            span = v[4] - v[3]
            if r <= 0:
                e["kind"] = "other"
            elif abs(span - 2 * math.pi) < 1e-6 or abs(span) < 1e-12:
                e.update(kind="circle", center=[v[0], v[1]], radius=r)
            else:
                e.update(kind="arc", center=[v[0], v[1]], radius=r, a0=v[3], a1=v[4],
                         start=[v[5], v[6]], end=[v[7], v[8]])
        elif gt == 8:
            e.update(kind="ellipse", center=[v[0], v[1]], major_radius=v[2], minor_radius=v[3], major_axis=[v[4], v[5]])
        elif gt == 9:
            e.update(kind="spline", n_control_points=int(v[0]))
        return e

    def _read_center_line(self, sk: Any, elements: list[dict[str, Any]]) -> dict[str, Any] | None:
        cl = self._safe(lambda: sk.CenterLine)
        if cl is None:
            return None

        def dn(o: Any) -> str | None:
            return self._safe(lambda: self.part.CreateReferenceFromObject(o).DisplayName)

        name = dn(cl)
        if name is None:
            return {"kind": "unknown"}
        ab = self._safe(lambda: sk.AbsoluteAxis)
        h = dn(ab.HorizontalReference) if ab is not None else None
        v = dn(ab.VerticalReference) if ab is not None else None
        if name == h:
            return {"kind": "h", "name": name}
        if name == v:
            return {"kind": "v", "name": name}
        if any(e["name"] == name for e in elements):
            return {"kind": "line", "name": name}
        return {"kind": "unknown", "name": name}

    def _read_sketch_constraint(self, c: Any) -> dict[str, Any]:
        t = int(self._safe(lambda: c.Type, default=-1))
        row: dict[str, Any] = {
            "name": self._safe(lambda: c.Name), "type": t, "type_name": CST_TYPES.get(t, f"type_{t}"),
            "mode": CST_MODE.get(self._safe(lambda: c.Mode), "unknown"),
            "status": CST_STATUS.get(self._safe(lambda: c.Status), "unknown"),
            "inactive": self._safe(lambda: bool(c.IsInactive())),
            "elements": [],
        }
        if t in (1, 5, 6, 7, 14, 18, 19, 26):
            row["value"] = self._safe(lambda: float(c.Dimension.Value))
        for n in (1, 2, 3):
            ref = self._safe(lambda: c.GetConstraintElement(n))
            if ref is None:
                break
            row["elements"].append(self._safe(lambda: ref.DisplayName, default="?"))
        return row

    # -- features
    def _read_feature(self, s: Any, index: int) -> dict[str, Any]:
        typ = type_name(s)
        errs: list[str] = []
        f: dict[str, Any] = {
            "name": s.Name, "type": typ, "index": index,
            "up_to_date": self._safe(lambda: bool(self.part.IsUpToDate(s))),
            "inactive": self._safe(lambda: bool(self.part.IsInactive(s)), default=False),
            "visible": self._show(s), "sketch": None, "data": {}, "read_errors": errs,
        }
        kind = KIND_OF_TYPE.get(typ)
        d = f["data"]
        g = self._safe
        if kind in ("pad", "pocket"):
            f["sketch"] = g(lambda: s.Sketch.Name, errs, "Sketch")
            d.update(direction_type=g(lambda: s.DirectionType, errs, "DirectionType"),
                     orientation=g(lambda: s.DirectionOrientation, errs, "DirectionOrientation"),
                     symmetric=g(lambda: bool(s.IsSymmetric), errs, "IsSymmetric"),
                     thin=g(lambda: bool(s.IsThin), errs, "IsThin"),
                     l1_mode=g(lambda: s.FirstLimit.LimitMode, errs, "FirstLimit.LimitMode"),
                     l1_value=g(lambda: float(s.FirstLimit.Dimension.Value), errs, "FirstLimit.Dimension"),
                     l2_mode=g(lambda: s.SecondLimit.LimitMode, errs, "SecondLimit.LimitMode"),
                     l2_value=g(lambda: float(s.SecondLimit.Dimension.Value), errs, "SecondLimit.Dimension"))
            if d["l1_mode"] != 0:
                errs[:] = [e for e in errs if "FirstLimit.Dimension" not in e]
            if d["l2_mode"] not in (0, None):
                errs[:] = [e for e in errs if "SecondLimit.Dimension" not in e]
        elif kind in ("shaft", "groove"):
            f["sketch"] = g(lambda: s.Sketch.Name, errs, "Sketch")
            d.update(angle1=g(lambda: float(s.FirstAngle.Value), errs, "FirstAngle"),
                     angle2=g(lambda: float(s.SecondAngle.Value), errs, "SecondAngle"),
                     thin=g(lambda: bool(s.IsThin), errs, "IsThin"))
        elif kind == "hole":
            f["sketch"] = g(lambda: s.Sketch.Name, errs, "Sketch")
            d.update(type=g(lambda: s.Type, errs, "Type"), anchor_mode=g(lambda: s.AnchorMode, errs, "AnchorMode"),
                     bottom_type=g(lambda: s.BottomType, errs, "BottomType"),
                     threading_mode=g(lambda: s.ThreadingMode, errs, "ThreadingMode"),
                     diameter=g(lambda: float(s.Diameter.Value), errs, "Diameter"),
                     bottom_limit_mode=g(lambda: s.BottomLimit.LimitMode, errs, "BottomLimit.LimitMode"),
                     depth=g(lambda: float(s.BottomLimit.Dimension.Value), errs, "BottomLimit.Dimension"))
            if d["type"] in (1, 3):
                d["head_angle"] = g(lambda: float(s.HeadAngle.Value), errs, "HeadAngle")
            if d["type"] in (2, 3):
                d["head_depth"] = g(lambda: float(s.HeadDepth.Value), errs, "HeadDepth")
            if d["type"] == 2:
                d["head_diameter"] = g(lambda: float(s.HeadDiameter.Value), errs, "HeadDiameter")
            if d["bottom_limit_mode"] != 0:
                errs[:] = [e for e in errs if "BottomLimit.Dimension" not in e]
            if d["bottom_limit_mode"] == 3:
                ref = g(lambda: s.BottomLimit.LimitingElement, errs, "BottomLimit.LimitingElement")
                info = g(lambda: geometry.face_info(self.app, ref)) if ref is not None else None
                if info and info.get("plane"):
                    d["up_to_point"] = g(lambda: geometry.inside_point(self.app, self.doc, ref, info))
            hv = g(lambda: list(self._run(_HOLE_VBS, [s])), errs, "GetOrigin")
            if hv:
                d["origin"] = [_f(x) for x in hv[:3]]
                d["direction"] = [_f(x) for x in hv[3:6]]
        elif kind == "fillet":
            d.update(radius=g(lambda: float(s.Radius.Value), errs, "Radius"),
                     propagation=g(lambda: s.EdgePropagation, errs, "EdgePropagation"))
            d["points"], d["unreadable_edges"] = self._edge_points(g(lambda: s.ObjectsToFillet, errs, "ObjectsToFillet"))
        elif kind == "chamfer":
            d.update(mode=g(lambda: s.Mode, errs, "Mode"), propagation=g(lambda: s.Propagation, errs, "Propagation"),
                     orientation=g(lambda: s.Orientation, errs, "Orientation"),
                     length1=g(lambda: float(s.Length1.Value), errs, "Length1"),
                     length2=g(lambda: float(s.Length2.Value), errs, "Length2"),
                     angle=g(lambda: float(s.Angle.Value), errs, "Angle"))
            d["points"], d["unreadable_edges"] = self._edge_points(
                g(lambda: s.ElementsToChamfer, errs, "ElementsToChamfer"))
        elif kind == "mirror":
            mp = g(lambda: s.MirroringPlane, errs, "MirroringPlane")
            label = g(lambda: mp.DisplayName) if mp is not None else None
            d["plane_label"] = label
            hits = re.findall(r"(?<![A-Za-z])(xy|yz|zx)(?![A-Za-z])", (label or "").lower())
            d["plane"] = hits[0] if len(set(hits)) == 1 else None
        elif kind == "rect_pattern":
            d.update(item=g(lambda: s.ItemToCopy.Name), count1=g(lambda: int(s.FirstDirectionRepartition.InstancesCount.Value)),
                     spacing1=g(lambda: float(s.FirstDirectionRepartition.Spacing.Value)),
                     count2=g(lambda: int(s.SecondDirectionRepartition.InstancesCount.Value)),
                     spacing2=g(lambda: float(s.SecondDirectionRepartition.Spacing.Value)))
        elif kind == "circ_pattern":
            d.update(item=g(lambda: s.ItemToCopy.Name), count=g(lambda: int(s.AngularRepartition.InstancesCount.Value)),
                     angular_spacing_deg=g(lambda: float(s.AngularRepartition.AngularSpacing.Value)))
        elif kind == "shell":
            d.update(inner_thickness=g(lambda: float(s.InternalThickness.Value)),
                     outer_thickness=g(lambda: float(s.ExternalThickness.Value)),
                     faces=g(lambda: s.FacesToRemove.Count))
        elif kind == "thickness":
            d.update(offset=g(lambda: float(s.Offset.Value)), faces=g(lambda: s.FacesToThicken.Count))
        elif kind == "thread":
            d.update(diameter=g(lambda: float(s.Diameter)), pitch=g(lambda: float(s.Pitch)),
                     depth=g(lambda: float(s.Depth)))
        return f

    def _edge_points(self, coll: Any) -> tuple[list[list[float]], int]:
        """Middle point of every selected edge, and how many selections could not be read as an edge."""
        pts: list[list[float]] = []
        bad = 0
        if coll is None:
            return pts, 0
        for i in range(1, coll.Count + 1):
            ref = self._safe(lambda: coll.Item(i))
            v = self._safe(lambda: list(self._run(_EDGE_VBS, [self.doc, ref])))
            if not v:
                bad += 1
                continue
            pts.append([_f(x) for x in v[4:7]])
        return pts, bad

    # -- measurements
    def measures(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Volume, area, COG, bounding box, inertia of the result (main) body, plus each other body."""
        out: dict[str, Any] = {"bodies": {}}
        part = self.part
        density = raw["part"].get("density")
        for b in raw["bodies"]:
            body = self._safe(lambda: part.Bodies.Item(b["name"]))
            if body is None or not b["shapes"]:
                continue
            m = self._measure_body(body, density)
            out["bodies"][b["name"]] = m
            if b.get("main"):
                out["result"] = {"body": b["name"], **m}
        return out

    def _measure_body(self, body: Any, density: float | None) -> dict[str, Any]:
        errs: list[str] = []
        m: dict[str, Any] = {}
        info = self._safe(lambda: geometry.mass_info(self.app, self.part, body), errs, "mass")
        if info:
            m.update(volume_mm3=info["volume_mm3"], area_mm2=info["area_mm2"], cog_mm=info["cog"])
        if self.bbox_max_faces:
            nf = self._safe(lambda: len(geometry.topology(self.doc, body, "face")), errs, "faces")
            if nf is not None and nf <= self.bbox_max_faces:
                bb = self._safe(lambda: geometry.bounding_box(self.app, body), errs, "bbox")
                if bb:
                    m["bbox"] = bb
            elif nf is not None:
                m["bbox_skipped"] = f"{nf} faces (limit {self.bbox_max_faces}): exact box costs ~0.25 s per face"
        iv = self._safe(lambda: list(self._run(_INERTIA_VBS, [self.doc, body])),
                        errs, "inertia")
        if iv:
            iv = [_f(x) for x in iv]
            k = 1e6  # kg.m2 -> kg.mm2
            m["inertia"] = {
                "density_kg_m3": density,
                "mass_kg": _r(iv[24], 9),
                "matrix_kg_mm2": [_r(x * k, 6) for x in iv[0:9]],
                "principal_moments_kg_mm2": [_r(x * k, 6) for x in iv[12:15]],
                "principal_axes": [_r(x, 6) for x in iv[15:24]],
                "cog_mm": [_r(x * 1000, 4) for x in iv[9:12]],
            }
        if errs:
            m["errors"] = errs
        return m

    # -- products
    def read_product(self) -> dict[str, Any]:
        root = self.doc.Product
        raw: dict[str, Any] = {
            "document": {"name": self._safe(lambda: self.doc.Name), "full_name": self._safe(lambda: self.doc.FullName),
                         "type": "Product"},
            "product": {"name": root.Name, "part_number": self._safe(lambda: root.PartNumber)},
            "components": [], "constraints": [], "errors": self.errors,
        }

        def walk(node: Any, prefix: str) -> None:
            products = node.Products
            for i in range(1, products.Count + 1):
                c = products.Item(i)
                path = f"{prefix}/{c.Name}" if prefix else c.Name
                pos = self._safe(lambda: [_f(v) for v in self._run(_POSITION_VBS, [c])])
                nchild = self._safe(lambda: c.Products.Count, default=0)
                entry = {
                    "path": path, "instance": c.Name, "part_number": self._safe(lambda: c.PartNumber),
                    "file": self._safe(lambda: c.ReferenceProduct.Parent.FullName),
                    "is_assembly": bool(nchild), "children_count": nchild,
                    "pose": ({"origin": [_r(v, 4) for v in pos[9:12]],
                              "axes": [[_r(v, 6) for v in pos[k:k + 3]] for k in (0, 3, 6)]} if pos else None),
                }
                raw["components"].append(entry)
                if nchild:
                    walk(c, path)

        walk(root, "")
        raw["constraints"] = self._read_product_constraints(root, root.Name, "")
        for comp in raw["components"]:
            if comp["is_assembly"]:
                node = root
                for part_name in comp["path"].split("/"):
                    node = node.Products.Item(part_name)
                raw["constraints"] += self._read_product_constraints(node, root.Name, comp["path"])
        return raw

    def _read_product_constraints(self, node: Any, root_name: str, owner: str) -> list[dict[str, Any]]:
        out = []
        cons = self._safe(lambda: node.Connections("CATIAConstraints"))
        if cons is None:
            return out
        for i in range(1, cons.Count + 1):
            c = cons.Item(i)
            t = int(self._safe(lambda: c.Type, default=-1))
            refs = []
            for n in (1, 2, 3):
                r = self._safe(lambda: c.GetConstraintElement(n))
                if r is None:
                    break
                refs.append(self._safe(lambda: r.DisplayName, default=""))
            row = {
                "name": self._safe(lambda: c.Name), "type": t, "type_name": CST_TYPES.get(t, f"type_{t}"),
                "status": CST_STATUS.get(self._safe(lambda: c.Status), "unknown"),
                "inactive": self._safe(lambda: bool(c.IsInactive())), "owner": owner or root_name,
                "references": refs, "instances": [_instance_of(x, root_name) for x in refs],
            }
            if t in (1, 6, 7):
                row["value"] = self._safe(lambda: float(c.Dimension.Value))
            out.append(row)
        return out


# ============================================================================================
# The tool class
# ============================================================================================


class ReverseTools:
    """Reverse-engineering tools: describe, audit, measure the active document (read-only)."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_describe_model",
                "description": (
                    "Reverse-engineer the ACTIVE document without modifying it. CATPart: bodies, features in tree "
                    "order, sketches with exact geometry (mm, sketch axes) and constraints, parameters, plane "
                    "frames, volume, bounding box, inertia, plus the source of a replayable PartScript that rebuilds "
                    "the part in a NEW document. A feature the reader does not understand is listed under 'opaque' "
                    "(name, type, what is known, why) and the spec says replay.complete=false: nothing is guessed. "
                    "CATProduct: components, poses, constraints and statuses. Limits: sketch constraints are "
                    "described, not replayed; patterns, shell, draft, thickness and surfaces are opaque; edges are "
                    "reproduced by a point on them. Units mm and degrees. Set output_dir to write "
                    "<name>_spec.json and <name>_replay.py."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "output_dir": {"type": "string", "description": "Folder to write the spec JSON and the replay "
                                                                        "script into (created if needed)."},
                        "replay_name": {"type": "string",
                                        "description": "Part name of the replay (default '<part>_Replay')."},
                        "include_script": {"type": "boolean", "default": True,
                                           "description": "Return the script source in the answer (it is written to "
                                                          "output_dir either way)."},
                        "include_spec": {"type": "boolean", "default": True,
                                         "description": "Return the full spec in the answer (false = summary only, "
                                                        "useful for big models; the file is written either way)."},
                        "measure": {"type": "boolean", "default": True,
                                    "description": "Measure volume, box and inertia (the exact bounding box costs ~0.25 s "
                                                   "per face; parts above 400 faces skip it)."},
                        "validate": {"type": "boolean", "default": True,
                                     "description": "Dry-run the generated script against the tool schemas."},
                    },
                },
            },
            {
                "name": "catia_audit_model",
                "description": (
                    "Audit the ACTIVE document's tree WITHOUT modifying it. Returns issues ranked error > warning > "
                    "info, each with a code, where, message and the exact tool calls that fix it ('fix', a list of "
                    "{tool, args}) or a 'manual' instruction when no tool can. Part: default names, bodies without "
                    "result or empty, hidden or unmerged bodies, features not up to date or deactivated, sketches "
                    "unused, open profiles, unsatisfied constraints, order (dress-up before material). Assembly: "
                    "constraints not OK, default names, components not constrained outside the fixed one, broken "
                    "links. 'not_checked' says what the API cannot tell (degrees of freedom of a sketch)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "min_severity": {"type": "string", "enum": list(SEVERITIES), "default": "info",
                                         "description": "Hide issues below this severity."},
                    },
                },
            },
            {
                "name": "catia_measure_model",
                "description": (
                    "Measure each body of the ACTIVE part: volume (mm3), area, centre of gravity, exact bounding "
                    "box (mm) and inertia (principal moments and matrix at the COG, kg.mm2, and mass at the part "
                    "density). Read-only. Use it to compare an original with its replay."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "with_bbox": {"type": "boolean", "default": True,
                                      "description": "Compute the exact bounding box (slow on parts with many faces)."},
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_describe_model":
                return self._describe(arguments)
            case "catia_audit_model":
                return self._audit(arguments)
            case "catia_measure_model":
                return self._measure(arguments)
            case _:
                raise ValueError(f"Unknown reverse tool: {tool_name}")

    # -- helpers
    def _reader(self, measure: bool = True, bbox: bool = True) -> tuple[ModelReader, str]:
        self.conn.ensure_connected()
        doc = self.conn.active_document
        tn = type_name(doc)
        if tn == "ProductDocument":
            kind = "Product"
        elif tn == "PartDocument":
            kind = "Part"
        else:
            kind = "Product" if self._has(doc, "Product") and not self._has(doc, "Part") else "Part"
        return ModelReader(self.conn.app, doc, measure=measure, bbox_max_faces=400 if bbox else 0), kind

    @staticmethod
    def _has(doc: Any, attr: str) -> bool:
        try:
            getattr(doc, attr)
            return True
        except Exception:
            return False

    def _describe(self, args: dict[str, Any]) -> str:
        reader, kind = self._reader(bool(args.get("measure", True)))
        if kind == "Product":
            raw = reader.read_product()
            spec = {"format": FORMAT, "kind": "product", "document": raw["document"], "product": raw["product"],
                    "components": raw["components"], "constraints": raw["constraints"], "opaque": [],
                    "notes": ["no replay script is generated for assemblies (AssemblyScript needs stable face/axis "
                              "designation points per part, which cannot be derived from the constraint references)"],
                    "read_errors": raw["errors"]}
            audit = audit_product(raw)
            spec["audit_summary"] = audit["stats"]
            return self._finish(spec, None, args, raw["product"]["name"])
        raw = reader.read_part()
        if not raw["bodies"]:
            raise RuntimeError("The part has no body: nothing to describe.")
        spec = build_spec(raw, args.get("replay_name"))
        script = render_script(spec)
        replay: dict[str, Any] = spec["replay"]
        if args.get("validate", True):
            from catia_mcp.scripting import load_schemas

            try:
                schemas = load_schemas()
            except Exception as e:  # pragma: no cover - server import problems
                schemas, replay["validation_note"] = None, f"schemas unavailable: {e}"
            replay["validation"] = validate_replay(spec, schemas)
        spec["read_errors"] = raw["errors"]
        return self._finish(public_spec(spec), script, args, raw["part"]["name"])

    def _finish(self, spec: dict[str, Any], script: str | None, args: dict[str, Any], name: str) -> str:
        files: list[str] = []
        out_dir = args.get("output_dir")
        if out_dir:
            d = Path(out_dir).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            stem = sanitize_name(name)
            jp = d / f"{stem}_spec.json"
            jp.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
            files.append(str(jp))
            if script:
                sp = d / f"{stem}_replay.py"
                sp.write_text(script, encoding="utf-8")
                files.append(str(sp))
        if spec["kind"] == "part":
            n_feat = sum(len(b["features"]) for b in spec["bodies"])
            summary = {
                "part": spec["part"]["name"], "bodies": len(spec["bodies"]), "features": n_feat,
                "sketches": len(spec["sketches"]), "opaque": len(spec["opaque"]),
                "opaque_solid_features": spec["replay"]["opaque_solid_features"],
                "replay_complete": spec["replay"]["complete"],
                "replay_valid": (spec["replay"].get("validation") or {}).get("valid"),
                "read_errors": len(spec.get("read_errors") or []),
            }
        else:
            summary = {"product": spec["product"]["name"], "components": len(spec["components"]),
                       "constraints": len(spec["constraints"]), "read_errors": len(spec.get("read_errors") or []),
                       **spec["audit_summary"]}
        out: dict[str, Any] = {"summary": summary, "opaque": spec["opaque"], "files": files}
        if args.get("include_spec", True):
            out["spec"] = spec
        if script and args.get("include_script", True):
            out["script"] = script
        return json.dumps(out, indent=1, ensure_ascii=False, default=str)

    def _audit(self, args: dict[str, Any]) -> str:
        reader, kind = self._reader(measure=True, bbox=False)
        raw = reader.read_product() if kind == "Product" else reader.read_part()
        res = audit_product(raw) if kind == "Product" else audit_part(raw)
        floor = SEVERITIES.index(args.get("min_severity", "info"))
        res["issues"] = [i for i in res["issues"] if SEVERITIES.index(i["severity"]) <= floor]
        res["document"] = raw["document"]
        res["read_errors"] = raw["errors"]
        return json.dumps(res, indent=1, ensure_ascii=False, default=str)

    def _measure(self, args: dict[str, Any]) -> str:
        reader, kind = self._reader(measure=False, bbox=bool(args.get("with_bbox", True)))
        if kind != "Part":
            raise RuntimeError("catia_measure_model works on a part; the active document is a product.")
        part = reader.part = reader.doc.Part
        density = reader._safe(lambda: float(part.Density))
        bodies = part.Bodies
        main = reader._safe(lambda: part.MainBody.Name)
        out: dict[str, Any] = {"part": part.Name, "density_kg_m3": density, "bodies": {}}
        for i in range(1, bodies.Count + 1):
            b = bodies.Item(i)
            if b.Shapes.Count == 0:
                out["bodies"][b.Name] = {"empty": True}
                continue
            m = reader._measure_body(b, density)
            out["bodies"][b.Name] = m
            if b.Name == main:
                out["result"] = {"body": b.Name, **m}
        return json.dumps(out, indent=1, ensure_ascii=False)


__all__ = [
    "FORMAT", "ModelReader", "ReverseTools", "apply_ops", "audit_part", "audit_product", "build_spec",
    "chain_profiles", "classify_elements", "compare_measures", "frame_to_plane", "open_ends", "public_spec",
    "render_script", "replay_name", "sanitize_name", "type_name", "validate_replay",
]
