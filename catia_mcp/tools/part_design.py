"""Part Design tools for CATIA V5.

3D feature creation: Pad, Pocket, Fillet, Chamfer, Shaft, Groove, Hole,
RectPattern, CircPattern, Mirror, Rib, Slot, Shell, Thickness, Draft.
"""

from __future__ import annotations

import json
import math
from typing import Any

from catia_mcp import geometry
from catia_mcp.connection import CATIAConnection


class PartDesignTools:
    """Tools for 3D Part Design features in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_pad",
                "description": (
                    "Create a Pad (extrusion) from the last sketch. "
                    "Extrudes a 2D profile into a 3D solid along the normal to the sketch plane."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "height": {
                            "type": "number",
                            "description": "Extrusion height/depth in mm",
                        },
                        "direction": {
                            "type": "string",
                            "description": "Extrusion direction: 'normal' (default), 'reverse', 'both'",
                            "enum": ["normal", "reverse", "both"],
                            "default": "normal",
                        },
                        "symmetric": {
                            "type": "boolean",
                            "description": "If true, extrude equally on both sides (total = height)",
                            "default": False,
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use. If not specified, uses the last created sketch.",
                        },
                        "second_height": {
                            "type": "number",
                            "description": (
                                "Independent length for the OPPOSITE side (CATIA's 'Second Limit'), "
                                "e.g. height=21, second_height=1 extrudes 21mm one way and 1mm the "
                                "other — NOT the same as symmetric (which forces both sides equal). "
                                "Ignored if symmetric=true."
                            ),
                        },
                    },
                    "required": ["height"],
                },
            },
            {
                "name": "catia_pocket",
                "description": (
                    "Create a Pocket (cut extrusion) from the last sketch. With "
                    "direction='auto' (default) the tool checks that material was "
                    "really removed and flips the direction if not (CATIA's default "
                    "side is unreliable — proven live). Limits: fixed depth, "
                    "'up_to_next' (Jusqu'à la suivante) or 'up_to_last' (through)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "depth": {
                            "type": "number",
                            "description": "Cut depth in mm (limit='dimension').",
                        },
                        "limit": {
                            "type": "string",
                            "enum": ["dimension", "up_to_next", "up_to_last"],
                            "default": "dimension",
                        },
                        "direction": {
                            "type": "string",
                            "description": "auto = CATIA default side (against the sketch normal), flipped automatically if it removed nothing; normal = CATIA default side; reverse = the other side.",
                            "enum": ["auto", "normal", "reverse"],
                            "default": "auto",
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use. If not specified, uses the last sketch.",
                        },
                    },
                },
            },
            {
                "name": "catia_shaft",
                "description": (
                    "Create a Shaft (revolution) from the last sketch. The revolution "
                    "axis is the sketch axis: either a line drawn with "
                    "catia_sketch_line(is_axis=true) (axis='sketch_line', default), or "
                    "the sketch's own H or V axis (axis='h'/'v', e.g. 'the X axis "
                    "available in the sketch' on an XY sketch). The closed profile "
                    "must lie entirely on one side of the axis."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "angle": {
                            "type": "number",
                            "description": "Revolution angle in degrees (default: 360 for full revolution)",
                            "default": 360,
                        },
                        "axis": {
                            "type": "string",
                            "enum": ["sketch_line", "h", "v"],
                            "default": "sketch_line",
                            "description": "Revolution axis: the is_axis line of the sketch, or its H / V absolute axis.",
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use.",
                        },
                    },
                },
            },
            {
                "name": "catia_groove",
                "description": (
                    "Create a Groove (revolution cut). Same axis rules as catia_shaft. "
                    "Works as the FIRST feature of an empty body (it then holds the "
                    "removed volume; assemble that body into the main one to cut it — "
                    "the 'Machined' body pattern)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "angle": {
                            "type": "number",
                            "description": "Revolution angle in degrees (default: 360)",
                            "default": 360,
                        },
                        "axis": {
                            "type": "string",
                            "enum": ["sketch_line", "h", "v"],
                            "default": "sketch_line",
                            "description": "Revolution axis: the is_axis line of the sketch, or its H / V absolute axis.",
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use.",
                        },
                    },
                },
            },
            {
                "name": "catia_fillet",
                "description": (
                    "Constant-radius edge fillet on one or more edges, as ONE fillet "
                    "feature (like selecting several edges in the Congé d'arête dialog). "
                    "Edges are designated by a 3D point lying on each of them (e.g. the "
                    "midpoint of the edge read on the drawing); searched in the active "
                    "body. Tangent edges are propagated automatically. Use "
                    "catia_list_edges to see edge endpoints if unsure."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "radius": {
                            "type": "number",
                            "description": "Fillet radius in mm",
                        },
                        "edge_points": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "description": "One [x, y, z] (mm) per edge, each ON the edge.",
                        },
                    },
                    "required": ["radius", "edge_points"],
                },
            },
            {
                "name": "catia_chamfer",
                "description": (
                    "Chamfer one or more edges as ONE feature. Edges designated by a 3D "
                    "point on each (same as catia_fillet). Mode length+angle (default "
                    "45°) or two lengths."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "length": {
                            "type": "number",
                            "description": "Chamfer length in mm",
                        },
                        "angle": {
                            "type": "number",
                            "description": "Chamfer angle in degrees (default: 45). Ignored if length2 is given.",
                            "default": 45,
                        },
                        "length2": {
                            "type": "number",
                            "description": "Second length (mm) for a length/length chamfer.",
                        },
                        "edge_points": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "description": "One [x, y, z] (mm) per edge, each ON the edge.",
                        },
                    },
                    "required": ["length", "edge_points"],
                },
            },
            {
                "name": "catia_hole",
                "description": (
                    "Create a Hole (Trou) at a 3D point on a planar face, drilled along "
                    "the face normal into the material. The face is the one through "
                    "'point' (active body first, then other bodies — a hole in a "
                    "'Machined' body on a face of the 'Rough' body works). Types: "
                    "simple, tapered (conique, taper_angle), counterbored (lamé, "
                    "head_diameter + head_depth), countersunk (fraisé, head_diameter + "
                    "head_angle). Limit: fixed 'depth', or 'up_to_point' = a point inside "
                    "the plane/face where the hole must stop (Jusqu'au plan)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "point": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": "[x, y, z] mm: hole centre, lying ON the entry face.",
                        },
                        "diameter": {
                            "type": "number",
                            "description": "Hole diameter in mm",
                        },
                        "depth": {
                            "type": "number",
                            "description": "Hole depth in mm (ignored when up_to_point is given).",
                        },
                        "up_to_point": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": "[x, y, z] mm of a point inside the face/plane the hole goes up to.",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["simple", "tapered", "counterbored", "countersunk"],
                            "default": "simple",
                        },
                        "taper_angle": {"type": "number", "description": "tapered: cone angle in degrees."},
                        "head_diameter": {"type": "number", "description": "counterbored/countersunk: head diameter (mm)."},
                        "head_depth": {"type": "number", "description": "counterbored: head depth (mm)."},
                        "head_angle": {"type": "number", "description": "countersunk: head angle (degrees, default 90)."},
                        "threaded": {
                            "type": "boolean",
                            "description": "Whether to add threading (default: false)",
                            "default": False,
                        },
                    },
                    "required": ["point", "diameter"],
                },
            },
            {
                "name": "catia_rect_pattern",
                "description": (
                    "Rectangular Pattern (Répétition rectangulaire) of a feature along "
                    "two global axes."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dir1": {"type": "string", "enum": ["x", "y", "z"], "default": "x", "description": "First direction (global axis)."},
                        "dir2": {"type": "string", "enum": ["x", "y", "z"], "default": "y", "description": "Second direction (global axis)."},
                        "dir1_count": {
                            "type": "integer",
                            "description": "Number of instances in first direction",
                        },
                        "dir1_spacing": {
                            "type": "number",
                            "description": "Spacing in first direction (mm)",
                        },
                        "dir2_count": {
                            "type": "integer",
                            "description": "Number of instances in second direction (default: 1)",
                            "default": 1,
                        },
                        "dir2_spacing": {
                            "type": "number",
                            "description": "Spacing in second direction (mm)",
                            "default": 0,
                        },
                        "feature_name": {
                            "type": "string",
                            "description": "Name of the feature to pattern. Defaults to last feature.",
                        },
                    },
                    "required": ["dir1_count", "dir1_spacing"],
                },
            },
            {
                "name": "catia_circ_pattern",
                "description": (
                    "Circular Pattern (Répétition circulaire) of a feature around an "
                    "axis: the normal of an origin plane through the origin (axis_plane, "
                    "e.g. 'zx' = around Y), or the axis of the cylindrical face through "
                    "axis_face_point. count instances including the original; default "
                    "spacing 360/count = complete crown."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "Number of instances, the original included.",
                        },
                        "angular_spacing": {
                            "type": "number",
                            "description": "Angular spacing in degrees (default: 360/count, complete crown).",
                        },
                        "axis_plane": {
                            "type": "string", "enum": ["xy", "yz", "zx"],
                            "description": "Rotation axis = normal of this origin plane (xy→Z, yz→X, zx→Y).",
                        },
                        "axis_face_point": {
                            "type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                            "description": "[x, y, z] on a cylindrical face whose axis is the rotation axis.",
                        },
                        "reverse": {"type": "boolean", "default": False, "description": "Rotate the other way."},
                        "feature_name": {
                            "type": "string",
                            "description": "Feature to pattern. Defaults to last feature.",
                        },
                    },
                    "required": ["count"],
                },
            },
            {
                "name": "catia_mirror",
                "description": (
                    "Symmetry of the whole active body's current solid about a plane "
                    "(Symétrie): the result is the body plus its mirror image. Plane = an "
                    "origin plane, or the planar face through plane_face_point."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plane": {"type": "string", "enum": ["xy", "yz", "zx"]},
                        "plane_face_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                    },
                },
            },
            {
                "name": "catia_shell",
                "description": (
                    "Shell (Coque): hollow the solid, keeping walls of 'thickness' mm inside "
                    "(outer_thickness adds material outside). The faces through "
                    "open_face_points are removed (openings). Verified live."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thickness": {"type": "number", "description": "Inner wall thickness (mm)."},
                        "outer_thickness": {"type": "number", "default": 0},
                        "open_face_points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
                    },
                    "required": ["thickness", "open_face_points"],
                },
            },
            {
                "name": "catia_draft",
                "description": (
                    "Draft (Dépouille) of the faces through face_points by 'angle' degrees, "
                    "about the neutral face through neutral_face_point (or an origin plane "
                    "neutral_plane), pulling along 'direction'. Verified live."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "angle": {"type": "number"},
                        "face_points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
                        "neutral_face_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                        "neutral_plane": {"type": "string", "enum": ["xy", "yz", "zx"]},
                        "direction": {**{"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}, "description": "Pulling direction [dx, dy, dz]."},
                    },
                    "required": ["angle", "face_points", "direction"],
                },
            },
            {
                "name": "catia_thickness",
                "description": "Thickness (Épaisseur): offset the faces through face_points by 'offset' mm (negative removes). Verified live.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "offset": {"type": "number"},
                        "face_points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
                    },
                    "required": ["offset", "face_points"],
                },
            },
            {
                "name": "catia_thread",
                "description": (
                    "Thread / tap (Filetage / Taraudage, cosmetic as in the GUI) on a "
                    "cylindrical face, starting from a planar limit face. Faces designated "
                    "by a 3D point on each (lateral_face_point on the cylinder, "
                    "limit_face_point on the end face where the thread starts). "
                    "E.g. M100x4 over 47 mm: diameter=100, pitch=4, depth=47. Verified live."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "lateral_face_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                        "limit_face_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                        "diameter": {"type": "number", "description": "Nominal diameter (mm)."},
                        "pitch": {"type": "number", "description": "Pitch (mm)."},
                        "depth": {"type": "number", "description": "Threaded length (mm)."},
                        "polarity": {"type": "string", "enum": ["thread", "tap"], "default": "thread", "description": "thread = external (filetage), tap = internal (taraudage)."},
                        "left_hand": {"type": "boolean", "default": False},
                    },
                    "required": ["lateral_face_point", "limit_face_point", "diameter", "pitch", "depth"],
                },
            },
            {
                "name": "catia_rename_feature",
                "description": (
                    "Rename a feature or sketch in the active Part Body. Sets the .Name "
                    "COM property directly (CATIA V5 exposes Feature.Name and Sketch.Name "
                    "as read/write) — no macro/VBA bridge required. Use this right after "
                    "creating a feature (catia_pad, catia_pocket, ...) instead of asking "
                    "the user to rename it by hand in the GUI."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "new_name": {
                            "type": "string",
                            "description": "New explicit name, e.g. 'Pad_bague_ext_30mm'.",
                        },
                        "old_name": {
                            "type": "string",
                            "description": (
                                "Current name to look up (e.g. 'Pad.1'). If omitted, "
                                "renames the most recently created feature/sketch."
                            ),
                        },
                        "kind": {
                            "type": "string",
                            "description": (
                                "Where to look for old_name: 'feature' (Body.Shapes), "
                                "'sketch' (Body.Sketches), or 'auto' (try both)."
                            ),
                            "enum": ["auto", "feature", "sketch"],
                            "default": "auto",
                        },
                    },
                    "required": ["new_name"],
                },
            },
            {
                "name": "catia_delete_feature",
                "description": (
                    "Delete a feature or sketch from the active Body (right-click > "
                    "Delete in the GUI). Use this to clean up after a failed/wrong "
                    "operation instead of closing and recreating the whole document — "
                    "e.g. a Shaft/Groove that raised an UpdateObject error can still "
                    "leave a broken feature behind in the tree; delete it, fix the "
                    "sketch, and retry."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the feature or sketch to delete (e.g. 'Gorge.1'). Required: the tool never guesses which feature to delete.",
                        },
                        "kind": {
                            "type": "string",
                            "description": "Where to look for name: 'feature' (Body.Shapes), 'sketch' (Body.Sketches), or 'auto' (try both).",
                            "enum": ["auto", "feature", "sketch"],
                            "default": "auto",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "catia_list_features",
                "description": "List all features in the active Part Body with their names and types.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_list_edges",
                "description": (
                    "List the edges of a body's current solid with length and "
                    "start/middle/end points (mm) — pick an edge's middle point to pass "
                    "to catia_fillet / catia_chamfer edge_points."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body_name": {"type": "string", "description": "Body to inspect (default: active body)."},
                    },
                },
            },
            {
                "name": "catia_list_faces",
                "description": (
                    "List the faces of a body's current solid: type, area, centre of "
                    "gravity; planar faces: plane, parallel origin plane (xy/yz/zx + "
                    "offset) and an inside_point guaranteed ON the face (the COG of a "
                    "holed face is in the hole) — use it as face_point; cylinders/cones: "
                    "radius and axis — any point on them works as axis_point."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body_name": {"type": "string", "description": "Body to inspect (default: active body)."},
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_pad":
                return self._pad(arguments)
            case "catia_pocket":
                return self._pocket(arguments)
            case "catia_shaft":
                return self._shaft(arguments)
            case "catia_groove":
                return self._groove(arguments)
            case "catia_fillet":
                return self._fillet(arguments)
            case "catia_chamfer":
                return self._chamfer(arguments)
            case "catia_hole":
                return self._hole(arguments)
            case "catia_rect_pattern":
                return self._rect_pattern(arguments)
            case "catia_circ_pattern":
                return self._circ_pattern(arguments)
            case "catia_mirror":
                return self._mirror(arguments)
            case "catia_shell":
                return self._shell(arguments)
            case "catia_draft":
                return self._draft(arguments)
            case "catia_thickness":
                return self._thickness(arguments)
            case "catia_thread":
                return self._thread(arguments)
            case "catia_rename_feature":
                return self._rename_feature(arguments)
            case "catia_delete_feature":
                return self._delete_feature(arguments)
            case "catia_list_features":
                return self._list_features()
            case "catia_list_edges":
                return self._list_edges(arguments)
            case "catia_list_faces":
                return self._list_faces(arguments)
            case _:
                raise ValueError(f"Unknown part design tool: {tool_name}")

    def _get_last_sketch(self, sketch_name: str | None = None) -> Any:
        """Get a sketch by name or the last sketch in the body."""
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        sketches = body.Sketches

        if sketch_name:
            sketch = sketches.Item(sketch_name)
        elif sketches.Count == 0:
            raise RuntimeError("No sketches found in the active body. Create a sketch first.")
        else:
            sketch = sketches.Item(sketches.Count)
        # Arbre (prouvé R19) : AddNewPad/Pocket/Shaft/Groove ne rangent l'esquisse SOUS
        # la feature que si l'esquisse est l'objet de travail ; sinon elle reste à côté,
        # au niveau du corps (seule la toute première feature d'un corps l'absorbe).
        # Seulement pour la dernière esquisse : la feature s'insère après l'objet de
        # travail, une esquisse ancienne la placerait au milieu de l'historique.
        if sketch.Name == sketches.Item(sketches.Count).Name:
            try:
                part.InWorkObject = sketch
            except Exception:
                pass
        return sketch

    def _get_last_shape(self, feature_name: str | None = None) -> Any:
        """Get a shape/feature by name or the last one in the body."""
        body = self.conn.get_active_part_body()
        shapes = body.Shapes

        if feature_name:
            return shapes.Item(feature_name)

        if shapes.Count == 0:
            raise RuntimeError("No features found in the active body.")
        return shapes.Item(shapes.Count)

    def _rename_feature(self, args: dict[str, Any]) -> str:
        """Rename a feature (Body.Shapes item) or sketch (Body.Sketches item).

        CATIA V5's Automation API exposes `Name` as a plain read/write property
        on Feature and Sketch objects (AnyObject.Name). Setting it via COM is
        equivalent to renaming through the GUI (Properties > Feature Name) and
        is immediately visible in the specification tree — no VBA/SystemService
        macro bridge is needed.
        """
        self.conn.ensure_connected()
        new_name = args["new_name"]
        old_name = args.get("old_name")
        kind = args.get("kind", "auto")

        target, found_in = self._find_feature_or_sketch(old_name, kind)

        previous_name = target.Name
        target.Name = new_name

        # Verify the COM write actually stuck (some CATIA states silently reject it,
        # e.g. a locked/in-work-object feature) instead of reporting a false success.
        if target.Name != new_name:
            raise RuntimeError(
                f"Rename did not take effect: still named '{target.Name}' after "
                f"setting '{new_name}'. The feature may be read-only in this context."
            )

        return f"Renamed {found_in} '{previous_name}' -> '{new_name}'"

    def _find_feature_or_sketch(self, name: str | None, kind: str = "auto") -> tuple[Any, str]:
        """Shared lookup for catia_rename_feature and catia_delete_feature.

        Returns (com_object, "feature"|"sketch"). Raises if not found.
        """
        body = self.conn.get_active_part_body()
        target = None
        found_in = None

        if kind in ("feature", "auto"):
            shapes = body.Shapes
            if name:
                for i in range(1, shapes.Count + 1):
                    if shapes.Item(i).Name == name:
                        target = shapes.Item(i)
                        found_in = "feature"
                        break
            elif shapes.Count > 0:
                target = shapes.Item(shapes.Count)
                found_in = "feature"

        if target is None and kind in ("sketch", "auto"):
            sketches = body.Sketches
            if name:
                for i in range(1, sketches.Count + 1):
                    if sketches.Item(i).Name == name:
                        target = sketches.Item(i)
                        found_in = "sketch"
                        break
            elif sketches.Count > 0:
                target = sketches.Item(sketches.Count)
                found_in = "sketch"

        if target is None:
            where = f"'{name}'" if name else "(last created item)"
            raise RuntimeError(
                f"Could not find {where} in the active body's Shapes/Sketches. "
                "Check the name with catia_list_features first."
            )
        return target, found_in

    def _delete_feature(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        name = args.get("name")
        if not name:
            raise ValueError("catia_delete_feature needs the exact 'name' of what to delete (see catia_list_features).")
        kind = args.get("kind", "auto")

        target, found_in = self._find_feature_or_sketch(name, kind)
        target_name = target.Name

        sel = self.conn.hso
        sel.Clear()
        sel.Add(target)
        sel.Delete()
        sel.Clear()

        update_note = ""
        try:
            self.conn.get_active_part().Update()
        except Exception as e:
            update_note = f" (deleted OK, but part.Update() afterward failed: {e})"

        self.conn.refresh_display()
        return f"Deleted {found_in} '{target_name}'.{update_note}"

    def _pad(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        height = args["height"]
        direction = args.get("direction", "normal")
        symmetric = args.get("symmetric", False)
        second_height = args.get("second_height")

        body = self.conn.get_active_part_body()
        volume_before = self._body_volume(part, body)
        pad = sf.AddNewPad(sketch, height)

        mode = direction
        if second_height is not None:
            # Independent asymmetric two-limit pad (e.g. First Limit 21mm,
            # Second Limit 1mm) — NOT the same as symmetric.
            pad.SecondLimit.Dimension.Value = second_height
        elif symmetric or direction == "both":
            # CATIA's mirrored extent puts the FIRST length on EACH side (proven
            # live: a 150 lug came out 300 thick). The schema promises total =
            # height, so each side gets height / 2.
            pad.FirstLimit.Dimension.Value = height / 2.0
            pad.IsSymmetric = True
            mode = f"symmetric, {height / 2.0:g} each side"
        elif direction == "reverse":
            pad.DirectionOrientation = 1  # catReverse

        part.UpdateObject(pad)
        self.conn.refresh_display()
        second_note = f" + {second_height} mm second limit" if second_height is not None else ""
        change = self._body_volume(part, body) - volume_before
        return (
            f"Pad created: {height} mm ({mode}){second_note}, volume change {change:+.2f} mm³. "
            f"Feature: '{pad.Name}'"
        )

    def _pocket(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        limit = args.get("limit", "dimension")
        depth = float(args.get("depth") or 10.0)
        if limit == "dimension" and args.get("depth") is None:
            raise ValueError("depth is required when limit='dimension'.")
        direction = args.get("direction", "auto")

        volume_before = self._body_volume(part, body)
        pocket = sf.AddNewPocket(sketch, depth)
        # LimitMode: 0 dimension, 1 up to next, 2 up to last (CatLimitMode).
        pocket.FirstLimit.LimitMode = {"dimension": 0, "up_to_next": 1, "up_to_last": 2}[limit]
        # Proven live: DirectionOrientation is RELATIVE to the sketch normal
        # (0 = along it, 1 = against it) and CATIA's default is 1. That default
        # cuts into the material for a sketch on a top face, but cuts nothing
        # for a sketch on a pad's base plane. 'auto' keeps the default and flips
        # only if the body did not change.
        default_side = pocket.DirectionOrientation
        if direction == "reverse":
            pocket.DirectionOrientation = 1 - default_side
        self._update_or_remove(part, pocket, "pocket")

        # "Did it do anything" = the body volume CHANGED. Not "decreased": in a
        # body that holds removal volumes ('Machined' body pattern: groove/hole
        # as first features) a pocket makes the measured volume grow.
        def unchanged() -> bool:
            return abs(self._body_volume(part, body) - volume_before) < 1e-3

        note = ""
        if direction == "auto" and volume_before and unchanged():
            pocket.DirectionOrientation = 1 - default_side
            self._update_or_remove(part, pocket, "pocket (reversed)")
            if unchanged():
                self._delete_silently(pocket)
                raise RuntimeError(
                    "The pocket removes no material in either direction: the profile "
                    "does not overlap the solid. The feature was removed."
                )
            note = " (direction flipped automatically: the default side cut nothing)"
        removed = ""
        if volume_before:
            removed = f", volume change {self._body_volume(part, body) - volume_before:+.2f} mm³"

        self.conn.refresh_display()
        spec = f"{depth} mm deep" if limit == "dimension" else limit.replace("_", " ")
        return f"Pocket created: {spec}{removed}{note}. Feature: '{pocket.Name}'"

    def _body_volume(self, part: Any, body: Any) -> float:
        """Volume (mm³) of the body's current result, 0 if it has none.

        Measurable.Volume is a plain property (no ByRef array), so it works
        from Python directly; it is in m³."""
        try:
            if body.Shapes.Count == 0:
                return 0.0
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            return spa.GetMeasurable(part.CreateReferenceFromObject(body)).Volume * 1e9
        except Exception:
            return 0.0

    def _sketch_axis_name(self, sketch: Any) -> str | None:
        """Return the name of the sketch's CenterLine, or None if it has none."""
        try:
            axis = sketch.CenterLine
            return axis.Name if axis is not None else None
        except Exception:
            return None

    def _delete_silently(self, obj: Any) -> None:
        """Remove a feature that failed to update, so it doesn't poison the tree."""
        try:
            sel = self.conn.hso
            sel.Clear()
            sel.Add(obj)
            sel.Delete()
            sel.Clear()
        except Exception:
            pass

    def _revolve(self, args: dict[str, Any], kind: str) -> str:
        """Shared Shaft/Groove implementation.

        Root cause found live 2026-09-28 (after ruling out FirstAngle,
        SecondAngle, Line2D.Construction and Shaft.RevoluteAxis): CATIA reads
        the revolution axis from Sketch.CenterLine. With it set, AddNewShaft/
        AddNewGroove + part.Update() work; without it, Update fails with a
        generic E_FAIL. catia_sketch_line(is_axis=true) sets it.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        self.conn.get_active_part_body()
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        angle = args.get("angle", 360)

        axis_choice = args.get("axis", "sketch_line")
        if axis_choice in ("h", "v"):
            # Proven live: CenterLine accepts the sketch's absolute H/V axis,
            # set while the sketch is open for edition.
            sketch.OpenEdition()
            absolute = sketch.AbsoluteAxis
            sketch.CenterLine = (
                absolute.HorizontalReference if axis_choice == "h" else absolute.VerticalReference
            )
            sketch.CloseEdition()

        axis_name = self._sketch_axis_name(sketch)
        if axis_name is None:
            raise RuntimeError(
                f"Sketch '{sketch.Name}' has no axis. Reopen it (or draw a new "
                "sketch) and add the revolution axis with "
                "catia_sketch_line(..., is_axis=true) before calling "
                f"catia_{kind}. Nothing was created."
            )

        feature = sf.AddNewShaft(sketch) if kind == "shaft" else sf.AddNewGroove(sketch)
        # FirstAngle is a Parameter object: a bare assignment raises
        # "Property ...FirstAngle can not be set." (live-tested).
        feature.FirstAngle.Value = angle

        try:
            part.Update()
        except Exception as e:
            self._delete_silently(feature)
            hint = (
                " A groove needs existing material in the active body to cut."
                if kind == "groove"
                else ""
            )
            raise RuntimeError(
                f"CATIA refused the {kind} (axis '{axis_name}'): {e}. Check that "
                "the profile is closed and lies entirely on one side of the axis."
                f"{hint} The failed feature was removed from the tree."
            ) from e

        self.conn.refresh_display()
        label = "Shaft (revolution)" if kind == "shaft" else "Groove (revolution cut)"
        return f"{label} created: {angle}° around '{axis_name}'. Feature: '{feature.Name}'"

    def _shaft(self, args: dict[str, Any]) -> str:
        return self._revolve(args, "shaft")

    def _groove(self, args: dict[str, Any]) -> str:
        return self._revolve(args, "groove")

    def _edge_refs(self, points: list[list[float]]) -> list[Any]:
        """Resolve edge_points to edge references on the active body's solid.

        The old name-based lookup could never work: it searched
        "Topology.Edge" (invalid, the type is "Topology.CGMEdge") and edges
        have no stable user-visible names anyway. See geometry.py.
        """
        if not points:
            raise ValueError("edge_points is required: one [x, y, z] on each edge.")
        body = self.conn.get_active_part_body()
        picked = geometry.pick_many(self.conn.app, self.conn.active_document, body, "edge", points)
        return [ref for ref, _ in picked]

    def _update_or_remove(self, part: Any, feature: Any, what: str) -> None:
        """part.Update(); on failure remove the feature so it cannot poison
        every later update (proven live), and raise a useful message."""
        try:
            part.Update()
        except Exception as e:
            self._delete_silently(feature)
            raise RuntimeError(
                f"CATIA refused the {what}: {e}. The failed feature was removed from the tree."
            ) from e

    def _fillet(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        radius = args["radius"]
        refs = self._edge_refs(args.get("edge_points") or [])

        # 1 = catTangencyFilletEdgePropagation (proven live with 2 edges).
        fillet = part.ShapeFactory.AddNewSolidEdgeFilletWithConstantRadius(refs[0], 1, radius)
        for ref in refs[1:]:
            fillet.AddObjectToFillet(ref)
        self._update_or_remove(part, fillet, f"fillet R{radius}")

        self.conn.refresh_display()
        return f"Fillet created: R{radius} mm on {len(refs)} edge(s). Feature: '{fillet.Name}'"

    def _chamfer(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        length = args["length"]
        length2 = args.get("length2")
        angle = args.get("angle", 45)
        refs = self._edge_refs(args.get("edge_points") or [])

        # AddNewChamfer(object, propagation, mode, orientation, length1, length2_or_angle).
        # Proven live: mode 0 = TWO LENGTHS, mode 1 = LENGTH + ANGLE (the
        # opposite of what one would guess: mode 0 with "45" made a 45 mm
        # chamfer and the update failed). Propagation 0 = tangency.
        # The previous code omitted orientation and passed the whole feature.
        mode = 0 if length2 is not None else 1
        chamfer = part.ShapeFactory.AddNewChamfer(
            refs[0], 0, mode, 0, length, length2 if length2 is not None else angle
        )
        for ref in refs[1:]:
            chamfer.AddElementToChamfer(ref)
        self._update_or_remove(part, chamfer, "chamfer")

        self.conn.refresh_display()
        spec = f"{length} x {length2} mm" if length2 is not None else f"{length} mm x {angle}°"
        return f"Chamfer created: {spec} on {len(refs)} edge(s). Feature: '{chamfer.Name}'"

    # CatHoleType
    _HOLE_TYPES = {"simple": 0, "tapered": 1, "counterbored": 2, "countersunk": 3}

    def _hole(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        app, doc = self.conn.app, self.conn.active_document
        sf = part.ShapeFactory

        point = [float(c) for c in args["point"]]
        diameter = args["diameter"]
        hole_type = args.get("type", "simple")
        up_to = args.get("up_to_point")
        depth = float(args.get("depth") or 10.0)
        if not up_to and args.get("depth") is None:
            raise ValueError("Give either depth or up_to_point.")

        face_ref, owner = geometry.pick_in_part(app, doc, part, "face", point, prefer=body)
        limit_ref = limit_owner = None
        if up_to:
            limit_ref, limit_owner = geometry.pick_in_part(app, doc, part, "face", up_to, prefer=body)

        hole = sf.AddNewHoleFromPoint(point[0], point[1], point[2], face_ref, depth)
        details = [f"D{diameter} mm", hole_type]
        try:
            hole.Type = self._HOLE_TYPES[hole_type]
            hole.Diameter.Value = diameter
            if hole_type == "tapered" and args.get("taper_angle") is not None:
                # Proven live: HeadAngle drives "HoleTaperedType.1\Angle".
                hole.HeadAngle.Value = args["taper_angle"]
                details.append(f"taper {args['taper_angle']}°")
            if hole_type == "counterbored":
                if args.get("head_diameter") is not None:
                    hole.HeadDiameter.Value = args["head_diameter"]
                    details.append(f"head D{args['head_diameter']}")
                if args.get("head_depth") is not None:
                    hole.HeadDepth.Value = args["head_depth"]
                    details.append(f"head depth {args['head_depth']}")
            if hole_type == "countersunk":
                # Proven live: a countersunk hole has NO HeadDiameter (raises);
                # it is driven by HeadDepth + HeadAngle. Convert a wanted head
                # diameter into the equivalent depth.
                head_angle = float(args.get("head_angle", 90))
                hole.HeadAngle.Value = head_angle
                if args.get("head_diameter") is not None:
                    head_depth = (args["head_diameter"] - diameter) / 2 / math.tan(math.radians(head_angle / 2))
                    hole.HeadDepth.Value = head_depth
                    details.append(f"head D{args['head_diameter']} (depth {head_depth:.3f})")
                elif args.get("head_depth") is not None:
                    hole.HeadDepth.Value = args["head_depth"]
                    details.append(f"head depth {args['head_depth']}")
                details.append(f"head angle {head_angle}°")
            if args.get("threaded", False):
                # CatHoleThreadingMode (type library): 0 = THREADED, 1 = smooth.
                # The previous code set 1 here, i.e. asked for a smooth hole.
                hole.ThreadingMode = 0
            if limit_ref is not None:
                limit = hole.BottomLimit
                limit.LimitMode = 3  # catUpToPlaneLimit (proven live)
                limit.LimitingElement = limit_ref
                details.append(f"up to the face of '{limit_owner.Name}' through {list(up_to)}")
            else:
                details.append(f"depth {depth} mm")
        except Exception as e:
            self._delete_silently(hole)
            raise RuntimeError(f"Hole setup failed ({e}); the feature was removed.") from e

        self._update_or_remove(part, hole, "hole")
        self.conn.refresh_display()
        return (
            f"Hole created on the face of '{owner.Name}' at {point}: "
            + ", ".join(details) + f". Feature: '{hole.Name}' (in body '{body.Name}')"
        )

    def _rect_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        sf = part.ShapeFactory

        feature = self._get_last_shape(args.get("feature_name"))
        d1_count = args["dir1_count"]
        d1_spacing = args["dir1_spacing"]
        d2_count = args.get("dir2_count", 1)
        d2_spacing = args.get("dir2_spacing", 0)
        # Direction references: the origin plane whose normal is the axis.
        plane_of_axis = {"x": "yz", "y": "zx", "z": "xy"}
        planes = self.conn.get_origin_elements()
        dir1 = part.CreateReferenceFromObject(planes[plane_of_axis[args.get("dir1", "x")]])
        dir2 = part.CreateReferenceFromObject(planes[plane_of_axis[args.get("dir2", "y")]])

        volume_before = self._body_volume(part, body)
        # Real signature (read from CATIA's type library): 12 args —
        # (shape, n1, n2, step1, step2, pos1, pos2, dir1, dir2, rev1, rev2, rotation).
        # The previous 8-argument call could never work.
        pattern = sf.AddNewRectPattern(
            feature, d1_count, d2_count, float(d1_spacing), float(d2_spacing),
            1, 1, dir1, dir2, False, False, 0.0,
        )
        self._update_or_remove(part, pattern, "rectangular pattern")
        self.conn.refresh_display()
        return (
            f"Rectangular pattern created: {d1_count}x{d2_count} along {args.get('dir1', 'x')}/"
            f"{args.get('dir2', 'y')}, spacing {d1_spacing}x{d2_spacing} mm, volume change "
            f"{self._body_volume(part, body) - volume_before:+.2f} mm³. Feature: '{pattern.Name}'"
        )

    def _circ_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        sf = part.ShapeFactory

        feature = self._get_last_shape(args.get("feature_name"))
        count = int(args["count"])
        angular_spacing = float(args.get("angular_spacing") or 360.0 / count)

        if args.get("axis_face_point"):
            axis, _ = geometry.pick_in_part(
                self.conn.app, self.conn.active_document, part, "face",
                args["axis_face_point"], prefer=body,
            )
            axis_label = f"axis of the cylindrical face through {args['axis_face_point']}"
        else:
            key = args.get("axis_plane")
            if not key:
                raise ValueError("Give axis_plane ('xy'/'yz'/'zx') or axis_face_point.")
            axis = part.CreateReferenceFromObject(self.conn.get_origin_elements()[key])
            axis_label = f"normal of plane {key.upper()}"

        volume_before = self._body_volume(part, body)
        # Real signature (type library): (shape, n_radial, n_angular, step_radial,
        # step_angular, pos_radial, pos_angular, center, axis, reversed, rotation,
        # radius_aligned). Proven live: an origin plane as center+axis rotates
        # around its normal. The previous 8-argument call had no axis at all.
        pattern = sf.AddNewCircPattern(
            feature, 1, count, 0.0, angular_spacing, 1, 1,
            axis, axis, bool(args.get("reverse", False)), 0.0, True,
        )
        self._update_or_remove(part, pattern, "circular pattern")
        self.conn.refresh_display()
        return (
            f"Circular pattern created: {count} instances every {angular_spacing:g}° around the "
            f"{axis_label}, volume change {self._body_volume(part, body) - volume_before:+.2f} mm³. "
            f"Feature: '{pattern.Name}'"
        )

    def _face_refs(self, points: list[Any]) -> list[Any]:
        if not points:
            raise ValueError("At least one face point is required.")
        body = self.conn.get_active_part_body()
        picked = geometry.pick_many(self.conn.app, self.conn.active_document, body, "face", points)
        return [ref for ref, _ in picked]

    def _change_report(self, part: Any, body: Any, before: float) -> str:
        return f"volume change {self._body_volume(part, body) - before:+.2f} mm³"

    def _mirror(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        if args.get("plane_face_point"):
            ref = self._face_refs([args["plane_face_point"]])[0]
            label = f"the face through {args['plane_face_point']}"
        else:
            key = args.get("plane")
            if key not in ("xy", "yz", "zx"):
                raise ValueError("Give plane ('xy'/'yz'/'zx') or plane_face_point.")
            ref = part.CreateReferenceFromObject(self.conn.get_origin_elements()[key])
            label = f"plane {key.upper()}"
        before = self._body_volume(part, body)
        mirror = part.ShapeFactory.AddNewMirror(ref)
        self._update_or_remove(part, mirror, "symmetry")
        return f"Symmetry about {label}, {self._change_report(part, body, before)}. Feature: '{mirror.Name}'"

    def _thread(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        lateral, limit = self._face_refs([args["lateral_face_point"], args["limit_face_point"]])
        thread = part.ShapeFactory.AddNewThreadWithRef(lateral, limit)
        # Proven live: Diameter/Depth/Pitch are plain doubles (no .Value), and the
        # feature is born a tap ("Taraudage.N") until the polarity is set
        # (CatThreadPolarity: catThread=0, catTap=1; CatThreadSide: right=0, left=1).
        thread.SetExplicitPolarity(1 if args.get("polarity") == "tap" else 0)
        thread.Side = 1 if args.get("left_hand") else 0
        thread.Diameter = float(args["diameter"])
        thread.Pitch = float(args["pitch"])
        thread.Depth = float(args["depth"])
        self._update_or_remove(part, thread, "thread")
        kind = "Tap" if args.get("polarity") == "tap" else "Thread"
        return (
            f"{kind} M{args['diameter']:g}x{args['pitch']:g} over {args['depth']:g} mm "
            f"(cosmetic, no volume change). Feature: '{thread.Name}'"
        )

    def _shell(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        refs = self._face_refs(args.get("open_face_points") or [])
        before = self._body_volume(part, body)
        # Real signature (type library): AddNewShell(face, inner, outer). The old
        # call passed the whole last feature + 4 arguments.
        shell = part.ShapeFactory.AddNewShell(refs[0], float(args["thickness"]), float(args.get("outer_thickness", 0)))
        for ref in refs[1:]:
            shell.AddFaceToRemove(ref)
        self._update_or_remove(part, shell, "shell")
        return (
            f"Shell created: {args['thickness']} mm walls, {len(refs)} face(s) opened, "
            f"{self._change_report(part, body, before)}. Feature: '{shell.Name}'"
        )

    def _draft(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        faces = self._face_refs(args.get("face_points") or [])
        if args.get("neutral_face_point"):
            neutral = self._face_refs([args["neutral_face_point"]])[0]
        elif args.get("neutral_plane"):
            neutral = part.CreateReferenceFromObject(self.conn.get_origin_elements()[args["neutral_plane"]])
        else:
            raise ValueError("Give neutral_face_point or neutral_plane.")
        dx, dy, dz = (float(v) for v in args["direction"])
        before = self._body_volume(part, body)
        # AddNewDraft(face, neutral, neutralMode, parting, dirX, dirY, dirZ, mode,
        # angle, multiselectionMode) — 10 arguments, verified live.
        draft = part.ShapeFactory.AddNewDraft(faces[0], neutral, 0, neutral, dx, dy, dz, 0, float(args["angle"]), 0)
        for ref in faces[1:]:
            draft.DraftDomains.Item(1).AddFaceToDraft(ref)
        self._update_or_remove(part, draft, "draft")
        return (
            f"Draft {args['angle']}° on {len(faces)} face(s), "
            f"{self._change_report(part, body, before)}. Feature: '{draft.Name}'"
        )

    def _thickness(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        refs = self._face_refs(args.get("face_points") or [])
        before = self._body_volume(part, body)
        thickness = part.ShapeFactory.AddNewThickness(refs[0], float(args["offset"]))
        for ref in refs[1:]:
            thickness.AddFaceToThicken(ref)
        self._update_or_remove(part, thickness, "thickness")
        return (
            f"Thickness {args['offset']:+g} mm on {len(refs)} face(s), "
            f"{self._change_report(part, body, before)}. Feature: '{thickness.Name}'"
        )

    def _list_features(self) -> str:
        self.conn.ensure_connected()
        body = self.conn.get_active_part_body()
        shapes = body.Shapes

        features = []
        for i in range(1, shapes.Count + 1):
            shape = shapes.Item(i)
            features.append({
                "index": i,
                "name": shape.Name,
                "type": shape.Type if hasattr(shape, "Type") else "unknown",
            })

        if not features:
            return "No features in the active body"
        return json.dumps(features, indent=2)

    def _inspect_body(self, args: dict[str, Any]) -> Any:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        name = args.get("body_name")
        if name:
            return part.Bodies.Item(name)
        return self.conn.get_active_part_body()

    def _list_edges(self, args: dict[str, Any]) -> str:
        body = self._inspect_body(args)
        app, doc = self.conn.app, self.conn.active_document
        refs = geometry.topology(doc, body, "edge")
        edges = []
        for i, ref in enumerate(refs, 1):
            try:
                edges.append({"index": i, **geometry.edge_info(app, ref)})
            except Exception as e:
                edges.append({"index": i, "error": str(e)[:120]})
        if not edges:
            return f"No edges found in body '{body.Name}'."
        return json.dumps({"body": body.Name, "edges": edges})

    def _list_faces(self, args: dict[str, Any]) -> str:
        body = self._inspect_body(args)
        app, doc = self.conn.app, self.conn.active_document
        faces = []
        for i, ref in enumerate(geometry.topology(doc, body, "face"), 1):
            try:
                info = geometry.face_info(app, ref)
                if info["plane"]:
                    info["inside_point"] = geometry.inside_point(app, doc, ref, info)
                std = geometry.parallel_origin_plane(info)
                if std:
                    info["parallel_to"] = {"plane": std[0], "offset": round(std[1], 3)}
                faces.append({"index": i, **info})
            except Exception as e:
                faces.append({"index": i, "error": str(e)[:120]})
        if not faces:
            return f"No faces found in body '{body.Name}'."
        return json.dumps({"body": body.Name, "faces": faces})
