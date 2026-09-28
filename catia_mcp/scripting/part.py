"""PartScript: a DSL that EMITS the tool calls needed to model one part (no COM, no CATIA).

Proven method, encoded as rules that are checked while you write the script:

* multi-body: base shape in ``<Part>_Resultat``, every functional zone in its own body, merged with
  ``combine`` (assemble / add / remove / intersect);
* everything is named explicitly (no ``Pad.1``): sketches ``Sk_<role>``, features ``<Role>_<dim>``,
  bodies ``Body_<zone>``;
* one sketch feeds exactly one feature; sketch edges are closed, non-self-intersecting profiles;
* revolutions use a half profile on ONE side of the axis;
* the end of the script measures (volume change of every feature, inertia, bounding box, naming
  audit) and ``checks()`` states what the drawing says so the run can compare.

Sketch axes (H, V) of the origin planes: xy -> H=X V=Y, yz -> H=Y V=Z, zx -> H=Z V=X. The sketch
normal (pad direction "normal") is +Z for xy, +X for yz, +Y for zx.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from catia_mcp.scripting._base import ScriptBase
from catia_mcp.scripting._common import (
    EPS,
    ScriptError,
    check_name,
    integer,
    num,
    one_of,
    point2,
    point3,
)

PLANES = ("xy", "yz", "zx")
DEFAULT_VIEWS = ("isometric", "isometric_back")
BOOLEAN_OPS = ("assemble", "add", "remove", "intersect", "union_trim")


# ── plane ───────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Plane:
    """Where a sketch lives: an origin plane (+ optional offset along its normal) or the planar
    face containing ``face_point`` (a point INSIDE the face, not on its border)."""

    kind: str | None = "xy"
    offset: float | None = None
    face_point: tuple[float, float, float] | None = None


# ── profile helpers (pure geometry, used for checks) ────────────────────────────────────
def line_seg(to: Sequence[float]) -> dict[str, Any]:
    """A straight segment of a ``profile`` (ends at ``to``)."""
    return {"type": "line", "to": point2(to, "line_seg.to")}


def arc_seg(to: Sequence[float], center: Sequence[float], direction: str = "ccw") -> dict[str, Any]:
    """An arc segment of a ``profile``: from the previous point to ``to`` around ``center``,
    travelling ``ccw`` or ``cw`` (a wrong direction draws the long way round the circle)."""
    one_of(direction, "arc_seg.direction", ("ccw", "cw"))
    return {"type": "arc", "to": point2(to, "arc_seg.to"), "center": point2(center, "arc_seg.center"),
            "direction": direction}


def profile_points(start: Sequence[float], segments: Sequence[dict[str, Any]],
                   max_chord: float = 0.5) -> list[tuple[float, float]]:
    """Points along a profile (arcs sampled every <=5 degrees AND <=0.5 mm of chord)."""
    cur = (float(start[0]), float(start[1]))
    pts = [cur]
    for s in segments:
        end = tuple(s["to"]) if "to" in s else (float(start[0]), float(start[1]))
        if s.get("type", "line") != "arc":
            pts.append(end)
            cur = end
            continue
        c = tuple(s["center"])
        r = math.dist(cur, c)
        a0 = math.atan2(cur[1] - c[1], cur[0] - c[0])
        a1 = math.atan2(end[1] - c[1], end[0] - c[0])
        if s.get("direction", "ccw") == "ccw":
            while a1 <= a0:
                a1 += 2 * math.pi
        else:
            while a1 >= a0:
                a1 -= 2 * math.pi
        n = max(2, int(abs(a1 - a0) / math.radians(5)) + 1, int(r * abs(a1 - a0) / max_chord) + 1)
        pts += [(c[0] + r * math.cos(a0 + (a1 - a0) * k / n), c[1] + r * math.sin(a0 + (a1 - a0) * k / n))
                for k in range(1, n + 1)]
        cur = end
    return pts


def polygon_area(pts: Sequence[Sequence[float]]) -> float:
    """Absolute area of a closed polygon (shoelace)."""
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def self_intersections(pts: Sequence[Sequence[float]], closed: bool = True) -> list[tuple[int, int]]:
    """Index pairs of non-adjacent segments that properly cross (touching ends do not count)."""
    n = len(pts)
    segs = [(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]
    bad = []
    for i in range(len(segs)):
        for j in range(i + 2, len(segs)):
            if closed and i == 0 and j == len(segs) - 1:
                continue
            a, b = segs[i]
            c, d = segs[j]
            d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
            if d1 * d2 < -1e-12 and d3 * d4 < -1e-12:
                bad.append((i, j))
    return bad


# ── sketch ──────────────────────────────────────────────────────────────────────────────
class Sketch:
    """An open sketch. Add geometry with the methods below; it closes when the next feature (or
    sketch) is created, on ``close()``, or at the end of a ``with`` block."""

    def __init__(self, script: PartScript, name: str, plane: Plane) -> None:
        self._s = script
        self.name = name
        self.plane = plane
        self.closed = False
        self.consumed = False
        self.count = 0
        self.axis_lines = 0
        self.points: list[tuple[float, float]] = []   # sampled outline points (for checks)
        self.areas: list[float] = []                  # area of each closed element

    def __enter__(self) -> Sketch:
        return self

    def __exit__(self, *exc: object) -> None:
        if exc[0] is None and not self.closed:
            self.close()

    def _emit(self, tool: str, args: dict[str, Any]) -> None:
        if self.closed:
            raise ScriptError(f"sketch '{self.name}' is already closed; open a new sketch.")
        if self._s._open is not self:
            raise ScriptError(f"sketch '{self.name}' is not the open sketch any more.")
        self._s._emit(tool, args)
        self.count += 1

    # elements ------------------------------------------------------------------------------
    def circle(self, cx: float, cy: float, radius: float, construction: bool = False) -> Sketch:
        cx, cy = num(cx, "circle.cx"), num(cy, "circle.cy")
        r = num(radius, "circle.radius (a RADIUS, not a diameter)", positive=True)
        args: dict[str, Any] = {"cx": cx, "cy": cy, "radius": r}
        if construction:
            args["construction"] = True
        else:
            self.areas.append(math.pi * r * r)
            self.points += [(cx + r, cy), (cx - r, cy), (cx, cy + r), (cx, cy - r)]
        self._emit("catia_sketch_circle", args)
        return self

    def rect(self, x1: float, y1: float, x2: float, y2: float) -> Sketch:
        v = [num(x1, "rect.x1"), num(y1, "rect.y1"), num(x2, "rect.x2"), num(y2, "rect.y2")]
        if abs(v[0] - v[2]) < EPS or abs(v[1] - v[3]) < EPS:
            raise ScriptError(f"rect: zero width or height ({v}).")
        self.areas.append(abs((v[2] - v[0]) * (v[3] - v[1])))
        self.points += [(v[0], v[1]), (v[2], v[1]), (v[2], v[3]), (v[0], v[3])]
        self._emit("catia_sketch_rectangle", dict(zip(("x1", "y1", "x2", "y2"), v)))
        return self

    def centered_rect(self, cx: float, cy: float, width: float, height: float) -> Sketch:
        cx, cy = num(cx, "centered_rect.cx"), num(cy, "centered_rect.cy")
        w, h = num(width, "centered_rect.width", positive=True), num(height, "centered_rect.height", positive=True)
        self.areas.append(w * h)
        self.points += [(cx - w / 2, cy - h / 2), (cx + w / 2, cy + h / 2)]
        self._emit("catia_sketch_centered_rectangle", {"cx": cx, "cy": cy, "width": w, "height": h})
        return self

    def poly(self, points: Iterable[Sequence[float]]) -> Sketch:
        """Closed polygon through ``points`` [(h, v), ...] (>= 3, no self-crossing)."""
        pts = [point2(p, f"poly.points[{i}]") for i, p in enumerate(points)]
        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts = pts[:-1]  # the closing segment is added for you
        if len(pts) < 3:
            raise ScriptError("poly: at least 3 distinct points are needed.")
        return self.profile(pts[0], [line_seg(p) for p in pts[1:]], closed=True)

    def polyline(self, points: Iterable[Sequence[float]], closed: bool = False) -> Sketch:
        """Chain of straight segments (open by default; ``closed=True`` = ``poly``)."""
        pts = [point2(p, f"polyline.points[{i}]") for i, p in enumerate(points)]
        if len(pts) < 2:
            raise ScriptError("polyline: at least 2 points are needed.")
        return self.profile(pts[0], [line_seg(p) for p in pts[1:]], closed=closed)

    def profile(self, start: Sequence[float], segments: Sequence[dict[str, Any]], closed: bool = True) -> Sketch:
        """Connected lines and arcs (``line_seg`` / ``arc_seg``); closed profiles are checked for
        self-intersection and for arcs whose end points are not on the circle."""
        start = point2(start, "profile.start")
        if not segments:
            raise ScriptError("profile: no segments.")
        segs: list[dict[str, Any]] = []
        cur = tuple(start)
        for i, s in enumerate(segments):
            typ = s.get("type", "line")
            if typ not in ("line", "arc"):
                raise ScriptError(f"profile.segments[{i}]: type must be 'line' or 'arc'.")
            end = tuple(point2(s["to"], f"profile.segments[{i}].to")) if s.get("to") is not None else tuple(start)
            if s.get("to") is None and i != len(segments) - 1:
                raise ScriptError(f"profile.segments[{i}]: 'to' may only be omitted on the LAST segment.")
            seg: dict[str, Any] = {"type": typ}
            if s.get("to") is not None:
                seg["to"] = list(end)
            if typ == "arc":
                if s.get("center") is None:
                    raise ScriptError(f"profile.segments[{i}]: an arc needs a 'center'.")
                c = tuple(point2(s["center"], f"profile.segments[{i}].center"))
                r0, r1 = math.dist(cur, c), math.dist(end, c)
                if r0 < EPS or abs(r0 - r1) > 1e-3:
                    raise ScriptError(
                        f"profile.segments[{i}]: arc from {cur} to {end} around {c}: the end points are "
                        f"{r0:.4f} and {r1:.4f} mm from the center (must be equal). Check the center."
                    )
                direction = s.get("direction", "ccw")
                one_of(direction, f"profile.segments[{i}].direction", ("ccw", "cw"))
                seg.update(center=list(c), direction=direction)
                a0 = math.atan2(cur[1] - c[1], cur[0] - c[0])
                a1 = math.atan2(end[1] - c[1], end[0] - c[0])
                sweep = math.degrees((a1 - a0) % (2 * math.pi) if direction == "ccw" else (a0 - a1) % (2 * math.pi))
                if sweep > 180.0 + 1e-6 or sweep < 1e-9:
                    # legitimate sometimes, but the classic direction mistake (ccw instead of cw)
                    self._s.warnings.append(
                        f"sketch '{self.name}': an arc sweeps {sweep or 360:.0f} degrees; if the drawing shows "
                        "a short arc, its direction (ccw/cw) is inverted."
                    )
            elif math.dist(cur, end) < EPS:
                raise ScriptError(f"profile.segments[{i}]: zero-length line at {cur}.")
            segs.append(seg)
            cur = end
        if closed and math.dist(cur, tuple(start)) > 1e-6:
            segs.append({"type": "line"})  # explicit closing segment back to the start
        elif closed and segs[-1].get("to") is not None and segs[-1]["type"] == "line":
            segs[-1] = {"type": "line"}
        pts = profile_points(start, segs)
        if closed:
            pts = pts[:-1] if math.dist(pts[0], pts[-1]) < 1e-6 else pts
            crossings = self_intersections(pts, closed=True)
            if crossings:
                raise ScriptError(
                    f"profile: the outline crosses itself (segments {crossings[0][0]} and {crossings[0][1]}). "
                    "Usual causes: points listed out of order, or an arc drawn 'ccw' instead of 'cw'."
                )
            area = polygon_area(pts)
            if area < 1e-6:
                raise ScriptError("profile: the closed profile has no area (all points aligned?).")
            self.areas.append(area)
        self.points += pts
        self._emit("catia_sketch_profile", {"start": list(start), "segments": segs, "closed": bool(closed)})
        return self

    def line(self, x1: float, y1: float, x2: float, y2: float, axis: bool = False) -> Sketch:
        """One line. ``axis=True`` marks it as the revolution axis (use with ``shaft(axis='sketch_line')``)."""
        v = [num(x1, "line.x1"), num(y1, "line.y1"), num(x2, "line.x2"), num(y2, "line.y2")]
        if math.dist(v[:2], v[2:]) < EPS:
            raise ScriptError("line: zero length.")
        args: dict[str, Any] = dict(zip(("x1", "y1", "x2", "y2"), v))
        if axis:
            args["is_axis"] = True
            self.axis_lines += 1
        else:
            self.points += [(v[0], v[1]), (v[2], v[3])]
        self._emit("catia_sketch_line", args)
        return self

    def arc(self, cx: float, cy: float, radius: float, start_angle: float, end_angle: float) -> Sketch:
        """A free arc (degrees, counter-clockwise from ``start_angle`` to ``end_angle``)."""
        cx, cy = num(cx, "arc.cx"), num(cy, "arc.cy")
        r = num(radius, "arc.radius", positive=True)
        a0, a1 = num(start_angle, "arc.start_angle"), num(end_angle, "arc.end_angle")
        if abs(a1 - a0) < EPS or abs(a1 - a0) % 360 < EPS:
            raise ScriptError("arc: start and end angles are equal (empty or full circle: use circle()).")
        sweep = (a1 - a0) % 360
        self.points += [(cx + r * math.cos(math.radians(a0 + sweep * k / 12)),
                         cy + r * math.sin(math.radians(a0 + sweep * k / 12))) for k in range(13)]
        self._emit("catia_sketch_arc", {"cx": cx, "cy": cy, "radius": r, "start_angle": a0, "end_angle": a1})
        return self

    def point(self, x: float, y: float) -> Sketch:
        self._emit("catia_sketch_point", {"x": num(x, "point.x"), "y": num(y, "point.y")})
        return self

    # end -------------------------------------------------------------------------------------
    def close(self) -> Sketch:
        if self.closed:
            return self
        if self.count == 0:
            raise ScriptError(f"sketch '{self.name}' is empty: draw something before closing it.")
        self._s._emit("catia_close_sketch", {})
        self.closed = True
        self._s._open = None
        return self

    def bounds(self) -> tuple[float, float, float, float] | None:
        if not self.points:
            return None
        hs, vs = [p[0] for p in self.points], [p[1] for p in self.points]
        return min(hs), max(hs), min(vs), max(vs)


# ── the part script ─────────────────────────────────────────────────────────────────────
class PartScript(ScriptBase):
    """Model one part. ``PartScript("Flange", "out/Flange")`` then sketches/features, ``save()``.

    ``result`` is the name of the final body (default ``<name>_Resultat``); the file is
    ``<folder>/<name>.CATPart``. ``density`` (kg/m3, default steel 7850) is used for the mass check.
    """

    kind = "part"

    def __init__(self, name: str, folder: str | Path, result: str | None = None,
                 density: float | None = 7850.0, close_all: bool = True) -> None:
        super().__init__(check_name(name, "part name"), folder)
        self.result = check_name(result or f"{name}_Resultat", "result body name")
        self.density = num(density, "density (kg/m3)", positive=True, limit=None) if density is not None else None
        self._names: set[str] = {self.result}
        self._bodies: dict[str, dict[str, Any]] = {self.result: {"material": False, "merged": False}}
        self._active: str = self.result
        self._features: set[str] = set()
        self._feature_checks: list[dict[str, Any]] = []
        self._sketches: dict[str, Sketch] = {}
        self._last_sketch: Sketch | None = None
        self._open: Sketch | None = None
        self._saved = False
        self._expect: dict[str, Any] = {}
        self._measure_steps: dict[str, int] = {}
        if close_all:
            self._emit("catia_close_all", {})
        self._emit("catia_new_part", {"name": name})
        # no old_name: renames the body that exists right after new_part, in any UI language
        # UNVERIFIED-LIVE: proven with old_name="Corps principal" (French UI); this form relies on the
        # tool renaming the last body (bodies.py), which is the main body just after new_part.
        self._emit("catia_rename_body", {"new_name": self.result})

    # ── planes, sketches ──
    def plane(self, kind: str | None = "xy", offset: float | None = None,
              face_point: Sequence[float] | None = None) -> Plane:
        """A sketch support: ``plane("yz")``, ``plane("xy", offset=25)`` (parallel plane 25 mm along
        its normal) or ``plane(face_point=(x, y, z))`` (the planar face of the ACTIVE body containing
        that point; a point strictly inside the face)."""
        if face_point is not None:
            if offset is not None:
                raise ScriptError("plane: give face_point OR offset, not both.")
            return Plane(kind if kind in PLANES else None, None, tuple(point3(face_point, "plane.face_point")))  # type: ignore[arg-type]
        one_of(kind, "plane.kind", PLANES)
        return Plane(kind, num(offset, "plane.offset") if offset is not None else None, None)

    def _close_open_sketch(self) -> None:
        if self._open is not None:
            self._open.close()

    def _new_name(self, name: str, what: str) -> str:
        check_name(name, what, self._names)
        self._names.add(name)
        return name

    def sketch(self, plane: Plane | str, name: str) -> Sketch:
        """Open a sketch named ``name`` on ``plane`` (a ``Plane`` or "xy"/"yz"/"zx") in the ACTIVE body."""
        self._close_open_sketch()
        if isinstance(plane, str):
            plane = self.plane(plane)
        if not isinstance(plane, Plane):
            raise ScriptError(f"sketch: plane must be a Plane or 'xy'/'yz'/'zx', got {plane!r}.")
        self._new_name(name, "sketch name")
        args: dict[str, Any] = {"name": name}
        if plane.kind:
            args["plane"] = plane.kind
        if plane.offset is not None:
            args["offset"] = plane.offset
        if plane.face_point is not None:
            args["face_point"] = list(plane.face_point)
        # UNVERIFIED-LIVE: face_point WITHOUT a plane (auto-detected from the face normal); the proven
        # scripts only used origin planes, with or without offset.
        self._emit("catia_create_sketch", args)
        sk = Sketch(self, name, plane)
        self._sketches[name] = sk
        self._open = sk
        self._last_sketch = sk
        return sk

    def _take_sketch(self, sketch: Sketch | str | None, feature: str) -> tuple[Sketch, str | None]:
        self._close_open_sketch()
        if sketch is None:
            sk = self._last_sketch
            if sk is None or sk.consumed:
                raise ScriptError(f"{feature}: no free sketch. Every sketch feeds exactly one feature: "
                                  "draw a new one first.")
            return sk, None
        nm = sketch.name if isinstance(sketch, Sketch) else sketch
        sk = self._sketches.get(nm)
        if sk is None:
            raise ScriptError(f"{feature}: unknown sketch {nm!r} (known: {', '.join(self._sketches) or 'none'}).")
        if sk.consumed:
            raise ScriptError(f"{feature}: sketch {nm!r} already feeds a feature; draw a new one.")
        return sk, nm

    def _consume(self, sk: Sketch) -> None:
        sk.consumed = True

    # ── bodies ──
    def _cur(self) -> dict[str, Any]:
        return self._bodies[self._active]

    def body(self, name: str) -> str:
        """New body ``name`` (one per functional zone), made active. Merge it with ``combine``."""
        self._close_open_sketch()
        self._new_name(name, "body name")
        self._emit("catia_new_body", {"name": name})
        self._emit("catia_activate_body", {"body_name": name})
        self._bodies[name] = {"material": False, "merged": False}
        self._active = name
        return name

    def activate(self, body: str) -> None:
        self._close_open_sketch()
        if body not in self._bodies:
            raise ScriptError(f"activate: unknown body {body!r} (known: {', '.join(self._bodies)}).")
        self._emit("catia_activate_body", {"body_name": body})
        self._active = body

    def combine(self, target: str, tool: str, op: str, name: str, *,
                faces_to_remove_points: Sequence[Sequence[float]] | None = None,
                faces_to_keep_points: Sequence[Sequence[float]] | None = None) -> str:
        """Boolean operation: ``tool`` body is merged INTO ``target`` (assemble/add/remove/intersect).
        ``remove`` subtracts the tool volume from the target: the classic way to machine holes, slots."""
        self._close_open_sketch()
        one_of(op, "combine.op", BOOLEAN_OPS)
        for label, b in (("target", target), ("tool", tool)):
            if b not in self._bodies:
                raise ScriptError(f"combine: unknown {label} body {b!r} (known: {', '.join(self._bodies)}).")
        if target == tool:
            raise ScriptError("combine: target and tool are the same body.")
        if not self._bodies[tool]["material"]:
            raise ScriptError(f"combine: tool body {tool!r} holds no solid yet (pad/shaft something in it first).")
        if self._bodies[tool]["merged"]:
            raise ScriptError(f"combine: body {tool!r} was already merged into another body.")
        self._new_name(name, "boolean name")
        args: dict[str, Any] = {"operation": op, "tool_body": tool, "target_body": target, "name": name}
        if op == "union_trim":
            if not (faces_to_remove_points or faces_to_keep_points):
                raise ScriptError("combine: union_trim needs faces_to_remove_points / faces_to_keep_points.")
            for key, val in (("faces_to_remove_points", faces_to_remove_points),
                             ("faces_to_keep_points", faces_to_keep_points)):
                if val:
                    args[key] = [point3(p, key) for p in val]
        step = self._emit("catia_boolean_operation", args)
        self._features.add(name)
        self._feature_checks.append({"step": step, "name": name,
                                     "sign": {"remove": "remove", "add": "add", "assemble": "add"}.get(op, "any")})
        self._bodies[tool]["merged"] = True
        if op != "remove" or self._bodies[target]["material"]:
            self._bodies[target]["material"] = True
        # deterministic state: work continues in the target body
        # UNVERIFIED-LIVE: the extra activate_body after a boolean (proven scripts went on to save(),
        # which activates the result body anyway).
        self._emit("catia_activate_body", {"body_name": target})
        self._active = target
        return name

    # ── features ──
    def _feature(self, tool: str, args: dict[str, Any], name: str, sign: str,
                 expect_dvol: float | None = None, tol: float = 0.02) -> int:
        self._new_name(name, "feature name")
        args = {**args, "name": name}
        step = self._emit(tool, args)
        self._features.add(name)
        rec: dict[str, Any] = {"step": step, "name": name, "sign": sign}
        if expect_dvol is not None:
            rec.update(dvol=num(expect_dvol, "expect_dvol (mm3)", limit=None), tol=num(tol, "tol", positive=True))
        self._feature_checks.append(rec)
        return step

    def pad(self, name: str, height: float, *, symmetric: bool = False, direction: str = "normal",
            second_height: float | None = None, sketch: Sketch | str | None = None,
            expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Extrude the free sketch by ``height`` mm. ``symmetric`` = ``height`` in TOTAL, half each side."""
        sk, sk_name = self._take_sketch(sketch, "pad")
        h = num(height, "pad.height", positive=True)
        one_of(direction, "pad.direction", ("normal", "reverse", "both"))
        args: dict[str, Any] = {"height": h}
        if symmetric:
            if second_height is not None:
                raise ScriptError("pad: second_height is ignored with symmetric=True; give one or the other.")
            args["symmetric"] = True
        if direction != "normal":
            args["direction"] = direction
        if second_height is not None:
            args["second_height"] = num(second_height, "pad.second_height", positive=True)
        if sk_name:
            # UNVERIFIED-LIVE: explicit sketch_name (proven scripts always used the last sketch)
            args["sketch_name"] = sk_name
        self._feature("catia_pad", args, name, "add", expect_dvol, tol)
        self._consume(sk)
        self._cur()["material"] = True
        return name

    def pocket(self, name: str, depth: float | None = None, *, limit: str = "dimension",
               direction: str = "auto", sketch: Sketch | str | None = None,
               expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Cut the free sketch: ``depth`` mm (limit="dimension") or up to the next/last face."""
        sk, sk_name = self._take_sketch(sketch, "pocket")
        one_of(limit, "pocket.limit", ("dimension", "up_to_next", "up_to_last"))
        one_of(direction, "pocket.direction", ("auto", "normal", "reverse"))
        args: dict[str, Any] = {"limit": limit}
        if limit == "dimension":
            if depth is None:
                raise ScriptError("pocket: depth is required with limit='dimension'.")
            args["depth"] = num(depth, "pocket.depth", positive=True)
        elif depth is not None:
            raise ScriptError(f"pocket: depth must not be given with limit={limit!r}.")
        if direction != "auto":
            args["direction"] = direction
        if sk_name:
            args["sketch_name"] = sk_name
        # in a body that holds no material yet, "removed" volume shows up as +volume: no sign check
        sign = "remove" if self._cur()["material"] else "any"
        self._feature("catia_pocket", args, name, sign, expect_dvol, tol)
        self._consume(sk)
        return name

    def _revolve(self, tool: str, label: str, name: str, axis: str, angle: float,
                 sketch: Sketch | str | None, sign_material: bool, expect_dvol: float | None, tol: float) -> str:
        sk, sk_name = self._take_sketch(sketch, label)
        one_of(axis, f"{label}.axis", ("h", "v", "sketch_line"))
        ang = num(angle, f"{label}.angle", positive=True, hi=360.0)
        b = sk.bounds()
        if axis == "sketch_line":
            if sk.axis_lines != 1:
                raise ScriptError(f"{label}: axis='sketch_line' needs exactly one line(..., axis=True) in the "
                                  f"sketch (found {sk.axis_lines}).")
        elif b is not None:
            hmin, hmax, vmin, vmax = b
            lo, hi = (vmin, vmax) if axis == "h" else (hmin, hmax)
            if lo < -1e-6 and hi > 1e-6:
                raise ScriptError(
                    f"{label}: the profile crosses the revolution axis ({'V=0' if axis == 'h' else 'H=0'}: "
                    f"spans {lo:g}..{hi:g}). Draw a half profile on ONE side of the axis."
                )
        args: dict[str, Any] = {"axis": axis, "angle": ang}
        if sk_name:
            args["sketch_name"] = sk_name
        sign = "add" if not sign_material else ("remove" if self._cur()["material"] else "any")
        self._feature(tool, args, name, sign, expect_dvol, tol)
        self._consume(sk)
        if not sign_material:
            self._cur()["material"] = True
        return name

    def shaft(self, name: str, axis: str, angle: float = 360.0, *, sketch: Sketch | str | None = None,
              expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Revolve the free sketch about ``axis``: "h" (sketch horizontal axis, V=0), "v" (H=0) or
        "sketch_line" (the line drawn with ``axis=True``)."""
        return self._revolve("catia_shaft", "shaft", name, axis, angle, sketch, False, expect_dvol, tol)

    def groove(self, name: str, axis: str, angle: float = 360.0, *, sketch: Sketch | str | None = None,
               expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Revolved cut (same rules as ``shaft``)."""
        return self._revolve("catia_groove", "groove", name, axis, angle, sketch, True, expect_dvol, tol)

    def hole(self, name: str, point: Sequence[float], diameter: float, depth: float | None = None, *,
             up_to_point: Sequence[float] | None = None, kind: str = "simple",
             head_diameter: float | None = None, head_depth: float | None = None,
             head_angle: float | None = None, taper_angle: float | None = None,
             threaded: bool = False, expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Hole whose centre ``point`` [x, y, z] lies ON the entry face. Give ``depth`` OR ``up_to_point``."""
        self._close_open_sketch()
        one_of(kind, "hole.kind", ("simple", "tapered", "counterbored", "countersunk"))
        args: dict[str, Any] = {"point": point3(point, "hole.point"),
                                "diameter": num(diameter, "hole.diameter (a DIAMETER)", positive=True)}
        if (depth is None) == (up_to_point is None):
            raise ScriptError("hole: give exactly one of depth / up_to_point.")
        if depth is not None:
            args["depth"] = num(depth, "hole.depth", positive=True)
        else:
            args["up_to_point"] = point3(up_to_point, "hole.up_to_point")
        if kind != "simple":
            args["type"] = kind
        if kind == "tapered":
            if taper_angle is None:
                raise ScriptError("hole: tapered needs taper_angle.")
            args["taper_angle"] = num(taper_angle, "hole.taper_angle", positive=True, hi=89.0)
        if kind in ("counterbored", "countersunk"):
            if head_diameter is None:
                raise ScriptError(f"hole: {kind} needs head_diameter.")
            hd = num(head_diameter, "hole.head_diameter", positive=True)
            if hd <= args["diameter"]:
                raise ScriptError("hole: head_diameter must exceed diameter.")
            args["head_diameter"] = hd
            if kind == "counterbored":
                if head_depth is None:
                    raise ScriptError("hole: counterbored needs head_depth.")
                args["head_depth"] = num(head_depth, "hole.head_depth", positive=True)
            elif head_angle is not None:
                args["head_angle"] = num(head_angle, "hole.head_angle", positive=True, hi=179.0)
        if threaded:
            args["threaded"] = True
        # UNVERIFIED-LIVE: hole kinds counterbored/countersunk/tapered and up_to_point (schema-valid only)
        sign = "remove" if self._cur()["material"] else "any"
        self._feature("catia_hole", args, name, sign, expect_dvol, tol)
        return name

    def fillet(self, name: str, radius: float, edge_points: Sequence[Sequence[float]], *,
               expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Round edges: ``edge_points`` = one [x, y, z] ON each edge (see catia_list_edges)."""
        self._close_open_sketch()
        args = {"radius": num(radius, "fillet.radius", positive=True), "edge_points": self._edges(edge_points)}
        self._feature("catia_fillet", args, name, "any", expect_dvol, tol)
        return name

    def chamfer(self, name: str, length: float, edge_points: Sequence[Sequence[float]], *,
                angle: float = 45.0, length2: float | None = None,
                expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Chamfer edges: ``length`` x ``angle`` (default 45), or ``length`` x ``length2``."""
        self._close_open_sketch()
        args: dict[str, Any] = {"length": num(length, "chamfer.length", positive=True),
                                "edge_points": self._edges(edge_points)}
        if length2 is not None:
            args["length2"] = num(length2, "chamfer.length2", positive=True)
        elif angle != 45.0:
            args["angle"] = num(angle, "chamfer.angle", positive=True, hi=89.0)
        self._feature("catia_chamfer", args, name, "remove" if self._cur()["material"] else "any",
                      expect_dvol, tol)
        return name

    @staticmethod
    def _edges(edge_points: Sequence[Sequence[float]]) -> list[list[float]]:
        if not isinstance(edge_points, (list, tuple)) or not edge_points:
            raise ScriptError("edge_points: a non-empty list of [x, y, z] points is required.")
        return [point3(p, f"edge_points[{i}]") for i, p in enumerate(edge_points)]

    def pattern(self, kind: str, name: str, feature: str, *, count: int, spacing: float | None = None,
                direction: str = "x", count2: int = 1, spacing2: float = 0.0, direction2: str = "y",
                angular: float | None = None, axis_plane: str | None = None,
                axis_face_point: Sequence[float] | None = None, reverse: bool = False,
                expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Repeat an existing feature. ``kind="rect"``: ``count`` x ``spacing`` mm along ``direction``
        (x/y/z), optionally ``count2`` x ``spacing2`` along ``direction2``. ``kind="circ"``: ``count``
        instances (original included) every ``angular`` degrees (default 360/count) about the normal
        of ``axis_plane`` or the axis of the cylinder through ``axis_face_point``. Patterns copy the
        SHAPE the feature cut, not its limit rule: give the original a fixed depth."""
        self._close_open_sketch()
        one_of(kind, "pattern.kind", ("rect", "circ"))
        if feature not in self._features:
            raise ScriptError(f"pattern: unknown feature {feature!r} (known: {', '.join(sorted(self._features)) or 'none'}).")
        n = integer(count, "pattern.count", lo=2)
        args: dict[str, Any] = {"feature_name": feature}
        if kind == "rect":
            if any(x is not None for x in (angular, axis_plane, axis_face_point)) or reverse:
                raise ScriptError("pattern: angular/axis_plane/axis_face_point/reverse belong to kind='circ'.")
            if spacing is None:
                raise ScriptError("pattern: rect needs spacing (mm).")
            one_of(direction, "pattern.direction", ("x", "y", "z"))
            args.update(dir1=direction, dir1_count=n, dir1_spacing=num(spacing, "pattern.spacing", nonzero=True))
            n2 = integer(count2, "pattern.count2", lo=1)
            if n2 > 1:
                one_of(direction2, "pattern.direction2", ("x", "y", "z"))
                if direction2 == direction:
                    raise ScriptError("pattern: direction2 must differ from direction.")
                args.update(dir2=direction2, dir2_count=n2, dir2_spacing=num(spacing2, "pattern.spacing2", nonzero=True))
            step_tool = "catia_rect_pattern"
        else:
            if spacing is not None or count2 != 1:
                raise ScriptError("pattern: spacing/count2 belong to kind='rect' (use angular for 'circ').")
            if (axis_plane is None) == (axis_face_point is None):
                raise ScriptError("pattern: circ needs exactly one of axis_plane / axis_face_point.")
            args["count"] = n
            if axis_plane is not None:
                args["axis_plane"] = one_of(axis_plane, "pattern.axis_plane", PLANES)
            else:
                args["axis_face_point"] = point3(axis_face_point, "pattern.axis_face_point")
            if angular is not None:
                args["angular_spacing"] = num(angular, "pattern.angular", nonzero=True, lo=-360, hi=360)
            if reverse:
                args["reverse"] = True
            step_tool = "catia_circ_pattern"
        # UNVERIFIED-LIVE: circ pattern with axis_face_point/angular/reverse and rect pattern with two
        # directions (only the simple forms were proven in the reference scripts).
        self._feature(step_tool, args, name, "any", expect_dvol, tol)
        return name

    def mirror(self, name: str, plane: str | None = None, plane_face_point: Sequence[float] | None = None, *,
               expect_dvol: float | None = None, tol: float = 0.02) -> str:
        """Mirror the body so far about an origin plane or a planar face."""
        self._close_open_sketch()
        if (plane is None) == (plane_face_point is None):
            raise ScriptError("mirror: give exactly one of plane / plane_face_point.")
        args: dict[str, Any] = {}
        if plane is not None:
            args["plane"] = one_of(plane, "mirror.plane", PLANES)
        else:
            args["plane_face_point"] = point3(plane_face_point, "mirror.plane_face_point")
        self._feature("catia_mirror", args, name, "any", expect_dvol, tol)
        return name

    # ── expectations, end of script ──
    def checks(self, volume: float | None = None, bbox: Sequence[float | None] | None = None, *,
               mass: float | None = None, tol: float = 0.02, bbox_tol: float = 0.5) -> None:
        """State what the drawing says, compared after the run: final ``volume`` (mm3, relative ``tol``),
        ``bbox`` = overall size (dx, dy, dz) in mm (absolute ``bbox_tol``), ``mass`` (kg, needs density).
        Compute them by hand from the drawing BEFORE running: a plausible volume does not prove the shape,
        a wrong one always proves an error."""
        if volume is not None:
            self._expect["volume"] = {"expected": num(volume, "checks.volume", positive=True, limit=None),
                                      "tol": num(tol, "checks.tol", positive=True)}
        if mass is not None:
            if self.density is None:
                raise ScriptError("checks: mass needs a density (PartScript(..., density=...)).")
            self._expect["mass"] = {"expected": num(mass, "checks.mass", positive=True, limit=None),
                                    "tol": num(tol, "checks.tol", positive=True)}
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 3:
                raise ScriptError("checks.bbox: three sizes (dx, dy, dz) in mm (None to skip an axis).")
            self._expect["bbox"] = {"expected": [None if b is None else num(b, "checks.bbox", positive=True) for b in bbox],
                                    "tol": num(bbox_tol, "checks.bbox_tol", positive=True)}

    def save(self, views: Sequence[str] = DEFAULT_VIEWS, allow_unmerged: bool = False) -> Path:
        """Final measurements, naming audit, ``<folder>/<name>.CATPart`` and one screenshot per view."""
        self._close_open_sketch()
        if self._saved:
            raise ScriptError("save() was already called.")
        if not self._bodies[self.result]["material"]:
            raise ScriptError(f"save: the result body {self.result!r} is empty; build the base shape in it "
                              "or combine() the other bodies into it.")
        orphans = [b for b, v in self._bodies.items() if b != self.result and not v["merged"]]
        if orphans and not allow_unmerged:
            raise ScriptError(f"save: bodies never merged into another body: {', '.join(orphans)} "
                              "(combine() them, or pass allow_unmerged=True if they are intentional).")
        unused = [n for n, s in self._sketches.items() if not s.consumed]
        if unused:
            self.warnings.append("sketches never used by a feature: " + ", ".join(unused))
        target = self.folder / f"{self.name}.CATPart"
        self._emit("catia_activate_body", {"body_name": self.result})
        self._emit("catia_update_part", {})
        inertia: dict[str, Any] = {"body_name": self.result}
        if self.density is not None:
            inertia["density"] = self.density
        self._measure_steps["inertia"] = self._emit("catia_get_inertia", inertia)
        self._measure_steps["bbox"] = self._emit("catia_get_bounding_box", {})
        self._measure_steps["tree"] = self._emit("catia_get_tree", {})
        self._emit("catia_save_document", {"file_path": str(target)})
        for i, v in enumerate(views, 1):
            one_of(v, "save.views", ("front", "back", "top", "bottom", "left", "right", "isometric",
                                     "isometric_back", "isometric_left", "isometric_right"))
            self._emit("catia_set_view", {"view": v})
            self._emit("catia_fit_all", {})
            self._emit("catia_screenshot", {"file_path": str(self.folder / "screens" / f"{i:02d}_{v}.png")})
        self._saved = True
        return target

    def _before_finish(self) -> None:
        self._close_open_sketch()

    def steps(self, require_save: bool = True) -> list[dict[str, Any]]:
        self._before_finish()
        if require_save and not self._saved:
            raise ScriptError("steps(): call save() at the end of the script (or steps(require_save=False)).")
        return super().steps()

    def checks_spec(self) -> dict[str, Any]:
        spec: dict[str, Any] = {"features": [dict(f) for f in self._feature_checks]}
        if self._saved:
            spec["audit"] = {"step": self._measure_steps["tree"]}
            for key, step_key in (("volume", "inertia"), ("mass", "inertia"), ("bbox", "bbox")):
                if key in self._expect:
                    spec[key] = {"step": self._measure_steps[step_key], **self._expect[key]}
        return spec
