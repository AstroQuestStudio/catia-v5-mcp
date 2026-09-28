"""Optional vector-PDF drawing reader for CATIA workflows.

Reads exact geometry (circles, arcs, lines, scale) from vector engineering drawings,
renders views and overlays a CATIA sketch on the drawing for visual checking.

PyMuPDF (AGPL-3.0) is required at call time only and is imported lazily:
``pip install "catia-v5-mcp-server[drawing]"``. Importing this package never imports it.
"""

from catia_mcp.drawing.geometry import DrawingDependencyError, extract, is_available
from catia_mcp.drawing.render import overlay, render_view
from catia_mcp.drawing.report import format_report

__all__ = ["extract", "render_view", "overlay", "format_report", "is_available", "DrawingDependencyError"]
