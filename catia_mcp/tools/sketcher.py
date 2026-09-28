"""Sketcher tools for CATIA V5.

2D sketch creation and editing: lines, circles, rectangles, arcs, splines, constraints.
All dimensions are in millimeters. CATIA COM API uses millimeters natively.
"""

from __future__ import annotations

import json
import math
from typing import Any

from catia_mcp import geometry
from catia_mcp.connection import CATIAConnection

# Plane name mapping
PLANE_MAP = {
    "xy": "PlaneXY",
    "yz": "PlaneYZ",
    "zx": "PlaneZX",
    "xz": "PlaneZX",  # alias
}


def _polyline(verts: list[tuple[float, float]], segments: list[dict[str, Any]]) -> list[list[tuple[float, float]]]:
    """Each segment as a list of sampled points (arcs every ~5°), same conventions as _profile."""
    out = []
    for i, seg in enumerate(segments):
        a, b = verts[i], verts[i + 1]
        if seg.get("type", "line") != "arc":
            out.append([a, b])
            continue
        c = tuple(map(float, seg["center"]))
        r = math.dist(a, c)
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        a1 = math.atan2(b[1] - c[1], b[0] - c[0])
        if seg.get("direction", "ccw") == "ccw":
            while a1 <= a0:
                a1 += 2 * math.pi
        else:
            while a1 >= a0:
                a1 -= 2 * math.pi
        n = max(2, int(abs(a1 - a0) / math.radians(5)) + 1)
        pts = [(c[0] + r * math.cos(a0 + (a1 - a0) * k / n), c[1] + r * math.sin(a0 + (a1 - a0) * k / n))
               for k in range(n + 1)]
        pts[0], pts[-1] = a, b
        out.append(pts)
    return out


def _self_intersection(verts: list[tuple[float, float]], segments: list[dict[str, Any]]) -> str | None:
    """Describe the first crossing between two NON-adjacent segments of the profile, if any."""
    polys = _polyline(verts, segments)

    def cross(p, q, r, s):
        d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0])
        if abs(d) < 1e-12:
            return False
        t = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d
        u = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d
        return 1e-6 < t < 1 - 1e-6 and 1e-6 < u < 1 - 1e-6

    n = len(polys)
    closed = math.dist(verts[0], verts[-1]) < 1e-6
    for i in range(n):
        for j in range(i + 1, n):
            adjacent = j == i + 1 or (closed and i == 0 and j == n - 1)
            for p, q in zip(polys[i], polys[i][1:]):
                for r, s in zip(polys[j], polys[j][1:]):
                    if adjacent and (q == r or p == s or q == s or p == r):
                        continue
                    if cross(p, q, r, s):
                        return f"segment {i + 1} crosses segment {j + 1} near ({p[0]:.1f}; {p[1]:.1f})"
    return None


def _arc_warnings(verts: list[tuple[float, float]], segments: list[dict[str, Any]]) -> list[str]:
    """Arcs sweeping more than 180°: the classic silent error (proven live on a shaft part —
    a slot bottom R19.5 drawn 'ccw' instead of 'cw' went round the wrong side and left a
    bulge of material in the slot). Say which extreme point of the circle the arc passes."""
    out = []
    for i, (seg, pts) in enumerate(zip(segments, _polyline(verts, segments)), 1):
        if seg.get("type", "line") != "arc":
            continue
        c = tuple(map(float, seg["center"]))
        a0 = math.atan2(pts[0][1] - c[1], pts[0][0] - c[0])
        a1 = math.atan2(pts[-1][1] - c[1], pts[-1][0] - c[0])
        sweep = (a1 - a0) % (2 * math.pi) if seg.get("direction", "ccw") == "ccw" else (a0 - a1) % (2 * math.pi)
        if sweep > math.pi + 1e-6:
            far = pts[len(pts) // 2]  # middle of the arc = the side it goes round
            out.append(
                f"arc {i} sweeps {math.degrees(sweep):.0f}° (> 180°) around ({c[0]:g}; {c[1]:g}) and passes "
                f"through ({far[0]:.1f}; {far[1]:.1f}). If the drawing shows the arc on the OTHER side "
                f"of its centre, flip its 'direction'."
            )
    return out


def _save_preview(verts: list[tuple[float, float]], segments: list[dict[str, Any]]) -> str | None:
    """PNG of the contour (H right, V up, centres marked, start point in green)."""
    try:
        import tempfile
        from pathlib import Path

        from PIL import Image, ImageDraw

        polys = _polyline(verts, segments)
        xs = [p[0] for poly in polys for p in poly]
        ys = [p[1] for poly in polys for p in poly]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
        size, pad = 700, 40
        k = (size - 2 * pad) / span

        def tr(p):
            return (pad + (p[0] - min(xs)) * k, size - pad - (p[1] - min(ys)) * k)

        img = Image.new("RGB", (size, size), "white")
        d = ImageDraw.Draw(img)
        for seg, poly in zip(segments, polys):
            d.line([tr(p) for p in poly], fill=(200, 0, 0) if seg.get("type") == "arc" else (20, 60, 140), width=3)
            if seg.get("type") == "arc":
                cx, cy = tr(tuple(map(float, seg["center"])))
                d.line([(cx - 5, cy), (cx + 5, cy)], fill="gray")
                d.line([(cx, cy - 5), (cx, cy + 5)], fill="gray")
        sx, sy = tr(verts[0])
        d.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], fill=(0, 160, 0))
        d.text((8, 8), f"H {min(xs):g}..{max(xs):g}   V {min(ys):g}..{max(ys):g}", fill="black")
        out = Path(tempfile.gettempdir()) / "catia_mcp_previews"
        out.mkdir(exist_ok=True)
        path = out / f"profil_{len(list(out.glob('profil_*.png'))) + 1:03d}.png"
        img.save(path)
        return str(path)
    except Exception:
        return None


class SketcherTools:
    """Tools for 2D sketch operations in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        self._active_sketch: Any | None = None
        self._active_factory: Any | None = None

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_create_sketch",
                "description": (
                    "Create a new 2D sketch (in the active body) and open it for editing. "
                    "Support, in priority order: face_point (the planar face of the solid "
                    "that passes through this 3D point, e.g. 'the top face of the boss'), "
                    "else plane + offset (a named offset plane is inserted in the tree), "
                    "else the origin plane. The sketch axes are aligned on the origin "
                    "plane parallel to the support (xy: H=X,V=Y; yz: H=Y,V=Z; zx: H=Z,V=X) "
                    "with its origin on the projection of (0,0,0), so 2D coordinates stay "
                    "the ones read on the drawing's view. Close with catia_close_sketch."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plane": {
                            "type": "string",
                            "description": (
                                "Origin plane: 'xy', 'yz', 'zx'. With face_point it is "
                                "auto-detected from the face normal and can be omitted."
                            ),
                            "enum": ["xy", "yz", "zx"],
                            "default": "xy",
                        },
                        "offset": {
                            "type": "number",
                            "description": "Distance (mm) along the plane normal for a parallel offset plane.",
                        },
                        "face_point": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": (
                                "[x, y, z] in mm of a point INSIDE the planar face to sketch "
                                "on (not on its border). Searched in the active body first, "
                                "then in the other bodies."
                            ),
                        },
                        "name": {"type": "string", "description": "Optional name for the sketch."},
                    },
                },
            },
            {
                "name": "catia_close_sketch",
                "description": (
                    "Close the active sketch and return to Part Design. "
                    "Must be called after finishing sketch geometry before applying 3D features."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_sketch_line",
                "description": (
                    "Draw a line in the active sketch from (x1, y1) to (x2, y2). "
                    "Coordinates in mm. Set is_axis=true to make it the sketch's "
                    "axis (Sketch.CenterLine), REQUIRED before catia_shaft/"
                    "catia_groove: without it CATIA has no revolution axis and the "
                    "feature fails at update (confirmed live 2026-09-28). The axis "
                    "is excluded from the profile, so draw the closed profile "
                    "entirely on one side of it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x1": {"type": "number", "description": "Start X coordinate (mm)"},
                        "y1": {"type": "number", "description": "Start Y coordinate (mm)"},
                        "x2": {"type": "number", "description": "End X coordinate (mm)"},
                        "y2": {"type": "number", "description": "End Y coordinate (mm)"},
                        "is_axis": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "If true, set this line as the sketch axis "
                                "(Sketch.CenterLine) so it serves as the revolution "
                                "axis for a Shaft/Groove on this sketch."
                            ),
                        },
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                },
            },
            {
                "name": "catia_sketch_rectangle",
                "description": (
                    "Draw a rectangle in the active sketch defined by two opposite corners. "
                    "Creates 4 lines forming a closed profile. Coordinates in mm."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x1": {"type": "number", "description": "First corner X (mm)"},
                        "y1": {"type": "number", "description": "First corner Y (mm)"},
                        "x2": {"type": "number", "description": "Opposite corner X (mm)"},
                        "y2": {"type": "number", "description": "Opposite corner Y (mm)"},
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                },
            },
            {
                "name": "catia_sketch_centered_rectangle",
                "description": (
                    "Draw a rectangle centered at (cx, cy) with given width and height. "
                    "Coordinates and dimensions in mm."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Center X (mm)", "default": 0},
                        "cy": {"type": "number", "description": "Center Y (mm)", "default": 0},
                        "width": {"type": "number", "description": "Width in mm"},
                        "height": {"type": "number", "description": "Height in mm"},
                    },
                    "required": ["width", "height"],
                },
            },
            {
                "name": "catia_sketch_circle",
                "description": "Draw a circle in the active sketch. Coordinates and radius in mm.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Center X (mm)", "default": 0},
                        "cy": {"type": "number", "description": "Center Y (mm)", "default": 0},
                        "radius": {"type": "number", "description": "Radius in mm"},
                        "construction": {
                            "type": "boolean",
                            "default": False,
                            "description": "Construction (dashed) circle: reference only, not part of the profile.",
                        },
                    },
                    "required": ["radius"],
                },
            },
            {
                "name": "catia_sketch_profile",
                "description": (
                    "Draw a connected chain of lines and arcs whose ends are SHARED "
                    "sketch points (a real closed contour, unlike separate "
                    "lines/arcs whose ends only nearly meet). The tool of choice "
                    "for any non-trivial Pad/Pocket profile and for half-profiles "
                    "of revolution (Shaft/Groove: add the axis with "
                    "catia_sketch_line(is_axis=true) or use axis='h'/'v'). Arcs are "
                    "given by center + end point (radius = distance center-start; "
                    "the end must be at the same distance)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                            "description": "[h, v] start point (mm, sketch coordinates).",
                        },
                        "segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": ["line", "arc"]},
                                    "to": {
                                        "type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                                        "description": "End point; may be omitted on the last segment of a closed profile (= start).",
                                    },
                                    "center": {
                                        "type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                                        "description": "arc only: center.",
                                    },
                                    "direction": {
                                        "type": "string", "enum": ["ccw", "cw"], "default": "ccw",
                                        "description": "arc only: travel direction from start to end.",
                                    },
                                },
                                "required": ["type"],
                            },
                        },
                        "closed": {"type": "boolean", "default": True},
                    },
                    "required": ["start", "segments"],
                },
            },
            {
                "name": "catia_sketch_two_circle_contour",
                "description": (
                    "Closed outer contour of two circles joined by their external "
                    "tangent lines (connecting rod, lever, tow bar, arm). Tangent "
                    "points are computed exactly and all ends are shared points."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "c1": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                        "r1": {"type": "number"},
                        "c2": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                        "r2": {"type": "number"},
                    },
                    "required": ["c1", "r1", "c2", "r2"],
                },
            },
            {
                "name": "catia_sketch_arc",
                "description": (
                    "Draw a circular arc defined by center, radius, and start/end angles (degrees). "
                    "Angles are measured counter-clockwise from the positive X axis."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Center X (mm)"},
                        "cy": {"type": "number", "description": "Center Y (mm)"},
                        "radius": {"type": "number", "description": "Radius (mm)"},
                        "start_angle": {"type": "number", "description": "Start angle (degrees)"},
                        "end_angle": {"type": "number", "description": "End angle (degrees)"},
                    },
                    "required": ["cx", "cy", "radius", "start_angle", "end_angle"],
                },
            },
            {
                "name": "catia_sketch_spline",
                "description": (
                    "Draw a spline through a list of control points. "
                    "Each point is [x, y] in mm."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "points": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "description": "List of [x, y] control points in mm",
                            "minItems": 2,
                        },
                        "closed": {
                            "type": "boolean",
                            "description": "Whether to close the spline (default: false)",
                            "default": False,
                        },
                    },
                    "required": ["points"],
                },
            },
            {
                "name": "catia_sketch_point",
                "description": "Create a point in the active sketch. Coordinates in mm.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "X coordinate (mm)"},
                        "y": {"type": "number", "description": "Y coordinate (mm)"},
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "catia_sketch_constraint",
                "description": (
                    "Add a dimensional constraint to the active sketch. "
                    "Supported types: distance, radius, angle, coincidence, tangent, "
                    "perpendicular, parallel, horizontal, vertical, fix."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Constraint type",
                            "enum": [
                                "distance", "radius", "angle",
                                "coincidence", "tangent", "perpendicular",
                                "parallel", "horizontal", "vertical", "fix",
                            ],
                        },
                        "value": {
                            "type": "number",
                            "description": "Constraint value (mm or degrees). Required for distance, radius, angle.",
                        },
                        "geometry_index_1": {
                            "type": "integer",
                            "description": "Index of first geometry element (1-based, from sketch geometry list)",
                        },
                        "geometry_index_2": {
                            "type": "integer",
                            "description": "Index of second geometry element (for relational constraints)",
                        },
                    },
                    "required": ["type"],
                },
            },
            {
                "name": "catia_sketch_get_geometry",
                "description": "List all geometry elements in the active sketch with their indices and types.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_create_sketch":
                return self._create_sketch(arguments)
            case "catia_close_sketch":
                return self._close_sketch()
            case "catia_sketch_line":
                return self._draw_line(
                    arguments["x1"], arguments["y1"],
                    arguments["x2"], arguments["y2"],
                    arguments.get("is_axis", False),
                )
            case "catia_sketch_rectangle":
                return self._draw_rectangle(
                    arguments["x1"], arguments["y1"],
                    arguments["x2"], arguments["y2"],
                )
            case "catia_sketch_centered_rectangle":
                return self._draw_centered_rectangle(
                    arguments.get("cx", 0), arguments.get("cy", 0),
                    arguments["width"], arguments["height"],
                )
            case "catia_sketch_circle":
                return self._draw_circle(
                    arguments.get("cx", 0), arguments.get("cy", 0),
                    arguments["radius"], arguments.get("construction", False),
                )
            case "catia_sketch_profile":
                return self._profile(
                    arguments["start"], arguments["segments"], arguments.get("closed", True)
                )
            case "catia_sketch_two_circle_contour":
                return self._two_circle_contour(
                    arguments["c1"], arguments["r1"], arguments["c2"], arguments["r2"]
                )
            case "catia_sketch_arc":
                return self._draw_arc(
                    arguments["cx"], arguments["cy"], arguments["radius"],
                    arguments["start_angle"], arguments["end_angle"],
                )
            case "catia_sketch_spline":
                return self._draw_spline(
                    arguments["points"], arguments.get("closed", False),
                )
            case "catia_sketch_point":
                return self._draw_point(arguments["x"], arguments["y"])
            case "catia_sketch_constraint":
                return self._add_constraint(arguments)
            case "catia_sketch_get_geometry":
                return self._get_geometry()
            case _:
                raise ValueError(f"Unknown sketcher tool: {tool_name}")

    def _ensure_sketch_open(self) -> None:
        if self._active_sketch is None:
            raise RuntimeError(
                "No active sketch. Use catia_create_sketch first to open a sketch."
            )

    def _origin_plane_ref(self, part: Any, plane_key: str) -> Any:
        if plane_key not in PLANE_MAP:
            raise ValueError(f"Unknown plane '{plane_key}'. Use 'xy', 'yz', or 'zx'.")
        return part.CreateReferenceFromObject(getattr(part.OriginElements, PLANE_MAP[plane_key]))

    def _offset_plane(self, part: Any, body: Any, plane_key: str, offset: float) -> Any:
        """Insert a named offset plane in the active body and return its reference.
        An identical plane already in the body is reused: two sketches on the same
        offset (e.g. two through-pockets from X=200) used to create two planes with
        the same name — a duplicate a tree audit flags."""
        name = f"Plan_{plane_key.upper()}_{offset:g}mm"
        try:
            shapes = body.HybridShapes
            for i in range(1, shapes.Count + 1):
                if shapes.Item(i).Name == name:
                    existing = shapes.Item(i)
                    return part.CreateReferenceFromObject(existing), existing
        except Exception:
            pass
        hsf = part.HybridShapeFactory
        base = self._origin_plane_ref(part, plane_key)
        plane = hsf.AddNewPlaneOffset(base, abs(offset), offset < 0)
        body.InsertHybridShape(plane)
        part.InWorkObject = plane
        part.UpdateObject(plane)
        try:
            plane.Name = name
        except Exception:
            pass
        # Hidden like CATIA's own construction planes: shown, it drew a plane symbol across
        # every capture (reported on 3_005, 3_001).
        try:
            sel = self.conn.active_document.Selection
            sel.Clear()
            sel.Add(plane)
            sel.VisProperties.SetShow(1)
            sel.Clear()
        except Exception:
            pass
        return part.CreateReferenceFromObject(plane), plane

    def _create_sketch(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        doc = self.conn.active_document
        app = self.conn.app

        plane_key = (args.get("plane") or "xy").lower().replace("xz", "zx")
        face_point = args.get("face_point")
        offset = args.get("offset")
        notes: list[str] = []
        support_label = ""
        axis_offset = 0.0
        plane_obj = None

        if face_point:
            # Look in the active body first: a face of ANOTHER body cannot carry
            # a sketch (proven live: the sketch never resolves, every
            # feature on it fails) — see CATIA_COM_PITFALLS.md.
            ref, owner = geometry.pick_in_part(app, doc, part, "face", face_point, prefer=body)
            info = geometry.face_info(app, ref)
            std = geometry.parallel_origin_plane(info)
            if std is None:
                raise RuntimeError(
                    f"The face through {face_point} is not planar or not parallel to "
                    f"xy/yz/zx (face data: {info}). Use an explicit plane + offset."
                )
            plane_key, axis_offset = std
            if owner.Name == body.Name:
                support = ref
                support_label = f"face of '{owner.Name}' through {face_point}"
            else:
                support, plane_obj = self._offset_plane(part, body, plane_key, axis_offset)
                support_label = (
                    f"plane '{plane_obj.Name}' coincident with the face of '{owner.Name}' "
                    f"through {face_point}"
                )
                notes.append(
                    "The face belongs to another body; CATIA cannot sketch on it from "
                    "this body, so an offset plane lying on that face was inserted instead."
                )
        elif offset:
            support, plane_obj = self._offset_plane(part, body, plane_key, float(offset))
            axis_offset = float(offset)
            support_label = f"offset plane '{plane_obj.Name}'"
        else:
            support = self._origin_plane_ref(part, plane_key)
            support_label = f"origin plane {plane_key.upper()}"

        sketch = body.Sketches.Add(support)
        if face_point or offset:
            sketch.SetAbsoluteAxisData(geometry.sketch_axis_data(plane_key, axis_offset))
        if plane_obj is not None:
            # Keep the construction plane in the tree but out of the 3D view.
            try:
                sel = doc.Selection
                sel.Clear(); sel.Add(plane_obj); sel.VisProperties.SetShow(1); sel.Clear()
            except Exception:
                pass

        if args.get("name"):
            try:
                sketch.Name = args["name"]
            except Exception as e:
                notes.append(f"rename failed: {e}")

        self._active_sketch = sketch
        self._active_factory = sketch.OpenEdition()

        h, v = geometry.STD_AXES[plane_key][:2]
        return (
            f"Sketch '{sketch.Name}' created in body '{body.Name}' on {support_label}. "
            f"Axes: H={h}, V={v}, normal={geometry.STD_AXES[plane_key][2]}. Ready for geometry."
            + ("" if not notes else " NOTE: " + " ".join(notes))
        )

    def _close_sketch(self) -> str:
        self._ensure_sketch_open()
        sketch = self._active_sketch
        sketch.CloseEdition()
        self.conn.get_active_part().UpdateObject(sketch)
        self._active_sketch = None
        self._active_factory = None
        self.conn.refresh_display()
        return "Sketch closed. You can now apply Part Design features (pad, pocket, etc.)."

    def _draw_line(
        self, x1: float, y1: float, x2: float, y2: float, is_axis: bool = False
    ) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        line = factory.CreateLine(x1, y1, x2, y2)

        axis_note = ""
        if is_axis:
            # Live-tested 2026-09-28: Shaft/Groove read their revolution axis
            # from Sketch.CenterLine (the Sketcher "Axis" button), NOT from
            # Line2D.Construction nor Shaft.RevoluteAxis — both of those were
            # tried and part.Update() still failed. Setting CenterLine is the
            # only variant that worked, and the line needs no Construction flag.
            self._active_sketch.CenterLine = line
            axis_note = " (sketch axis / CenterLine)"

        return f"Line created from ({x1}, {y1}) to ({x2}, {y2}) mm{axis_note}"

    def _draw_rectangle(self, x1: float, y1: float, x2: float, y2: float) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory

        # Create 4 lines forming a closed rectangle
        factory.CreateLine(x1, y1, x2, y1)  # bottom
        factory.CreateLine(x2, y1, x2, y2)  # right
        factory.CreateLine(x2, y2, x1, y2)  # top
        factory.CreateLine(x1, y2, x1, y1)  # left

        return (
            f"Rectangle created from ({x1}, {y1}) to ({x2}, {y2}) mm "
            f"[{abs(x2-x1):.1f} x {abs(y2-y1):.1f} mm]"
        )

    def _draw_centered_rectangle(
        self, cx: float, cy: float, width: float, height: float
    ) -> str:
        hw, hh = width / 2, height / 2
        return self._draw_rectangle(cx - hw, cy - hh, cx + hw, cy + hh)

    def _draw_circle(self, cx: float, cy: float, radius: float, construction: bool = False) -> str:
        self._ensure_sketch_open()
        circle = self._active_factory.CreateClosedCircle(cx, cy, radius)
        if construction:
            circle.Construction = True
        kind = "Construction circle" if construction else "Circle"
        return f"{kind} created at ({cx}, {cy}) with radius {radius} mm"

    def _draw_arc(
        self, cx: float, cy: float, radius: float,
        start_angle: float, end_angle: float,
    ) -> str:
        self._ensure_sketch_open()
        # Factory2D has no CreateArc: an arc is CreateCircle with start/end
        # angles in radians, counter-clockwise from start to end.
        end = end_angle if end_angle > start_angle else end_angle + 360
        self._active_factory.CreateCircle(
            cx, cy, radius, math.radians(start_angle), math.radians(end)
        )
        return (
            f"Arc created at ({cx}, {cy}), radius={radius} mm, "
            f"counter-clockwise from {start_angle}° to {end_angle}°"
        )

    def _profile(self, start: list[float], segments: list[dict[str, Any]], closed: bool = True) -> str:
        """Chain of lines/arcs whose ends are SHARED sketch points.

        Separately drawn lines/arcs only meet within floating-point noise;
        binding every end to the same Point2D (StartPoint/EndPoint) makes the
        contour truly connected, which a Pad/Pocket/Shaft of an arbitrary
        profile needs.
        """
        self._ensure_sketch_open()
        f = self._active_factory
        if not segments:
            raise ValueError("segments is empty.")

        verts = [tuple(map(float, start))]
        for i, seg in enumerate(segments):
            to = seg.get("to")
            if to is None:
                if closed and i == len(segments) - 1:
                    to = verts[0]
                else:
                    raise ValueError(f"segment {i + 1} has no 'to' point.")
            verts.append(tuple(map(float, to)))
        if closed and math.dist(verts[-1], verts[0]) > 1e-6:
            raise ValueError(
                f"closed profile does not return to its start: ends at {verts[-1]}, "
                f"starts at {verts[0]}. Add a final segment (its 'to' may be omitted)."
            )

        crossing = _self_intersection(verts, segments)
        if crossing:
            raise ValueError(
                f"profile crosses itself ({crossing}). CATIA would accept it and leave an island "
                "of material (seen live: an arc drawn the wrong way round left a R19.5 cylinder "
                "inside a pocket). Check each arc's 'direction'. Nothing was drawn."
            )

        points = [f.CreatePoint(*v) for v in (verts[:-1] if closed else verts)]
        if closed:
            points.append(points[0])

        report = []
        for i, seg in enumerate(segments):
            a, b = verts[i], verts[i + 1]
            pa, pb = points[i], points[i + 1]
            kind = seg.get("type", "line")
            if kind == "line":
                line = f.CreateLine(a[0], a[1], b[0], b[1])
                line.StartPoint, line.EndPoint = pa, pb
                report.append(f"line->{list(b)}")
            elif kind == "arc":
                c = tuple(map(float, seg["center"]))
                r = math.dist(a, c)
                if abs(math.dist(b, c) - r) > 1e-3:
                    raise ValueError(
                        f"arc {i + 1}: start {list(a)} and end {list(b)} are not at the same "
                        f"distance from center {list(c)} ({r:.4f} vs {math.dist(b, c):.4f})."
                    )
                ang_a = math.atan2(a[1] - c[1], a[0] - c[0])
                ang_b = math.atan2(b[1] - c[1], b[0] - c[0])
                ccw = seg.get("direction", "ccw") == "ccw"
                s, e = (ang_a, ang_b) if ccw else (ang_b, ang_a)
                if e <= s:
                    e += 2 * math.pi
                arc = f.CreateCircle(c[0], c[1], r, s, e)
                arc.CenterPoint = f.CreatePoint(*c)
                arc.StartPoint, arc.EndPoint = (pa, pb) if ccw else (pb, pa)
                report.append(f"arc R{r:.3f} ({'ccw' if ccw else 'cw'})->{list(b)}")
            else:
                raise ValueError(f"segment {i + 1}: unknown type '{kind}' (line|arc).")

        checks = _arc_warnings(verts, segments)
        preview = _save_preview(verts, segments)
        return (
            f"{'Closed' if closed else 'Open'} profile of {len(segments)} connected "
            f"segment(s) from {list(verts[0])}: " + ", ".join(report)
            + "".join(f"\n[check] {w}" for w in checks)
            + (f"\n[preview] {preview} — compare it with the drawing view BEFORE the feature "
               "(an arc drawn the wrong way round is a valid contour that CATIA accepts)." if preview else "")
        )

    def _two_circle_contour(self, c1: list[float], r1: float, c2: list[float], r2: float) -> str:
        """Outer contour of two circles joined by their external tangents
        (connecting rod / lever / tow-bar shape)."""
        (x1, y1), (x2, y2) = map(float, c1), map(float, c2)
        d = math.dist((x1, y1), (x2, y2))
        if d <= abs(r1 - r2):
            raise ValueError("One circle contains the other: no external tangent.")
        ex, ey = (x2 - x1) / d, (y2 - y1) / d
        px, py = -ey, ex
        s = (r1 - r2) / d
        c = math.sqrt(1 - s * s)
        n_top = (s * ex + c * px, s * ey + c * py)
        n_bot = (s * ex - c * px, s * ey - c * py)

        def at(cx, cy, r, n):
            return [cx + r * n[0], cy + r * n[1]]

        t1, t2 = at(x1, y1, r1, n_top), at(x2, y2, r2, n_top)
        b1, b2 = at(x1, y1, r1, n_bot), at(x2, y2, r2, n_bot)
        result = self._profile(b1, [
            {"type": "line", "to": b2},
            {"type": "arc", "center": [x2, y2], "to": t2},
            {"type": "line", "to": t1},
            {"type": "arc", "center": [x1, y1]},
        ])
        return (
            f"Two-circle contour: R{r1} at {list(c1)} + R{r2} at {list(c2)}, tangent points "
            f"top {[round(v, 4) for v in t1]} / {[round(v, 4) for v in t2]}, bottom "
            f"{[round(v, 4) for v in b1]} / {[round(v, 4) for v in b2]}. {result}"
        )

    def _draw_spline(self, points: list[list[float]], closed: bool = False) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory

        # Create a spline using control points
        # CATIA V5 Sketch.OpenEdition() returns a Factory2D
        # Factory2D.CreateSpline expects an array of 2D points
        spline_pts = []
        for pt in points:
            ctrl_pt = factory.CreatePoint(pt[0], pt[1])
            spline_pts.append(ctrl_pt)

        factory.CreateSpline(spline_pts)

        if closed and len(points) >= 3:
            # Close the spline by adding a line from last to first point
            factory.CreateLine(points[-1][0], points[-1][1], points[0][0], points[0][1])

        pts_str = ", ".join(f"({p[0]}, {p[1]})" for p in points)
        return f"Spline created through {len(points)} points: {pts_str}" + (
            " (closed)" if closed else ""
        )

    def _draw_point(self, x: float, y: float) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        factory.CreatePoint(x, y)
        return f"Point created at ({x}, {y}) mm"

    def _add_constraint(self, args: dict[str, Any]) -> str:
        self._ensure_sketch_open()
        sketch = self._active_sketch
        constraint_type = args["type"]
        value = args.get("value")
        idx1 = args.get("geometry_index_1")
        idx2 = args.get("geometry_index_2")

        constraints = sketch.Constraints
        geom = sketch.GeometricElements

        # Dimensional constraints (need a geometry reference + value)
        if constraint_type in ("distance", "radius", "angle"):
            if value is None:
                raise ValueError(f"Constraint type '{constraint_type}' requires a 'value' parameter.")
            if idx1 is None:
                raise ValueError(f"Constraint type '{constraint_type}' requires 'geometry_index_1'.")

            ref1 = geom.Item(idx1)

            if constraint_type == "distance" and idx2 is not None:
                ref2 = geom.Item(idx2)
                cst = constraints.AddBiEltCst(0, ref1, ref2)  # catCstTypeDistance = 0
                cst.Dimension.Value = value
            elif constraint_type == "distance":
                cst = constraints.AddMonoEltCst(0, ref1)  # Length constraint
                cst.Dimension.Value = value
            elif constraint_type == "radius":
                cst = constraints.AddMonoEltCst(1, ref1)  # catCstTypeRadius = 1
                cst.Dimension.Value = value
            elif constraint_type == "angle":
                if idx2 is None:
                    raise ValueError("Angle constraint requires 'geometry_index_2'.")
                ref2 = geom.Item(idx2)
                cst = constraints.AddBiEltCst(2, ref1, ref2)  # catCstTypeAngle = 2
                cst.Dimension.Value = value

            return f"{constraint_type.capitalize()} constraint added: {value} {'mm' if constraint_type != 'angle' else '°'}"

        # Geometric constraints (no value needed)
        cst_type_map = {
            "coincidence": 3,   # catCstTypeOn
            "tangent": 4,       # catCstTypeTangent
            "perpendicular": 6, # catCstTypePerpendicular
            "parallel": 7,      # catCstTypeParallel
            "horizontal": 8,    # catCstTypeHorizontality
            "vertical": 9,      # catCstTypeVerticality
            "fix": 10,          # catCstTypeFix
        }

        cst_code = cst_type_map.get(constraint_type)
        if cst_code is None:
            raise ValueError(f"Unknown constraint type: {constraint_type}")

        if constraint_type in ("horizontal", "vertical", "fix"):
            if idx1 is None:
                raise ValueError(f"Constraint '{constraint_type}' requires 'geometry_index_1'.")
            ref1 = geom.Item(idx1)
            constraints.AddMonoEltCst(cst_code, ref1)
        else:
            if idx1 is None or idx2 is None:
                raise ValueError(
                    f"Constraint '{constraint_type}' requires both 'geometry_index_1' and 'geometry_index_2'."
                )
            ref1 = geom.Item(idx1)
            ref2 = geom.Item(idx2)
            constraints.AddBiEltCst(cst_code, ref1, ref2)

        return f"{constraint_type.capitalize()} constraint added"

    def _get_geometry(self) -> str:
        self._ensure_sketch_open()
        sketch = self._active_sketch
        geom = sketch.GeometricElements

        elements = []
        for i in range(1, geom.Count + 1):
            elem = geom.Item(i)
            info = {
                "index": i,
                "name": elem.Name,
            }
            # Try to get the geometry type
            try:
                info["type"] = elem.GeometricType
            except Exception:
                pass
            elements.append(info)

        if not elements:
            return "No geometry elements in the active sketch"
        return json.dumps(elements, indent=2)
