"""Measurement and analysis tools for CATIA V5.

Distance, angle, inertia, bounding box, and part property queries.
"""

from __future__ import annotations

import json
from typing import Any

from catia_mcp import geometry
from catia_mcp.connection import CATIAConnection


class MeasurementTools:
    """Tools for measurement and analysis in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_measure_distance",
                "description": (
                    "Measure the minimum distance between two geometry elements. "
                    "Returns distance in mm."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element1": {
                            "type": "string",
                            "description": "Name of first element (feature, face, edge, point)",
                        },
                        "element2": {
                            "type": "string",
                            "description": "Name of second element",
                        },
                    },
                    "required": ["element1", "element2"],
                },
            },
            {
                "name": "catia_get_inertia",
                "description": (
                    "Get inertia properties of the active part: volume, surface area, "
                    "center of gravity, mass (if density is defined), moments of inertia."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "density": {
                            "type": "number",
                            "description": "Material density in kg/m3 (optional, for mass calculation)",
                        },
                        "body_name": {"type": "string", "description": "Body to measure (default: active body)."},
                    },
                },
            },
            {
                "name": "catia_get_bounding_box",
                "description": (
                    "Exact bounding box (mm) of a body's solid, curved shapes included: "
                    "x/y/z [min, max] and size. Use it to check a piece against its drawing."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body_name": {"type": "string", "description": "Body to measure (default: active body)."},
                    },
                },
            },
            {
                "name": "catia_get_parameters",
                "description": (
                    "List all user-defined and computed parameters of the active part. "
                    "Includes dimensions, formulas, and design tables."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "Optional name filter (partial match)",
                        },
                    },
                },
            },
            {
                "name": "catia_set_parameter",
                "description": (
                    "Set the value of a named parameter in the active part. "
                    "Useful for parametric design modifications."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Full parameter name (e.g., 'Part1\\\\Pad.1\\\\FirstLimit\\\\Length')",
                        },
                        "value": {
                            "type": "number",
                            "description": "New value for the parameter",
                        },
                    },
                    "required": ["name", "value"],
                },
            },
            {
                "name": "catia_update_part",
                "description": "Force update/rebuild of the active part. Recalculates all features.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_measure_distance":
                return self._measure_distance(arguments["element1"], arguments["element2"])
            case "catia_get_inertia":
                return self._get_inertia(arguments.get("density"), arguments.get("body_name"))
            case "catia_get_bounding_box":
                return self._get_bounding_box(arguments.get("body_name"))
            case "catia_get_parameters":
                return self._get_parameters(arguments.get("filter"))
            case "catia_set_parameter":
                return self._set_parameter(arguments["name"], arguments["value"])
            case "catia_update_part":
                return self._update_part()
            case _:
                raise ValueError(f"Unknown measurement tool: {tool_name}")

    def _measure_distance(self, elem1_name: str, elem2_name: str) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        spa = self.conn.active_document.GetWorkbench("SPAWorkbench")

        # Create references from names
        sel = self.conn.hso
        sel.Clear()

        # Search for the elements
        sel.Search(f"Name={elem1_name},all")
        if sel.Count == 0:
            raise RuntimeError(f"Element '{elem1_name}' not found")
        ref1 = part.CreateReferenceFromObject(sel.Item(1).Value)

        sel.Clear()
        sel.Search(f"Name={elem2_name},all")
        if sel.Count == 0:
            raise RuntimeError(f"Element '{elem2_name}' not found")
        ref2 = part.CreateReferenceFromObject(sel.Item(1).Value)
        sel.Clear()

        # Measure
        measurable = spa.GetMeasurable(ref1)
        distance = measurable.GetMinimumDistance(ref2)

        return f"Minimum distance between '{elem1_name}' and '{elem2_name}': {distance:.4f} mm"

    def _body_ref(self, body_name: str | None) -> tuple[Any, Any]:
        part = self.conn.get_active_part()
        body = part.Bodies.Item(body_name) if body_name else self.conn.get_active_part_body()
        return part, body

    def _get_inertia(self, density: float | None = None, body_name: str | None = None) -> str:
        # Measurable.GetCOG/GetInertia fill ByRef arrays, which pywin32 never
        # writes back (always zeros) — measured through VBScript instead, see
        # geometry.solid_info and CATIA_COM_PITFALLS.md §5.
        self.conn.ensure_connected()
        part, body = self._body_ref(body_name)
        info = geometry.mass_info(self.conn.app, part, body)
        result: dict[str, Any] = {
            "body": body.Name,
            "volume_mm3": info["volume_mm3"],
            "volume_cm3": round(info["volume_mm3"] / 1000, 4),
            "area_mm2": info["area_mm2"],
            "center_of_gravity_mm": info["cog"],
        }
        if density:
            mass_kg = density * info["volume_mm3"] * 1e-9
            result.update({"density_kg_m3": density, "mass_kg": round(mass_kg, 6)})
        return json.dumps(result, indent=2)

    def _get_bounding_box(self, body_name: str | None = None) -> str:
        self.conn.ensure_connected()
        part, body = self._body_ref(body_name)
        return json.dumps({"body": body.Name, **geometry.bounding_box(self.conn.app, body)}, indent=2)

    def _get_parameters(self, name_filter: str | None = None) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        params = part.Parameters

        result = []
        for i in range(1, params.Count + 1):
            param = params.Item(i)
            name = param.Name

            if name_filter and name_filter.lower() not in name.lower():
                continue

            info: dict[str, Any] = {"name": name}
            try:
                info["value"] = param.Value
            except Exception:
                info["value"] = "N/A"
            try:
                info["comment"] = param.Comment
            except Exception:
                pass

            result.append(info)

        if not result:
            return "No parameters found" + (f" matching '{name_filter}'" if name_filter else "")
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _set_parameter(self, name: str, value: float) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        params = part.Parameters

        param = params.Item(name)
        old_value = param.Value
        param.Value = value
        part.Update()

        self.conn.refresh_display()
        return f"Parameter '{name}' changed: {old_value} -> {value}"

    def _update_part(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        part.Update()
        self.conn.refresh_display()
        return "Part updated successfully"
