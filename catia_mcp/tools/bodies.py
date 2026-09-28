"""Body (multi-corps) management tools for CATIA V5.

The multi-body method (Insert > Body, rename each body, Define In Work Object, then combine
with boolean operations) needs bodies to be first-class: catia_pad/catia_pocket/etc. used to
always land in part.MainBody (see connection.get_active_part_body). These tools create, rename,
activate, list and show/hide bodies.
"""

from __future__ import annotations

import json
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.naming import is_default_name


def _operand_body(shape: Any) -> Any | None:
    """The body absorbed by a boolean feature (Assemble/Add/Remove...), if any.

    After an Assemble the operand body disappears from Part.Bodies and only
    lives under the boolean feature (proven live), so the tree walk must go
    through BooleanShape.Body to see it.
    """
    try:
        body = shape.Body
        _ = body.Shapes
        return body
    except Exception:
        return None


class BodyTools:
    """Tools for creating, naming, and activating Part Design Bodies."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_new_body",
                "description": (
                    "Insert a new Body in the active Part (Insertion > Corps in the "
                    "French UI). This is the entry point of the multi-corps method: "
                    "create one Body per functional zone of the piece, then combine "
                    "them later with catia_boolean_operation. The new body is NOT "
                    "made active automatically; call catia_activate_body next."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Descriptive name for the body, e.g. 'Rough', 'Machined', "
                                "'Bague_ext'. If omitted, keeps CATIA's default 'Body.N'."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_rename_body",
                "description": "Rename a Body (in Part.Bodies) via the .Name COM property.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "old_name": {
                            "type": "string",
                            "description": "Current body name, e.g. 'Body.2'. If omitted, renames the last body created.",
                        },
                        "new_name": {"type": "string", "description": "New descriptive name."},
                    },
                    "required": ["new_name"],
                },
            },
            {
                "name": "catia_activate_body",
                "description": (
                    "Set a Body as the active 'in work object' (Part.InWorkObject) — "
                    "the 'Define In Work Object' right-click action from the CATIA GUI. "
                    "After calling this, catia_create_sketch/catia_pad/catia_pocket/etc. "
                    "create their features inside this body instead of MainBody. Call "
                    "this right after catia_new_body to start working in it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body_name": {"type": "string", "description": "Name of the body to activate, e.g. 'Body.2' or 'Rough'."},
                    },
                    "required": ["body_name"],
                },
            },
            {
                "name": "catia_list_bodies",
                "description": "List all Bodies in the active Part, their feature counts, and which one is currently active (InWorkObject).",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "catia_get_tree",
                "description": (
                    "Specification tree of the active Part, as a reviewer sees it: every "
                    "body (including those absorbed by Assemble/Add/Remove...), its "
                    "sketches, features and planes, plus geometrical sets. Includes a "
                    "naming AUDIT listing every element still carrying a default name "
                    "(anything ending in '.N' like 'Extrusion.3'/'Pad.1'/'Esquisse.2', "
                    "or 'Corps principal'/'PartBody'). Run it after each piece and fix "
                    "every flagged name before saving."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "catia_hide_show_body",
                "description": "Show or hide a Body in the 3D view (does not delete it).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body_name": {"type": "string"},
                        "visible": {"type": "boolean", "description": "true = show, false = hide"},
                    },
                    "required": ["body_name", "visible"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_new_body":
                return self._new_body(arguments)
            case "catia_rename_body":
                return self._rename_body(arguments)
            case "catia_activate_body":
                return self._activate_body(arguments)
            case "catia_list_bodies":
                return self._list_bodies()
            case "catia_hide_show_body":
                return self._hide_show_body(arguments)
            case "catia_get_tree":
                return self._get_tree()
            case _:
                raise ValueError(f"Unknown body tool: {tool_name}")

    def _get_body(self, name: str) -> Any:
        part = self.conn.get_active_part()
        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            if bodies.Item(i).Name == name:
                return bodies.Item(i)
        raise RuntimeError(f"Body '{name}' not found. Use catia_list_bodies to see available bodies.")

    def _new_body(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = part.Bodies.Add()
        part.UpdateObject(body)

        name = args.get("name")
        if name:
            body.Name = name

        self.conn.refresh_display()
        return f"New body created: '{body.Name}' (not yet active — call catia_activate_body to work inside it)"

    def _rename_body(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        old_name = args.get("old_name")
        new_name = args["new_name"]

        bodies = part.Bodies
        if old_name:
            body = self._get_body(old_name)
        else:
            if bodies.Count == 0:
                raise RuntimeError("No bodies in the active part.")
            body = bodies.Item(bodies.Count)

        previous = body.Name
        body.Name = new_name
        if body.Name != new_name:
            raise RuntimeError(f"Rename did not take effect: body still named '{body.Name}'.")

        # Keep the MCP's own "which body is active" cache in sync — see
        # connection.get_active_part_body() for why this cache exists.
        if self.conn.active_body_name == previous:
            self.conn.active_body_name = new_name

        return f"Renamed body '{previous}' -> '{new_name}'"

    def _activate_body(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._get_body(args["body_name"])
        part.InWorkObject = body
        part.Update()

        # CATIA moves InWorkObject to the next FEATURE created inside this
        # body (not the body itself) — cache the name here so
        # get_active_part_body() keeps targeting this body for every
        # subsequent sketch/feature, not just the first one. Confirmed live
        # 2026-09-28: without this, a second feature after activation
        # silently landed back in the main body.
        self.conn.active_body_name = body.Name

        self.conn.refresh_display()
        return f"Body '{body.Name}' is now the active in-work object."

    def _list_bodies(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        bodies = part.Bodies

        # Prefer the MCP's cached active body (see connection.py) since
        # Part.InWorkObject.Name points at the last FEATURE, not the body,
        # right after any feature is created in it.
        active_name = self.conn.active_body_name
        if active_name is None:
            try:
                active_name = part.InWorkObject.Name
            except Exception:
                active_name = None

        out = []
        for i in range(1, bodies.Count + 1):
            body = bodies.Item(i)
            out.append(
                {
                    "index": i,
                    "name": body.Name,
                    "shape_count": body.Shapes.Count,
                    "sketch_count": body.Sketches.Count,
                    "active": body.Name == active_name,
                }
            )
        if not out:
            return "No bodies in the active part."
        return json.dumps(out, indent=2)

    def _get_tree(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        flagged: list[str] = []
        lines = [f"{part.Name}"]

        def note(path: str, name: str) -> str:
            if is_default_name(name):
                flagged.append(f"{path}/{name}" if path else name)
                return "   <-- DEFAULT NAME"
            return ""

        def own_sketch_names(body: Any) -> set[str]:
            """Body.Sketches also returns the sketches of absorbed operand
            bodies (proven live); keep only the body's own."""
            names = {body.Sketches.Item(i).Name for i in range(1, body.Sketches.Count + 1)}
            for i in range(1, body.Shapes.Count + 1):
                operand = _operand_body(body.Shapes.Item(i))
                if operand is not None:
                    names -= {operand.Sketches.Item(j).Name for j in range(1, operand.Sketches.Count + 1)}
            return names

        def walk_body(body: Any, indent: str, path: str) -> None:
            lines.append(f"{indent}[Corps] {body.Name}{note(path, body.Name)}")
            here = f"{path}/{body.Name}" if path else body.Name
            sub = indent + "    "
            shapes = body.Shapes
            shape_names = {shapes.Item(i).Name for i in range(1, shapes.Count + 1)}
            try:
                hs = body.HybridShapes
                for i in range(1, hs.Count + 1):
                    n = hs.Item(i).Name
                    if n not in shape_names:  # HybridShapes also lists some solid features
                        lines.append(f"{sub}[Géométrie] {n}{note(here, n)}")
            except Exception:
                pass
            own = own_sketch_names(body)
            sk = body.Sketches
            for i in range(1, sk.Count + 1):
                n = sk.Item(i).Name
                if n in own:
                    lines.append(f"{sub}[Esquisse] {n}{note(here, n)}")
            for i in range(1, shapes.Count + 1):
                shape = shapes.Item(i)
                lines.append(f"{sub}[Feature] {shape.Name}{note(here, shape.Name)}")
                operand = _operand_body(shape)
                if operand is not None:
                    walk_body(operand, sub + "    ", f"{here}/{shape.Name}")

        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            walk_body(bodies.Item(i), "    ", "")
        hbodies = part.HybridBodies
        for i in range(1, hbodies.Count + 1):
            hb = hbodies.Item(i)
            lines.append(f"    [Set géométrique] {hb.Name}{note('', hb.Name)}")
            for j in range(1, hb.HybridShapes.Count + 1):
                n = hb.HybridShapes.Item(j).Name
                lines.append(f"        {n}{note(hb.Name, n)}")

        if flagged:
            lines.append(f"\nAUDIT: {len(flagged)} default name(s) to fix (catia_rename_feature / catia_rename_body):")
            lines += [f"  - {f}" for f in flagged]
        else:
            lines.append("\nAUDIT: OK — no default name left.")
        return "\n".join(lines)

    def _hide_show_body(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        body = self._get_body(args["body_name"])
        sel = self.conn.hso
        sel.Clear()
        sel.Add(body)
        # CatVisPropertyShowType: 0 = show, 1 = no show
        show_value = 0 if args["visible"] else 1
        sel.VisProperties.SetShow(show_value)
        sel.Clear()
        self.conn.refresh_display()
        state = "shown" if args["visible"] else "hidden"
        return f"Body '{body.Name}' is now {state}."
