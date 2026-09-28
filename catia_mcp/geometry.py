"""Designate faces/edges of a solid by a 3D point, and measure them.

Why this module exists (all proven live on CATIA V5 R19, 2026-09-28 — see
_project_meta/CATIA_COM_PITFALLS.md §4-§6):
- Topology search must use "Topology.CGMFace"/"Topology.CGMEdge"; the
  "Topology.Face"/"Topology.Edge" spelling always fails.
- From pywin32 late binding, SPAWorkbench measurable methods with ByRef array
  outputs (GetCOG, GetPlane...) silently return zeros. Running a small VBScript
  inside CATIA via SystemService.Evaluate works and returns real values.
- A face/edge is designated by the point it passes through: a temporary
  (not-in-tree) HybridShape point + Measurable.GetMinimumDistance to every
  candidate; distance 0 = hit. This lets every feature that needs a face or an
  edge (sketch on face, hole, fillet, chamfer, draft...) be driven by
  coordinates, which is what an agent reading a drawing actually has.
"""

from __future__ import annotations

import re
from typing import Any

# CATScriptLanguage.CATVBScriptLanguage
_VBSCRIPT = 0

_FACE_INFO_VBS = """
Function CATMain(ref)
  ' out: 0-2 COG, 3 area mm2, 4-12 plane (origin, dir1, dir2) or zeros,
  ' 13 radius (cylinder/cone/sphere) or 0, 14-16 axis direction,
  ' 17-25 GetPointsOnAxis (3 points on the axis), 26 GeometryName.
  Dim spa: Set spa = CATIA.ActiveDocument.GetWorkbench("SPAWorkbench")
  Dim m: Set m = spa.GetMeasurable(ref)
  Dim out(26), k
  For k = 0 To 25
    out(k) = 0
  Next
  Dim c(2)
  m.GetCOG c
  out(0) = c(0): out(1) = c(1): out(2) = c(2)
  out(3) = m.Area * 1000000
  On Error Resume Next
  out(26) = m.GeometryName
  Dim p(8)
  m.GetPlane p
  If Err.Number = 0 Then
    For k = 0 To 8
      out(4 + k) = p(k)
    Next
  End If
  Err.Clear
  out(13) = m.Radius
  If Err.Number <> 0 Then out(13) = 0
  Err.Clear
  Dim a(2)
  m.GetAxis a
  If Err.Number = 0 Then
    out(14) = a(0): out(15) = a(1): out(16) = a(2)
  End If
  Err.Clear
  Dim q(8)
  m.GetPointsOnAxis q
  If Err.Number = 0 Then
    For k = 0 To 8
      out(17 + k) = q(k)
    Next
  End If
  Err.Clear
  CATMain = out
End Function
"""

_EDGE_INFO_VBS = """
Function CATMain(ref)
  Dim spa: Set spa = CATIA.ActiveDocument.GetWorkbench("SPAWorkbench")
  Dim m: Set m = spa.GetMeasurable(ref)
  Dim out(9)
  out(0) = m.Length
  Dim p(8)
  m.GetPointsOnCurve p
  Dim k
  For k = 0 To 8
    out(1 + k) = p(k)
  Next
  CATMain = out
End Function
"""

# Tolerance (mm) under which a candidate is considered to pass through the point.
HIT_TOLERANCE = 0.01


def run_vbs(app: Any, code: str, args: list[Any]) -> Any:
    """Run a VBScript CATMain inside CATIA and return its result."""
    return app.SystemService.Evaluate(code, _VBSCRIPT, "CATMain", args)


def topology(doc: Any, container: Any, kind: str, solid_only: bool = True) -> list[Any]:
    """All faces ('face') or edges ('edge') of a feature or body, as references.

    solid_only drops the topology of wireframe/surface elements living in the
    body (e.g. an offset plane inserted as a sketch support shows up as an
    infinite "GSMPlane" face — proven live, it blew the bounding box up to
    ±100 m and could be picked instead of a real face).
    """
    cgm = {"face": "CGMFace", "edge": "CGMEdge", "vertex": "CGMVertex"}[kind]
    sel = doc.Selection
    sel.Clear()
    sel.Add(container)
    sel.Search(f"Topology.{cgm},sel")
    refs = []
    # Proven live on a 36-tooth sprocket: Selection.Item(i) fails for some
    # entries of a large search result; one bad entry must not abort the rest.
    for i in range(1, sel.Count + 1):
        try:
            refs.append(sel.Item(i).Reference)
        except Exception:
            pass
    sel.Clear()
    if solid_only:
        refs = [r for r in refs if "GSM" not in r.DisplayName]
    return refs


_PICK_VBS = """
Function CATMain(doc, container, kind, pts, tol)
  ' doc = the PartDocument owning `container` (not necessarily the active
  ' document: in an assembly the active one is the Product).
  ' One topology search, then for every candidate face/edge the distance to
  ' each still-unresolved point; stops as soon as every point has a hit.
  ' Everything runs inside CATIA: from Python, ~70 % of Selection.Item(i)
  ' .Reference calls fail on big results (proven on a 36-tooth sprocket) and
  ' each COM round trip costs time.
  Dim part: Set part = doc.Part
  Dim spa: Set spa = doc.GetWorkbench("SPAWorkbench")
  Dim sel: Set sel = doc.Selection
  Dim np: np = (UBound(pts) + 1) / 3
  Dim mps(), best(), bestRef(), found
  ReDim mps(np - 1): ReDim best(np - 1): ReDim bestRef(np - 1)
  Dim j, pt
  For j = 0 To np - 1
    Set pt = part.HybridShapeFactory.AddNewPointCoord(pts(3 * j), pts(3 * j + 1), pts(3 * j + 2))
    pt.Compute
    Set mps(j) = spa.GetMeasurable(part.CreateReferenceFromObject(pt))
    best(j) = 1E+30
    Set bestRef(j) = Nothing
  Next
  sel.Clear
  sel.Add container
  sel.Search "Topology." & kind & ",sel"
  Dim n: n = sel.Count
  Dim refs(): ReDim refs(n)
  Dim i, r, dn, d
  For i = 1 To n
    Set refs(i) = Nothing
    On Error Resume Next
    Set r = sel.Item(i).Reference
    If Err.Number = 0 Then
      dn = r.DisplayName
      ' Skip sketch wires and wireframe/surface elements (planes...).
      If InStr(dn, "Wire") = 0 And InStr(dn, "GSM") = 0 Then Set refs(i) = r
    End If
    Err.Clear
    On Error GoTo 0
  Next
  sel.Clear
  found = 0
  For i = n To 1 Step -1
    If Not refs(i) Is Nothing Then
      For j = 0 To np - 1
        If best(j) > tol Then
          On Error Resume Next
          d = mps(j).GetMinimumDistance(refs(i))
          If Err.Number = 0 Then
            If d < best(j) Then
              best(j) = d
              Set bestRef(j) = refs(i)
              If d <= tol Then found = found + 1
            End If
          End If
          Err.Clear
          On Error GoTo 0
        End If
      Next
      If found >= np Then Exit For
    End If
  Next
  Dim out(): ReDim out(2 * np - 1)
  For j = 0 To np - 1
    Set out(2 * j) = bestRef(j)
    out(2 * j + 1) = best(j)
  Next
  CATMain = out
End Function
"""


def pick_many(app: Any, doc: Any, container: Any, kind: str, points: list[Any]) -> list[tuple[Any, float]]:
    """[(reference, distance)] of the face/edge of `container` through each point.

    One topology search for all points. Raises if a point hits nothing within
    HIT_TOLERANCE (with the closest distance, so the caller can fix the
    coordinates instead of silently getting a wrong element). For a face, give
    a point INSIDE it: a point on a shared edge touches two faces and the first
    one found wins.
    """
    cgm = {"face": "CGMFace", "edge": "CGMEdge", "vertex": "CGMVertex"}[kind]
    flat = [float(c) for p in points for c in p]
    out = list(run_vbs(app, _PICK_VBS, [doc, container, cgm, flat, HIT_TOLERANCE]))
    result = []
    for j, p in enumerate(points):
        ref, d = out[2 * j], out[2 * j + 1]
        if ref is None or d > HIT_TOLERANCE:
            raise RuntimeError(
                f"No {kind} of '{container.Name}' passes through {tuple(p)} "
                + (f"(closest is {d:.3f} mm away). " if ref is not None else "(none found). ")
                + f"Give a point lying ON the {kind}"
                + (" (inside the face, not on its border)." if kind == "face" else ".")
            )
        result.append((ref, d))
    return result


def pick(app: Any, doc: Any, container: Any, kind: str, point: Any) -> tuple[Any, float]:
    """(reference, distance) of the face/edge of `container` through `point`."""
    return pick_many(app, doc, container, kind, [point])[0]


_INSIDE_VBS = """
Function CATMain(doc, face, cx, cy, cz, ux, uy, uz, vx, vy, vz, rmax)
  ' Spiral search, in the face's plane, from its centre of gravity outwards,
  ' for a point lying ON the face (distance 0). Stops at the first hit.
  ' Needed because the COG of a holed/annular face (flange, washer, rim) is in
  ' the hole (proven live on the engine front flange).
  Dim part: Set part = doc.Part
  Dim spa: Set spa = doc.GetWorkbench("SPAWorkbench")
  Dim hsf: Set hsf = part.HybridShapeFactory
  Dim k, a, r, t, x, y, z, pt, d
  Const PI = 3.14159265358979
  For k = 0 To 16
    r = rmax * k / 16
    For a = 0 To 15
      If k = 0 And a > 0 Then Exit For
      t = 2 * PI * a / 16 + k * 0.37
      x = cx + r * (Cos(t) * ux + Sin(t) * vx)
      y = cy + r * (Cos(t) * uy + Sin(t) * vy)
      z = cz + r * (Cos(t) * uz + Sin(t) * vz)
      Set pt = hsf.AddNewPointCoord(x, y, z)
      pt.Compute
      d = spa.GetMeasurable(part.CreateReferenceFromObject(pt)).GetMinimumDistance(face)
      If d < 0.001 Then
        CATMain = Array(x, y, z, 1)
        Exit Function
      End If
    Next
  Next
  CATMain = Array(0, 0, 0, 0)
End Function
"""


def inside_point(app: Any, doc: Any, ref: Any, info: dict[str, Any]) -> list[float] | None:
    """A point guaranteed ON a planar face (its COG may lie in a hole)."""
    plane = info.get("plane")
    if not plane:
        return None
    c = info["cog"]
    rmax = 1.5 * max(info["area_mm2"], 1.0) ** 0.5
    v = list(run_vbs(app, _INSIDE_VBS, [doc, ref, *map(float, c), *map(float, plane["dir1"]),
                                        *map(float, plane["dir2"]), float(rmax)]))
    return [round(float(x), 3) for x in v[:3]] if v[3] else None


def face_info(app: Any, ref: Any) -> dict[str, Any]:
    """COG (mm), area (mm²), type, and plane (planar face) or axis + radius
    (cylinder/cone...): what an agent needs to aim constraints, holes, sketches."""
    v = list(run_vbs(app, _FACE_INFO_VBS, [ref]))
    rnd = lambda xs, n=3: [round(float(c), n) for c in xs]
    plane = v[4:13]
    # Measurable.GeometryName codes observed live: 4 cylinder, 6 cone, 7 plane.
    kind = {4: "cylinder", 6: "cone", 7: "plane"}.get(v[26], f"surface({v[26]})")
    info: dict[str, Any] = {
        "type": kind,
        "cog": rnd(v[0:3]),
        "area_mm2": round(v[3], 2),
        "plane": None if not any(plane) else {
            "origin": rnd(plane[0:3]), "dir1": rnd(plane[3:6], 4), "dir2": rnd(plane[6:9], 4),
        },
    }
    if v[13]:
        info["radius"] = round(v[13], 3)
    if any(v[14:17]):
        # GetAxis returns a vector scaled by the face length: normalise it.
        norm = sum(float(c) ** 2 for c in v[14:17]) ** 0.5 or 1.0
        info["axis"] = {
            "direction": [round(float(c) / norm, 4) + 0.0 for c in v[14:17]],
            "point": rnd(v[17:20]),
        }
    return info


_MASS_VBS = """
Function CATMain(ref)
  ' Volume (m3), area (m2) and COG (mm) of a Body: plain Measurable calls on
  ' the Body reference, fast whatever the part complexity.
  Dim spa: Set spa = CATIA.ActiveDocument.GetWorkbench("SPAWorkbench")
  Dim m: Set m = spa.GetMeasurable(ref)
  Dim out(4), c(2)
  out(0) = m.Volume
  out(1) = m.Area
  m.GetCOG c
  out(2) = c(0): out(3) = c(1): out(4) = c(2)
  CATMain = out
End Function
"""

_BBOX_VBS = """
Function CATMain(body)
  ' Exact bounding box = distance from 6 far planes to every result face.
  ' Proven live: GetMinimumDistance fails on a Body reference (both ways), a
  ' feature reference only measures that feature's own shape, a GSD Extremum
  ' on a body cannot be measured, and there is no searchable volume topology.
  ' Cost grows with the face count (~0.25 s per face: 300 faces = ~75 s).
  Dim doc: Set doc = CATIA.ActiveDocument
  Dim part: Set part = doc.Part
  Dim spa: Set spa = doc.GetWorkbench("SPAWorkbench")
  Dim sel: Set sel = doc.Selection
  sel.Clear: sel.Add body
  sel.Search "Topology.CGMFace,sel"
  Dim n: n = sel.Count
  Dim faces(): ReDim faces(n)
  Dim i, r, cnt: cnt = 0
  For i = 1 To n
    On Error Resume Next
    Set r = sel.Item(i).Reference
    If Err.Number = 0 Then
      If InStr(r.DisplayName, "GSM") = 0 Then
        Set faces(cnt) = r
        cnt = cnt + 1
      End If
    End If
    Err.Clear
    On Error GoTo 0
  Next
  sel.Clear
  Dim hsf: Set hsf = part.HybridShapeFactory
  Dim out(5), k, s, sign, pl, mp, best, d
  For k = 0 To 2
    For s = 0 To 1
      sign = 2 * s - 1
      Set pl = hsf.AddNewPlaneEquation(Abs(k = 0), Abs(k = 1), Abs(k = 2), sign * 100000)
      pl.Compute
      Set mp = spa.GetMeasurable(part.CreateReferenceFromObject(pl))
      best = 1E+30
      For i = 0 To cnt - 1
        d = mp.GetMinimumDistance(faces(i))
        If d < best Then best = d
      Next
      out(2 * k + s) = sign * (100000 - best)
    Next
  Next
  CATMain = out
End Function
"""


def mass_info(app: Any, part: Any, body: Any) -> dict[str, Any]:
    """Volume (mm³), area (mm²) and centre of gravity (mm) of a body. Fast."""
    if body.Shapes.Count == 0:
        raise RuntimeError(f"Body '{body.Name}' has no solid to measure.")
    v = list(run_vbs(app, _MASS_VBS, [part.CreateReferenceFromObject(body)]))
    return {
        "volume_mm3": round(v[0] * 1e9, 3),
        "area_mm2": round(v[1] * 1e6, 3),
        "cog": [round(c, 3) for c in v[2:5]],
    }


def bounding_box(app: Any, body: Any) -> dict[str, Any]:
    """Exact bounding box (mm) of a body's solid, curved shapes included."""
    if body.Shapes.Count == 0:
        raise RuntimeError(f"Body '{body.Name}' has no solid to measure.")
    bb = [round(c, 3) for c in run_vbs(app, _BBOX_VBS, [body])]
    return {
        "x": [bb[0], bb[1]], "y": [bb[2], bb[3]], "z": [bb[4], bb[5]],
        "size": [round(bb[1] - bb[0], 3), round(bb[3] - bb[2], 3), round(bb[5] - bb[4], 3)],
    }


def edge_info(app: Any, ref: Any) -> dict[str, Any]:
    """Length and start/middle/end points (mm) of an edge."""
    v = list(run_vbs(app, _EDGE_INFO_VBS, [ref]))
    pts = [[round(c, 3) for c in v[1 + 3 * k: 4 + 3 * k]] for k in range(3)]
    return {"length": round(v[0], 3), "start": pts[0], "middle": pts[1], "end": pts[2]}


def pick_in_part(
    app: Any, doc: Any, part: Any, kind: str, point: Any, prefer: Any | None = None
) -> tuple[Any, Any]:
    """Pick a face/edge through `point`, searching `prefer` (a body) first, then
    every other body of the part. Returns (reference, owning_body)."""
    bodies = [part.Bodies.Item(i) for i in range(1, part.Bodies.Count + 1)]
    if prefer is not None:
        bodies.sort(key=lambda b: b.Name != prefer.Name)
    errors = []
    for body in bodies:
        try:
            if body.Shapes.Count == 0:
                continue
            ref, _ = pick(app, doc, body, kind, tuple(point))
            return ref, body
        except Exception as e:
            errors.append(f"{body.Name}: {e}")
    raise RuntimeError(f"No {kind} through {tuple(point)} in any body. " + " | ".join(errors))


# Sketch axes CATIA uses by default on each origin plane: (H, V, normal).
STD_AXES = {
    "xy": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "yz": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "zx": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
}


def parallel_origin_plane(info: dict[str, Any]) -> tuple[str, float] | None:
    """For a planar face, return (origin plane parallel to it, signed offset)."""
    plane = info.get("plane")
    if not plane:
        return None
    a, b = plane["dir1"], plane["dir2"]
    n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    o = plane["origin"]
    for key, (_, _, std_n) in STD_AXES.items():
        dot = sum(n[i] * std_n[i] for i in range(3))
        if abs(abs(dot) - 1) < 1e-6:
            return key, sum(o[i] * std_n[i] for i in range(3))
    return None


def sketch_axis_data(plane_key: str, offset: float) -> tuple[float, ...]:
    """SetAbsoluteAxisData payload: origin (projection of 0,0,0) + H + V."""
    h, v, n = STD_AXES[plane_key]
    origin = tuple(offset * c for c in n)
    return tuple(float(c) for c in (*origin, *h, *v))


def brep_reference(part: Any, selection_ref: Any, feature: Any, permanent: bool = False) -> Any:
    """Turn a transient selection reference into a stable BRep reference.

    Selection references ("Selection_RSur:(...);Pad.1_ResultOUT;Z0;G3055)") are
    only valid while the topology is unchanged; features that store their
    support (sketch on face, hole) need the BRep-name form.
    """
    s = selection_ref.DisplayName.replace("Selection_", "", 1)
    s = re.sub(r";[^;()]*_ResultOUT;Z\d+;G\d+\)$", "", s)
    body_mode = "WithPermanentBody" if permanent else "WithTemporaryBody"
    name = f"{s};{body_mode};WithoutBuildError;WithSelectingFeatureSupport;MFBRepVersion_CXR15)"
    return part.CreateReferenceFromBRepName(name, feature)
