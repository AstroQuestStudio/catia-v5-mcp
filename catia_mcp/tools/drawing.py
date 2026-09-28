"""Drawing tools: read a vector PDF engineering drawing and check sketches against it.

These tools do NOT need CATIA. They need the optional PyMuPDF extra (AGPL-3.0), imported
lazily on first use: ``pip install "catia-v5-mcp-server[drawing]"``. Without it every tool
returns a clear install message instead of failing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pywin32 is not needed by these tools
    from catia_mcp.connection import CATIAConnection

_INSTALL_MSG = (
    'Drawing tools need the optional PyMuPDF package (AGPL-3.0). '
    'Install it with: pip install "catia-v5-mcp-server[drawing]"'
)

_PDF = {"type": "string", "description": "Path to a VECTOR PDF drawing (not a scan)."}
_PAGE = {"type": "integer", "default": 0, "description": "Page index, 0-based."}
_REGION = {
    "type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
    "description": "[x0, y0, x1, y1] in PAPER mm from the sheet's top-left corner (y down). "
                   "Omit for the whole sheet.",
}
_SCALE = {
    "type": "string",
    "description": "Drawing scale '1:2' (1 mm on paper = 2 mm on the part). If omitted the title "
                   "block text is used when readable, else 1:1. The title block scale is "
                   "sometimes WRONG: cross-check with a known dimension.",
}
_ORIGIN = {
    "type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
    "description": "[x, y] PAPER mm point that becomes the model origin (0, 0), typically an "
                   "axis/centre found in a first call without origin. Model X is right, Y up.",
}


class DrawingTools:
    """Tools for reading vector PDF drawings (independent of the CATIA connection)."""

    def __init__(self, connection: CATIAConnection | None) -> None:
        self.conn = connection  # unused: these tools work without CATIA

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "drawing_extract_geometry",
                "description": (
                    "Extract EXACT geometry from a vector PDF drawing: numbered circles, arcs "
                    "and line segments with centre, radius R and diameter D in part mm. "
                    "Dimension values are drawn as vector strokes, not text: read them by eye "
                    "on drawing_render, then CONFIRM with this measured geometry (they must "
                    "agree; when they do not, the measurement wins). Workflow: (1) "
                    "drawing_render the whole page to find the views and title block scale; "
                    "(2) call this on one view's region WITHOUT origin to find the centre/axis "
                    "(each entity lists its paper position); (3) call again with origin and "
                    "scale to get part coordinates to feed the sketch tools. Beware: R vs D, "
                    "rounded dimensions (a drawn tangent may differ from the written value by "
                    "a few hundredths), wrong title block scale."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "pdf_path": _PDF,
                        "page": _PAGE,
                        "region": _REGION,
                        "scale": _SCALE,
                        "origin": _ORIGIN,
                        "min_len": {"type": "number", "default": 1.0,
                                    "description": "Ignore segments shorter than this (paper mm)."},
                        "min_radius": {"type": "number", "default": 0.6,
                                       "description": "Ignore arcs/circles smaller than this (paper mm)."},
                        "max_items": {"type": "integer", "default": 60,
                                      "description": "Max entities listed per category."},
                    },
                    "required": ["pdf_path"],
                },
            },
            {
                "name": "drawing_render",
                "description": (
                    "Render a page or region of a PDF drawing to a PNG and return its path "
                    "(view it with an image reader). Use it to locate views and to READ the "
                    "dimension values by eye: they are vectorised, so text search cannot find "
                    "them. Zoom with region + higher dpi (300-400) for small annotations. With "
                    "grid (part mm) and origin, a graduated grid in PART millimetres is "
                    "drawn to help read positions."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "pdf_path": _PDF,
                        "page": _PAGE,
                        "region": _REGION,
                        "dpi": {"type": "integer", "default": 150, "description": "10-1200."},
                        "out_png": {"type": "string",
                                    "description": "Output PNG path (default: temp folder)."},
                        "scale": _SCALE,
                        "origin": _ORIGIN,
                        "grid": {"type": "number",
                                 "description": "Grid step in part mm (needs origin)."},
                    },
                    "required": ["pdf_path"],
                },
            },
            {
                "name": "drawing_overlay",
                "description": (
                    "Draw a CATIA sketch (in part mm) on top of the PDF drawing and return the "
                    "PNG path: the coloured sketch must follow the black drawing lines "
                    "everywhere. Do this for every non-trivial profile BEFORE pad/shaft, then "
                    "LOOK at the image. `sketch` takes the same arguments as the sketch tools: "
                    "a catia_sketch_profile object {start, segments}, an entity "
                    "{type: line|circle|arc|rectangle, ...}, or a list of them. `hv` maps the "
                    "sketch (h, v) axes to the view (X right, Y up): 'h,v', '-h,v', 'v,h', ..."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "pdf_path": _PDF,
                        "page": _PAGE,
                        "sketch": {
                            "type": ["object", "array"],
                            "description": "Sketch entities in mm (see description). Or use sketch_file.",
                        },
                        "sketch_file": {"type": "string", "description": "Path to a JSON file holding the sketch."},
                        "region": _REGION,
                        "scale": _SCALE,
                        "origin": _ORIGIN,
                        "hv": {"type": "string", "default": "h,v"},
                        "dpi": {"type": "integer", "default": 150},
                        "out_png": {"type": "string", "description": "Output PNG path (default: temp folder)."},
                    },
                    "required": ["pdf_path"],
                },
            },
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        from catia_mcp.drawing.geometry import DrawingDependencyError

        try:
            if name == "drawing_extract_geometry":
                return self._extract(arguments)
            if name == "drawing_render":
                return self._render(arguments)
            if name == "drawing_overlay":
                return self._overlay(arguments)
        except DrawingDependencyError:
            return _INSTALL_MSG
        raise ValueError(f"Unknown drawing tool: {name}")

    # -- implementations (imports are lazy so this module always imports) --
    def _extract(self, a: dict[str, Any]) -> str:
        from catia_mcp.drawing import geometry
        from catia_mcp.drawing.report import format_report

        res = geometry.extract(
            a["pdf_path"], int(a.get("page", 0)), a.get("region"), a.get("scale"),
            a.get("origin"), float(a.get("min_len", 1.0)), float(a.get("min_radius", 0.6)),
        )
        return format_report(res, int(a.get("max_items", 60)))

    def _render(self, a: dict[str, Any]) -> str:
        from catia_mcp.drawing import render

        path = render.render_view(
            a["pdf_path"], int(a.get("page", 0)), a.get("region"), a.get("out_png"),
            int(a.get("dpi", 150)), a.get("scale"), a.get("origin"), a.get("grid"),
        )
        return f"Rendered: {path}"

    def _overlay(self, a: dict[str, Any]) -> str:
        from catia_mcp.drawing import render

        sketch = a.get("sketch")
        if sketch is None:
            sketch = a.get("sketch_file")
        if sketch is None:
            raise ValueError("drawing_overlay needs `sketch` or `sketch_file`")
        path = render.overlay(
            a["pdf_path"], int(a.get("page", 0)), sketch, a.get("out_png"), a.get("region"),
            a.get("scale"), a.get("origin"), int(a.get("dpi", 150)), a.get("hv", "h,v"),
        )
        return (f"Overlay: {path}\nLook at it: each coloured polyline must follow the black "
                "drawing line everywhere (a gap = wrong dimension, origin, scale or hv).")
