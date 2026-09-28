"""Boolean operations between Bodies for CATIA V5 (multi-corps method).

The multi-body method builds separate Bodies, then combines them with Assemble / Add / Remove /
Intersect / Union Trim (ShapeFactory.AddNewAssemble/AddNewAdd/AddNewRemove/AddNewIntersect/
AddNewUnionTrim).

Live-verified 2026-09-28: ShapeFactory.AddNewAssemble(ToolBody) takes ONE
argument and operates into Part.InWorkObject (the first version passed two
bodies and never set the in-work object). Remove Lump ("Retrait de
volumes") is deliberately NOT implemented here: its exact Automation
signature couldn't be confirmed without a live CATIA session, and shipping
a guessed method name that silently does the wrong thing would be worse
than not having the tool - if needed, do it in the GUI (right-click body >
Retrait de volumes) until this can be verified against a running CATIA.
"""

from __future__ import annotations

from typing import Any

from catia_mcp import geometry
from catia_mcp.connection import CATIAConnection

_OPERATIONS = {
    "assemble": "AddNewAssemble",
    "add": "AddNewAdd",
    "remove": "AddNewRemove",
    "intersect": "AddNewIntersect",
    "union_trim": "AddNewUnionTrim",
}


class BooleanTools:
    """Tools for combining Bodies with boolean operations."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_boolean_operation",
                "description": (
                    "Combine two Bodies with a boolean operation (right-click a body "
                    "> x.Objet > ... in the GUI). This is how the multi-corps method "
                    "produces the final solid: model each "
                    "functional zone in its own Body, then combine them here.\n"
                    "- assemble: union that respects each body's feature nature (a "
                    "Pocket in the tool body removes material — most common case).\n"
                    "- add: union that always adds material, even if the tool body's "
                    "first feature is a Pocket (it's reinterpreted as a Boss).\n"
                    "- remove: target_body minus tool_body.\n"
                    "- intersect: keeps only material common to both bodies.\n"
                    "- union_trim: union with control over which side to keep/remove "
                    "(faces_to_remove_points / faces_to_keep_points: one [x, y, z] "
                    "inside each face of the tool body; see catia_list_faces)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["assemble", "add", "remove", "intersect", "union_trim"],
                        },
                        "tool_body": {
                            "type": "string",
                            "description": "Name of the body being combined INTO the target (e.g. 'Body.2').",
                        },
                        "target_body": {
                            "type": "string",
                            "description": "Name of the body receiving the operation. Defaults to the currently active body (InWorkObject) if omitted.",
                        },
                        "faces_to_remove_points": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "description": "union_trim only: a point inside each tool-body face to discard.",
                        },
                        "faces_to_keep_points": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "description": "union_trim only: a point inside each tool-body face to keep.",
                        },
                    },
                    "required": ["operation", "tool_body"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name != "catia_boolean_operation":
            raise ValueError(f"Unknown boolean tool: {tool_name}")
        return self._boolean_operation(arguments)

    def _get_body(self, part: Any, name: str) -> Any:
        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            if bodies.Item(i).Name == name:
                return bodies.Item(i)
        raise RuntimeError(f"Body '{name}' not found. Use catia_list_bodies to see available bodies.")

    def _boolean_operation(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        sf = part.ShapeFactory

        operation = args["operation"]
        method_name = _OPERATIONS[operation]

        tool_body = self._get_body(part, args["tool_body"])
        target_name = args.get("target_body")
        target_body = self._get_body(part, target_name) if target_name else self.conn.get_active_part_body()

        if tool_body.Name == target_body.Name:
            raise ValueError("tool_body and target_body are the same body.")

        # Proven live: AddNewAssemble/Add/Remove/... take ONE argument (the
        # body to combine) and operate INTO the current in-work body. The old
        # code passed (target, tool) and never set the in-work object.
        part.InWorkObject = target_body
        feature = getattr(sf, method_name)(tool_body)

        if operation == "union_trim":
            app, doc = self.conn.app, self.conn.active_document
            for pt in args.get("faces_to_remove_points", []):
                feature.AddFaceToRemove(geometry.pick(app, doc, tool_body, "face", pt)[0])
            for pt in args.get("faces_to_keep_points", []):
                feature.AddFaceToKeep(geometry.pick(app, doc, tool_body, "face", pt)[0])

        try:
            part.Update()
        except Exception as e:
            try:
                sel = self.conn.hso
                sel.Clear(); sel.Add(feature); sel.Delete(); sel.Clear()
            except Exception:
                pass
            raise RuntimeError(f"CATIA refused the {operation}: {e}. The failed feature was removed.") from e

        # Like the GUI: work continues in the target body.
        self.conn.active_body_name = target_body.Name
        self.conn.refresh_display()
        return (
            f"{operation} applied: '{tool_body.Name}' into '{target_body.Name}' "
            f"-> feature '{feature.Name}'"
        )
