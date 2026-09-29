"""Drafting tools for CATIA V5: 3D model -> ISO CATDrawing -> PDF -> closed-loop check.

Every tool below was proven live on CATIA V5 R19 (see ``docs/DRAFTING.md`` for the experiments and
the pitfalls). What could not be proven is not registered here. The API facts this module relies on:

* ``Documents.Add("Drawing")`` gives a drawing whose sheet has NO readable format until
  ``PaperSize`` (``CatPaperSize``: A0=2 ... A4=6) and ``Orientation`` (0 portrait, 1 landscape)
  are set; ``ProjectionMethod`` 0 = first angle, 1 = third angle; ``Sheet.Scale`` is paper/model.
* A generative view needs ``view.GenerativeBehavior.Document = <CATPart|CATProduct>.Product`` on EVERY
  view (front AND projected/section/detail). Without it the view is silently empty.
* ``DefineFrontView(hx,hy,hz, vx,vy,vz)`` takes the view's horizontal and vertical axes; the other
  ``Define*View`` take the parent's ``GenerativeBehavior``. New views are at (0, 0) and scale 1: the
  sheet scale is NOT inherited, set ``View.Scale``, ``View.x``, ``View.y`` yourself.
* ``View.x/.y`` is the centre of the projected bounding box, ``xAxisData/yAxisData`` the sheet position
  of the projected model origin. ``View.Size`` only works through a VBScript (byref array).
* ``Factory2D`` creation needs the view ``Activate()``d first. Dimensions of generated geometry cannot
  be referenced from Automation: they are attached to helper 2D geometry (hidden) placed exactly on
  the model geometry; ``MoveValue(x, y, 0, 0)`` sets the dimension line position.
* ``Sheet.GenerateDimensions()`` creates the dimensions driven by 3D constraints only.
* ``DrawingDocument.ExportData(path, "pdf")`` writes a vector PDF whose page equals the sheet.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from fractions import Fraction
from typing import Any

from catia_mcp import standards as std
from catia_mcp.connection import CATIAConnection
from catia_mcp.drawing import verify as V

# ---------------------------------------------------------------------------- constants (all read from the type library)

PAPER_CODE = {"A0": 2, "A1": 3, "A2": 4, "A3": 5, "A4": 6}          # CatPaperSize
ORIENTATION_CODE = {"portrait": 0, "landscape": 1}                  # CatPaperOrientation
PROJECTION_CODE = {"first_angle": 0, "third_angle": 1}              # CatSheetProjectionMethod
PROJ_VIEW_CODE = {"right": 0, "left": 1, "top": 2, "bottom": 3, "rear": 4}   # CatProjViewType
DIM_TYPE = {"distance": 0, "diameter": 9, "radius": 5}              # CatDimType (subset used)
DIM_TYPE_NAMES = {0: "distance", 1: "distance_offset", 2: "length", 3: "length_curvilinear", 4: "angle",
                  5: "radius", 6: "radius_tangent", 7: "radius_cylinder", 8: "radius_edge", 9: "diameter",
                  10: "diameter_tangent", 11: "diameter_cylinder", 12: "diameter_edge", 13: "diameter_cone",
                  14: "chamfer", 15: "slope", 16: "length_circular", 17: "radius_fillet", 18: "diameter_torus",
                  19: "radius_torus", 20: "distance_min"}
DIM_LINE_REP = {"horizontal": 1, "vertical": 2, "aligned": 6, "auto": 3}    # CatDimLineRep
VIEW_TYPE_NAMES = {0: "background", 1: "front", 2: "left", 3: "right", 4: "top", 5: "bottom", 6: "rear",
                   7: "auxiliary", 8: "isometric", 9: "section", 10: "section_cut", 11: "detail",
                   12: "untyped", 13: "main", 14: "pure_sketch", 15: "unfolded"}     # CatDrawingViewType
# ``SetRealWidth`` index -> mm measured in the exported PDF of this CATIA (installed standard)
WIDTH_INDEX = {"thin": 1, "medium": 2, "wide": 3}                    # 0.13 / 0.35 / 0.70 mm
# ``SetRealLineType`` index measured in the PDF: 1 continuous, 3 dashed, 4 chain (dash-dot)
LINE_TYPE_INDEX = {"continuous": 1, "dashed": 3, "chain": 4}

DEFAULT_TITLE_BLOCK_HEIGHT_MM = 62.0   # 7 rows x 7 mm + projection symbol + clearance
TITLE_ROW_HEIGHT_MM = 7.0
TITLE_COLS_MM = (44.0, 46.0, 44.0, 46.0)          # label, value, label, value = 180 mm (ISO 7200:2004 clause 6)
TITLE_FONT_MM = 2.5                              # text height in the title block cells (ISO 3098: >= 2.5 mm)
MANDATORY_TITLE_FIELDS = tuple(f.key for f in std.TITLE_BLOCK_FIELDS if f.mandatory)
VIEW_KINDS_ALL = ("front", "top", "bottom", "left", "right", "rear", "isometric", "section", "detail")

# Size of a view read through a VBScript (Size fills a byref array; Python only sees zeros)
_VBS_SIZE = """
Function CATMain(v)
  Dim a(3)
  v.Size a
  CATMain = Array(a(0), a(1), a(2), a(3))
End Function
"""
_VBS_DIMLINE = """
Function CATMain(dm)
  Dim a(9)
  dm.GetDimLine().GetGeomInfo a
  CATMain = Array(a(0), a(1), a(2), a(3))
End Function
"""


class DraftingError(RuntimeError):
    """A drafting tool refused or failed; the message says what to do."""


def _r(x: float, n: int = 4) -> float:
    return round(float(x), n)


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False)


# ---------------------------------------------------------------------------- pure logic (offline-testable)

def scale_label(ratio: float) -> str:
    """'1:2' for CATIA's paper/model ratio 0.5; the nearest simple fraction, never a long decimal."""
    f = Fraction(ratio).limit_denominator(10000)
    return f"{f.numerator}:{f.denominator}"


def parse_point2(value: Any, name: str) -> tuple[float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value)):
        raise DraftingError(f"{name} must be [u, w] in millimetres (two numbers), got {value!r}")
    return float(value[0]), float(value[1])


def validate_sheet_request(sheet: str, scale: Any, projection: str, orientation: str | None) -> list[str]:
    """Return the warnings; raise DraftingError for an invalid request (ISO 5457 / 5455 / 5456-2)."""
    warnings: list[str] = []
    if str(sheet).lower() != "auto":
        try:
            sh = std.get_sheet(sheet)
        except ValueError as e:
            raise DraftingError(str(e)) from None
        if orientation and orientation not in ORIENTATION_CODE:
            raise DraftingError("orientation must be 'landscape' or 'portrait'")
        if orientation and orientation != sh.orientation:
            warnings.append(f"ISO 5457:1999 4.1 prescribes {sh.orientation} for {sh.name}")
    if str(scale).lower() != "auto":
        try:
            sc = std.parse_scale(scale)
        except ValueError as e:
            raise DraftingError(f"scale: {e}. Use a ratio such as '1:2' or '5:1'") from None
        if not std.is_preferred_scale(sc):
            ext = std.is_preferred_scale(sc, extended=True)
            warnings.append(f"scale {sc.label} is not in the ISO 5455:1979 recommended series"
                            + (" (power-of-ten extension)" if ext else ""))
    if projection not in std.PROJECTION_METHODS:
        raise DraftingError(f"projection must be one of {', '.join(std.PROJECTION_METHODS)}")
    return warnings


def title_block_rows(fields: dict[str, Any], *, general_tolerance: str = "m", scale: str = "1:1",
                     projection: str = "first_angle", paper_size: str = "A3") -> list[list[str]]:
    """Cell texts of the title block: rows of four cells [label, value, label, value].

    A row whose 3rd/4th cells are empty and marked by a leading ``"*"`` in the first cell is a full-width
    row (the value cell spans columns 2-4). Pure function: unit-tested offline.
    """
    def val(key: str) -> str:
        v = fields.get(key)
        return "" if v is None else str(v)

    names = {f.key: f.name for f in std.TITLE_BLOCK_FIELDS}
    sheet_txt = val("sheet_number")
    if fields.get("number_of_sheets") not in (None, ""):
        sheet_txt = f"{sheet_txt}/{fields['number_of_sheets']}"
    proj = "1st angle" if projection == "first_angle" else "3rd angle"
    rows: list[list[str]] = [["*" + names["title"], val("title"), "", ""]]
    if val("supplementary_title"):
        rows.append(["*" + names["supplementary_title"], val("supplementary_title"), "", ""])
    rows += [
        [names["legal_owner"], val("legal_owner"), names["document_type"], val("document_type")],
        [names["creator"], val("creator"), names["approval_person"], val("approval_person")],
        [names["identification_number"], val("identification_number"), names["revision_index"], val("revision_index")],
        [names["date_of_issue"], val("date_of_issue"), "Sheet", sheet_txt],
        ["Scale", scale, "Projection", proj],
        ["General tolerances", std.general_tolerance_designation(general_tolerance), names["paper_size"],
         val("paper_size") or paper_size],
    ]
    extra = [k for k in ("responsible_department", "technical_reference", "document_status", "classification")
             if val(k)]
    for i in range(0, len(extra), 2):
        a = extra[i]
        b = extra[i + 1] if i + 1 < len(extra) else None
        rows.append([names[a], val(a), names[b] if b else "", val(b) if b else ""])
    return rows


def projection_symbol_geometry(projection: str, x: float, y: float, unit: float = 3.5) -> dict[str, Any]:
    """Truncated-cone pictogram of ISO 5456-2:1996 (sheet mm, lower-left ``x``, ``y``, height 2 units).

    First angle: cone elevation on the left, end view (two concentric circles) on the right; third angle
    is the mirror image. Proportions: large diameter 2u, small diameter u, length 3u (u = 3.5 mm).
    """
    if projection not in std.PROJECTION_METHODS:
        raise DraftingError(f"projection must be one of {', '.join(std.PROJECTION_METHODS)}")
    u = unit
    cy = y + u
    x0, x1 = x, x + 3 * u                                   # first angle: elevation (large end left)
    lines = [((x0, cy - u), (x1, cy - u / 2)), ((x1, cy - u / 2), (x1, cy + u / 2)),
             ((x1, cy + u / 2), (x0, cy + u)), ((x0, cy + u), (x0, cy - u))]
    axis = ((x0 - 0.5 * u, cy), (x1 + 0.5 * u, cy))
    x_circ = x + 5.5 * u
    if projection == "third_angle":                         # mirror image about the middle of the cone
        xc = x + 3 * u

        def mx(px: float) -> float:
            return 2 * xc - px

        lines = [((mx(p[0]), p[1]), (mx(q[0]), q[1])) for p, q in lines]
        axis = ((mx(axis[0][0]), cy), (mx(axis[1][0]), cy))
        x_circ = mx(x_circ)
        x0, x1 = mx(x1), mx(x0)
    circles = [(x_circ, cy, u), (x_circ, cy, u / 2)]      # (cx, cy, radius)
    return {"lines": lines, "axis": axis, "circles": circles, "width": 7 * u, "height": 2 * u,
            "cone_x": (x0, x1), "circle_x": x_circ}


def dimension_line_position(kind: str, p1: tuple[float, float], p2: tuple[float, float], offset_model: float,
                            side: str | None) -> tuple[float, float]:
    """Where ``MoveValue`` must put a linear dimension so its line sits ``offset_model`` from the points."""
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    if kind == "horizontal":
        below = (side or "below") != "above"
        return mx, (min(p1[1], p2[1]) - offset_model) if below else (max(p1[1], p2[1]) + offset_model)
    if kind == "vertical":
        right = (side or "right") != "left"
        return (max(p1[0], p2[0]) + offset_model) if right else (min(p1[0], p2[0]) - offset_model), my
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        raise DraftingError("'from' and 'to' are the same point")
    nx, ny = -dy / n, dx / n                      # left of the direction from -> to
    sign = -1.0 if (side or "left") == "right" else 1.0
    return mx + sign * nx * offset_model, my + sign * ny * offset_model


def linear_value(kind: str, p1: tuple[float, float], p2: tuple[float, float]) -> float:
    if kind == "horizontal":
        return abs(p2[0] - p1[0])
    if kind == "vertical":
        return abs(p2[1] - p1[1])
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float = 0.0) -> bool:
    return a[0] < b[2] + gap and a[2] > b[0] - gap and a[1] < b[3] + gap and a[3] > b[1] - gap


def find_free_spot(size: tuple[float, float], area: tuple[float, float, float, float],
                   occupied: list[tuple[float, float, float, float]], prefer: list[tuple[float, float]],
                   gap: float = 15.0, step: float = 10.0) -> tuple[float, float] | None:
    """Centre for a box ``size`` inside ``area`` clear of ``occupied`` (+ ``gap``): first the preferred
    centres, then a raster scan (left to right, top to bottom). None when nothing fits."""
    w, h = size

    def ok(cx: float, cy: float) -> bool:
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        if box[0] < area[0] or box[1] < area[1] or box[2] > area[2] or box[3] > area[3]:
            return False
        return not any(rects_overlap(box, o, gap) for o in occupied)

    for cx, cy in prefer:
        if ok(cx, cy):
            return cx, cy
    y = area[3] - h / 2
    while y >= area[1] + h / 2 - 1e-9:
        x = area[0] + w / 2
        while x <= area[2] - w / 2 + 1e-9:
            if ok(x, y):
                return x, y
            x += step
        y -= step
    return None


# ---------------------------------------------------------------------------- the tools

class DraftingTools:
    """Tools that turn an open CATPart/CATProduct into a checked ISO drawing."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        # (drawing full name, view name) -> {"kind", "right", "up", "parent"}; what CATIA cannot tell us later
        self._vreg: dict[tuple[str, str], dict[str, Any]] = {}
        self._drawings: dict[str, dict[str, Any]] = {}   # drawing full name -> {"source", "view_from", "title_h", "plan"}
        self._last: str | None = None                     # full name of the drawing this instance last created/used

    # ------------------------------------------------------------------ schemas
    def get_tool_definitions(self) -> list[dict[str, Any]]:
        drawing = {"type": "string", "description": "Name of the CATDrawing document (default: the active document)."}
        num2 = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
        return [
            {
                "name": "catia_drawing_create",
                "description": (
                    "Create a new CATDrawing with an ISO 5457 sheet (A0-A4), a main scale (ISO 5455), the projection "
                    "method (first_angle = Europe/ISO default, third_angle = USA) and the 0.7 mm drawing frame "
                    "(20 mm left margin, 10 mm elsewhere). Then call catia_drawing_add_view for each view, "
                    "catia_drawing_title_block, catia_drawing_export_pdf and catia_drawing_check.\n"
                    "sheet='auto' and/or scale='auto' need `source` (an OPEN CATPart/CATProduct): the tool measures the "
                    "views listed in `views` and picks the smallest sheet and the largest recommended scale (>= min_scale) "
                    "that hold them with room for dimensions and the title block; the plan (sheet, scale, view centres) is "
                    "returned and used by add_view when position='auto'. Units: mm. Returns JSON {drawing, sheet, scale, "
                    "projection, frame, plan, warnings}. The drawing is left open and active; nothing is saved."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sheet": {"type": "string", "default": "A3", "description": "'A0'..'A4' or 'auto'."},
                        "orientation": {"type": "string", "enum": ["landscape", "portrait"],
                                        "description": "Default: ISO 5457 (A0-A3 landscape, A4 portrait)."},
                        "scale": {"type": "string", "default": "1:1",
                                  "description": "'1:1', '1:2', '2:1', '1:5'... (paper:model) or 'auto'."},
                        "projection": {"type": "string", "enum": list(std.PROJECTION_METHODS), "default": "first_angle"},
                        "source": {"type": "string",
                                   "description": "Name of the open CATPart/CATProduct to draw (e.g. 'Flange.CATPart', or its part name 'Flange'). "
                                                  "Required for sheet/scale 'auto'; remembered for add_view."},
                        "views": {"type": "array", "items": {"type": "string", "enum": ["front", "top", "bottom", "left",
                                                                                          "right", "rear"]},
                                  "description": "Views to plan for 'auto' (default front, top, right)."},
                        "view_from": {"type": "string", "enum": list(V.VIEW_FROM), "default": "-Y",
                                      "description": "Side the viewer stands on for the FRONT view. '-Y' = CATIA's usual "
                                                     "front (X right, Z up); '+Z' = look down at the part (X right, Y up)."},
                        "min_scale": {"type": "string", "default": "1:2", "description": "Smallest acceptable scale for 'auto'."},
                        "title_block_height_mm": {"type": "number", "default": DEFAULT_TITLE_BLOCK_HEIGHT_MM,
                                                  "description": "Strip kept free above the bottom frame line for the title block."},
                        "frame": {"type": "boolean", "default": True, "description": "Draw the ISO 5457 frame."},
                    },
                },
            },
            {
                "name": "catia_drawing_add_view",
                "description": (
                    "Add one view of the 3D source to the drawing and place it. kind: front (the base view, create it "
                    "first), top/bottom/left/right/rear (orthographic projections of the front view: 'right' = seen from "
                    "the right of the front view), isometric, section, detail.\n"
                    "Placement (position='auto', default): ISO 5456-2 arrangement from the plan of catia_drawing_create "
                    "(first angle: top view BELOW the front, right view on its LEFT; third angle mirrored), else beside "
                    "the parent in a free spot. Give position [x, y] (sheet mm, origin bottom-left, view CENTRE) to override. "
                    "A view that would overlap another one or leave the frame is refused and removed.\n"
                    "View coordinates (u, w), used by section/detail/dimensions, are millimetres at MODEL scale with the "
                    "origin on the projection of the model origin; u to the right, w up. With view_from='-Y': front u=X, "
                    "w=Z; top u=X, w=Y; right u=Y, w=Z; left u=-Y, w=Z; bottom u=X, w=-Y; rear u=-X, w=Z.\n"
                    "section: give parent, cut_direction ('vertical' = cutting line x=cut_at, 'horizontal' = line y=cut_at, "
                    "in the parent's (u,w)) or cutting_line [[u1,w1],[u2,w2]]; the tool draws the cutting-plane arrows, "
                    "labels (A-A) and hatching; flip with side=1 if the wrong half is shown. detail: parent, centre [u,w], "
                    "radius (mm, model scale), scale (default '5:1'). Returns JSON: view name, kind, scale, centre, "
                    "paper box, origin, and the axes needed by catia_drawing_check."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(VIEW_KINDS_ALL)},
                        "drawing": drawing,
                        "source": {"type": "string", "description": "Open CATPart/CATProduct (default: the one given to create)."},
                        "label": {"type": "string", "description": "View name (default: the kind, capitalised; CATIA appends "
                                                                   "A-A / A to section and detail views)."},
                        "view_from": {"type": "string", "enum": list(V.VIEW_FROM),
                                      "description": "front only: side the viewer stands on (default: from create, '-Y')."},
                        "parent": {"type": "string", "description": "Reference view for projections, section, detail "
                                                                    "(default: the front view)."},
                        "position": {"anyOf": [num2, {"type": "string", "enum": ["auto"]}], "default": "auto"},
                        "scale": {"type": "string", "description": "View scale ('1:2', '5:1'); default the sheet scale "
                                                                    "(details default to '5:1'). A view whose scale differs "
                                                                    "from the sheet must be labelled (ISO 5455): CATIA prints it."},
                        "hidden_lines": {"type": "boolean", "default": False,
                                         "description": "Show hidden edges as thin dashed lines."},
                        "gap_mm": {"type": "number", "default": 25, "description": "Minimum clearance between views (paper mm)."},
                        "cut_direction": {"type": "string", "enum": ["vertical", "horizontal"]},
                        "cut_at": {"type": "number", "description": "section: u (vertical) or w (horizontal) of the cutting line."},
                        "cutting_line": {"type": "array", "items": num2, "minItems": 2, "maxItems": 2,
                                         "description": "section: [[u1, w1], [u2, w2]] in the parent view (model mm)."},
                        "side": {"type": "integer", "enum": [0, 1], "default": 0, "description": "section: which side is drawn."},
                        "centre": {**num2, "description": "detail: circle centre [u, w] in the parent view (model mm)."},
                        "radius": {"type": "number", "description": "detail: circle radius (model mm)."},
                    },
                    "required": ["kind"],
                },
            },
            {
                "name": "catia_drawing_generate_dimensions",
                "description": (
                    "Run CATIA's 'Generate Dimensions' on the active sheet: creates the dimensions driven by the 3D "
                    "constraints of the model (pad lengths, hole diameters...). It does NOT dimension everything (overall "
                    "sizes and hole positions are usually missing) and CATIA draws them green: complete the drawing with "
                    "catia_drawing_add_dimension. Returns JSON with every dimension per view (type, value, mm)."
                ),
                "inputSchema": {"type": "object", "properties": {"drawing": drawing}},
            },
            {
                "name": "catia_drawing_add_dimension",
                "description": (
                    "Add ONE dimension to a view, in view coordinates (u, w) (mm, model scale, see catia_drawing_add_view). "
                    "type='linear': `from` and `to` points, orientation horizontal | vertical | aligned, the dimension line "
                    "is placed offset_mm (paper) away on `side` (below/above for horizontal, right/left for vertical, "
                    "left/right of the from->to direction for aligned). type='diameter' or 'radius': `centre` and "
                    "`diameter`/`radius` of the circle, leader at leader_angle_deg. Take the values from the model "
                    "(catia_list_faces gives cylinder radii and axes), never from memory: the dimension shows the value "
                    "you give, the tool cannot attach it to the generated edge; catia_drawing_check compares it with the PDF.\n"
                    "ISO 129-1: one dimension per feature, keep dimension lines >= 10 mm from the outline (offset_mm default 10), "
                    "each following line 7 mm further. `prefix` e.g. '4x ' for identical holes. Returns JSON {dimension, "
                    "type, value_mm, expected_mm, line}; a value that differs from the request by more than 0.005 mm removes "
                    "the dimension and fails."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view": {"type": "string", "description": "View name (see catia_drawing_info)."},
                        "type": {"type": "string", "enum": ["linear", "diameter", "radius"]},
                        "drawing": drawing,
                        "from": {**num2, "description": "linear: first point [u, w]."},
                        "to": {**num2, "description": "linear: second point [u, w]."},
                        "orientation": {"type": "string", "enum": ["horizontal", "vertical", "aligned"], "default": "horizontal"},
                        "side": {"type": "string", "enum": ["below", "above", "right", "left"]},
                        "offset_mm": {"type": "number", "default": 10, "description": "Distance of the dimension line from the "
                                                                                       "feature, paper mm."},
                        "centre": {**num2, "description": "diameter/radius: circle centre [u, w]."},
                        "diameter": {"type": "number", "description": "diameter dimension: the circle diameter, mm."},
                        "radius": {"type": "number", "description": "radius dimension: the circle radius, mm."},
                        "leader_angle_deg": {"type": "number", "default": 45,
                                             "description": "diameter/radius: direction of the dimension line, degrees "
                                                            "counter-clockwise from +u."},
                        "prefix": {"type": "string", "description": "Text before the diameter/radius symbol and value, e.g. '4x ' "
                                                                    "is written before the diameter symbol."},
                        "allow_duplicate": {"type": "boolean", "default": False,
                                            "description": "diameter/radius: allow a second dimension of the same value "
                                                           "in the view (refused by default, ISO 129-1)."},
                    },
                    "required": ["view", "type"],
                },
            },
            {
                "name": "catia_drawing_add_centerlines",
                "description": (
                    "Draw centre lines (thin chain lines, ISO 128-2 type 04.1) crossing at each given circle centre of a "
                    "view, extending extension_mm (paper) beyond the circle. centres: [{u, w, radius}] in view coordinates. "
                    "Returns the number of lines created."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view": {"type": "string"},
                        "centres": {"type": "array", "items": {"type": "object", "properties": {
                            "u": {"type": "number"}, "w": {"type": "number"}, "radius": {"type": "number"}},
                            "required": ["u", "w", "radius"]}},
                        "extension_mm": {"type": "number", "default": 3},
                        "drawing": drawing,
                    },
                    "required": ["view", "centres"],
                },
            },
            {
                "name": "catia_drawing_title_block",
                "description": (
                    "Draw the ISO 7200:2004 title block (180 mm wide table in the bottom right corner of the frame), the "
                    "scale, the general tolerance note (ISO 2768) and the projection symbol of ISO 5456-2. fields: "
                    "legal_owner, identification_number, date_of_issue, sheet_number, title, approval_person, creator, "
                    "document_type are MANDATORY (missing ones are refused); optional: supplementary_title, revision_index, "
                    "number_of_sheets, responsible_department, technical_reference, document_status, classification, "
                    "paper_size. general_tolerance: f, m (default), c or v. Nothing is invented: only what you pass is "
                    "written. Returns JSON with the block rectangle, rows written and ISO warnings (e.g. too long a title)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fields": {"type": "object", "description": "ISO 7200 field key -> text.",
                                   "additionalProperties": {"type": "string"}},
                        "general_tolerance": {"type": "string", "enum": list(std.GENERAL_TOLERANCE_CLASSES), "default": "m"},
                        "drawing": drawing,
                    },
                    "required": ["fields"],
                },
            },
            {
                "name": "catia_drawing_export_pdf",
                "description": (
                    "Export the drawing to a vector PDF (DrawingDocument.ExportData). Checks the file exists and, when "
                    "PyMuPDF is installed, that the PDF page equals the sheet (mm). Returns JSON {path, bytes, page_mm, "
                    "sheet_mm, page_matches_sheet}. Overwrites an existing file."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Absolute path ending in .pdf."}, "drawing": drawing},
                    "required": ["path"],
                },
            },
            {
                "name": "catia_drawing_close",
                "description": (
                    "Close ONE drawing (the named one, else the drawing this session last used) without touching any other "
                    "document; the 3D source stays open. save=false (default) discards changes, save=true saves it first "
                    "(the drawing must already have a file name: use catia_save_document with a path beforehand)."
                ),
                "inputSchema": {"type": "object", "properties": {"drawing": drawing, "save": {"type": "boolean", "default": False}}},
            },
            {
                "name": "catia_drawing_info",
                "description": (
                    "Read the drawing back: sheet format, scale, projection, every view (kind, scale, centre, paper box, "
                    "origin) and every dimension (type, value). Read-only."
                ),
                "inputSchema": {"type": "object", "properties": {"drawing": drawing}},
            },
            {
                "name": "catia_drawing_check",
                "description": (
                    "CLOSED-LOOP CHECK: export the drawing to PDF, read it back and compare it with the 3D model. For every "
                    "orthographic view created by add_view: circles (diameters within 0.02 mm, centres within 0.05 mm, hole "
                    "pitches), the four sides of the projected outline, the view size on paper vs the 3D bounding box x scale. "
                    "The 3D side is read from `source` (catia_list_faces cylinders + catia_get_bounding_box of the active "
                    "body) unless `expected` is given. Diameter/radius dimensions are cross-checked against the circles found "
                    "in the PDF. Limits: a blind hole is only visible from its open side (list the views that show it in "
                    "`views`); circles under 2 mm are text-sized and ignored. Returns JSON with ok and per-view details."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "drawing": drawing,
                        "source": {"type": "string"},
                        "views": {"type": "array", "items": {"type": "string"}, "description": "View names to check (default: all)."},
                        "pdf": {"type": "string", "description": "Existing PDF of this drawing (default: export a temporary one)."},
                        "expected": {"type": "object", "description": "Override of the 3D side: {view: {circles: [{cx, cy, d}], "
                                                                       "box: [umin, wmin, umax, wmax]}} in view mm.",
                                     "additionalProperties": True},
                        "diameter_tol_mm": {"type": "number", "default": 0.02},
                        "centre_tol_mm": {"type": "number", "default": 0.05},
                        "require_circles": {"type": "integer", "minimum": 0,
                                            "description": "Fail unless at least this many circles were expected and "
                                                           "matched in total (a check that expects nothing proves nothing)."},
                        "fail_on_mismatch": {"type": "boolean", "default": False,
                                             "description": "Raise an error (exit code 1 in the scenario runner) when "
                                                            "anything does not match."},
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        table = {
            "catia_drawing_create": self._create,
            "catia_drawing_add_view": self._add_view,
            "catia_drawing_generate_dimensions": self._generate_dimensions,
            "catia_drawing_add_dimension": self._add_dimension,
            "catia_drawing_add_centerlines": self._add_centerlines,
            "catia_drawing_title_block": self._title_block,
            "catia_drawing_export_pdf": self._export_pdf,
            "catia_drawing_info": self._info,
            "catia_drawing_close": self._close,
            "catia_drawing_check": self._check,
        }
        fn = table.get(tool_name)
        if fn is None:
            raise ValueError(f"Unknown drafting tool: {tool_name}")
        self.conn.ensure_connected()
        return _dump(fn(arguments))

    # ------------------------------------------------------------------ CATIA helpers
    @property
    def app(self) -> Any:
        return self.conn.app

    def _documents(self) -> list[Any]:
        docs = self.app.Documents
        return [docs.Item(i) for i in range(1, docs.Count + 1)]

    def _find_doc(self, name: str) -> Any:
        low = name.lower()
        docs = self._documents()
        for d in docs:
            if d.Name.lower() == low or d.FullName.lower() == low:
                return d
        for d in docs:                      # 'Flange' = document 'Flange.CATPart' or a part/product of that name
            if d.Name.lower().rsplit(".", 1)[0] == low:
                return d
        for d in docs:
            try:
                inner = d.Part.Name if d.Name.lower().endswith(".catpart") else d.Product.PartNumber
            except Exception:
                continue
            if str(inner).lower() == low:
                return d
        open_names = ", ".join(d.Name for d in self._documents()) or "none"
        raise DraftingError(f"document {name!r} is not open in CATIA (open: {open_names}). "
                            f"Open it first (catia_open_document).")

    @staticmethod
    def _is_drawing(doc: Any) -> bool:
        return doc.Name.lower().endswith(".catdrawing")

    def _drawing_doc(self, name: str | None) -> Any:
        doc = None
        if name:
            doc = self._find_doc(name)
        else:
            # Prefer the drawing this server last worked on: the ACTIVE document may have been changed by
            # someone else driving the same CATIA between two calls.
            if self._last:
                doc = next((d for d in self._documents() if d.FullName == self._last), None)
            if doc is None:
                try:
                    doc = self.app.ActiveDocument
                except Exception:
                    doc = None
            if doc is None:
                raise DraftingError("no active document; create a drawing with catia_drawing_create")
        if not self._is_drawing(doc):
            gone = (f" (the drawing this session was working on, {os.path.basename(self._last)!r}, is no longer open: "
                    f"it was closed, possibly by another client of the same CATIA)") if self._last and not name else ""
            raise DraftingError(f"{doc.Name!r} is not a CATDrawing{gone}; pass drawing=<name> or create one with "
                                f"catia_drawing_create")
        doc.Activate()
        self._last = doc.FullName
        return doc

    def _source_doc(self, drawing: Any, name: str | None) -> Any:
        info = self._drawings.get(drawing.FullName, {})
        name = name or info.get("source")
        if name:
            doc = self._find_doc(name)
        else:
            cands = [d for d in self._documents() if d.Name.lower().endswith((".catpart", ".catproduct"))]
            if len(cands) != 1:
                raise DraftingError("say which model to draw with source=<name of an open CATPart/CATProduct>"
                                    + (f" (open: {', '.join(d.Name for d in cands)})" if cands else " (none is open)"))
            doc = cands[0]
        if self._is_drawing(doc):
            raise DraftingError(f"source {doc.Name!r} is a drawing, not a part or product")
        return doc

    @staticmethod
    def _sheet(doc: Any) -> Any:
        return doc.Sheets.ActiveSheet

    def _all_views(self, sheet: Any) -> list[Any]:
        vs = sheet.Views
        return [vs.Item(i) for i in range(1, vs.Count + 1)]

    def _view(self, sheet: Any, name: str) -> Any:
        for v in self._all_views(sheet):
            if v.Name == name:
                return v
        names = ", ".join(v.Name for v in self._all_views(sheet) if v.Name not in ("Main View", "Background View"))
        raise DraftingError(f"no view named {name!r} in this drawing (views: {names or 'none'})")

    def _size_box(self, view: Any) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) of a view on the sheet, mm (VBScript: byref array)."""
        a = self.app.SystemService.Evaluate(_VBS_SIZE, 0, "CATMain", [view])
        return float(a[0]), float(a[2]), float(a[1]), float(a[3])

    def _sheet_size(self, sheet: Any) -> tuple[float, float]:
        return float(sheet.GetPaperWidth()), float(sheet.GetPaperHeight())

    def _sheet_name(self, sheet: Any) -> str:
        return {v: k for k, v in PAPER_CODE.items()}.get(int(sheet.PaperSize), "?")

    def _style(self, doc: Any, obj: Any, *, width: str | None = None, line: str | None = None, hide: bool = False) -> None:
        sel = doc.Selection
        sel.Clear()
        sel.Add(obj)
        try:
            vp = sel.VisProperties
            if width:
                vp.SetRealWidth(WIDTH_INDEX[width], 1)
            if line:
                vp.SetRealLineType(LINE_TYPE_INDEX[line], 1)
            if hide:
                vp.SetShow(1)
        finally:
            sel.Clear()

    def _activate_view(self, view: Any) -> Any:
        view.Activate()
        return view.Factory2D

    def _restore_main_view(self, sheet: Any) -> None:
        try:
            sheet.Views.Item("Main View").Activate()
        except Exception:
            pass

    def _view_axes(self, doc: Any, view_name: str) -> dict[str, Any] | None:
        return self._vreg.get((doc.FullName, view_name))

    def _frame(self, doc: Any, view: Any) -> V.ViewFrame:
        sheet = self._sheet(doc)
        _, h = self._sheet_size(sheet)
        info = self._vreg.get((doc.FullName, view.Name), {})
        return V.ViewFrame(float(view.x), float(view.y), float(view.Scale),
                           (float(view.xAxisData), float(view.yAxisData)), h,
                           tuple(info.get("right", (1.0, 0.0, 0.0))), tuple(info.get("up", (0.0, 1.0, 0.0))))

    def _view_summary(self, doc: Any, view: Any) -> dict[str, Any]:
        box = self._size_box(view)
        info = self._vreg.get((doc.FullName, view.Name), {})
        vt = int(view.ViewType)
        return {
            "name": view.Name, "type": VIEW_TYPE_NAMES.get(vt, str(vt)), "kind": info.get("kind"),
            "scale": scale_label(float(view.Scale)), "scale_ratio": _r(view.Scale, 6),
            "centre_mm": [_r(view.x, 3), _r(view.y, 3)],
            "origin_mm": [_r(view.xAxisData, 3), _r(view.yAxisData, 3)],
            "paper_box_mm": [_r(b, 3) for b in box],
            "size_mm": [_r(box[2] - box[0], 3), _r(box[3] - box[1], 3)],
            "axes": None if not info.get("right") else {"right": [_r(c, 6) for c in info["right"]],
                                                        "up": [_r(c, 6) for c in info["up"]]},
        }

    def _generative_views(self, sheet: Any) -> list[Any]:
        return [v for v in self._all_views(sheet) if v.Name not in ("Main View", "Background View")]

    def _draw_line(self, doc: Any, factory: Any, p1: tuple[float, float], p2: tuple[float, float], *,
                   width: str = "thin", line: str = "continuous") -> Any:
        ln = factory.CreateLine(p1[0], p1[1], p2[0], p2[1])
        self._style(doc, ln, width=width, line=line)
        return ln

    # ------------------------------------------------------------------ 3D helpers
    def _product(self, source_doc: Any) -> Any:
        last: Exception | None = None
        for _ in range(3):          # another client of the same CATIA can briefly disturb a document
            try:
                return source_doc.Product
            except Exception as e:
                last = e
                time.sleep(0.5)
        raise DraftingError(f"{source_doc.Name!r} has no Product ({last}); a drawing view needs an open, fully "
                            f"loaded CATPart or CATProduct") from None

    def _attach(self, gen: Any, product: Any) -> None:
        try:
            gen.Document = product
        except Exception as e:
            raise DraftingError(f"could not link the view to the 3D model: {e}") from None

    def _define_view(self, sheet: Any, product: Any, kind: str, label: str, *, front_axes: tuple | None,
                     parent: Any | None, scale: float, hidden: bool, extra: dict[str, Any]) -> Any:
        """Create and define a view; returns it (not yet positioned by the caller's rules)."""
        views = sheet.Views
        v = views.Add(label)
        try:
            g = v.GenerativeBehavior
            if kind == "front":
                self._attach(g, product)
                h, up = front_axes  # type: ignore[misc]
                g.DefineFrontView(*h, *up)
            elif kind == "isometric":
                self._attach(g, product)
                r, u = extra["axes"]
                g.DefineIsometricView(*r, *u)
            else:
                if parent is None:
                    raise DraftingError(f"the {kind} view needs a parent view")
                pg = parent.GenerativeBehavior
                if kind in PROJ_VIEW_CODE:
                    g.DefineProjectionView(pg, PROJ_VIEW_CODE[kind])
                elif kind == "section":
                    g.DefineSectionView(tuple(extra["profile"]), "SectionCut", "Offset", int(extra.get("side", 0)), pg)
                elif kind == "detail":
                    g.DefineCircularDetailView(float(extra["centre"][0]), float(extra["centre"][1]),
                                               float(extra["radius"]), pg)
                else:
                    raise DraftingError(f"unknown view kind {kind!r}")
                self._attach(g, product)
            if hidden:
                g.HiddenLineMode = 1
            v.Scale = scale
            return v
        except Exception:
            self._remove_view(sheet, v)
            raise

    @staticmethod
    def _remove_view(sheet: Any, view: Any) -> None:
        try:
            sheet.Views.Remove(view.Name)
        except Exception:
            pass

    def _measure_sizes(self, doc: Any, product: Any, kinds: list[str], h: tuple, v: tuple) -> dict[str, tuple[float, float]]:
        """Model size (w, h) of each requested orthographic view, measured on temporary views (removed after)."""
        sheet = self._sheet(doc)
        sizes: dict[str, tuple[float, float]] = {}
        made: list[Any] = []
        try:
            front = self._define_view(sheet, product, "front", "_tmp_front", front_axes=(h, v), parent=None,
                                      scale=1.0, hidden=False, extra={})
            made.append(front)
            front.GenerativeBehavior.Update()
            b = self._size_box(front)
            sizes["front"] = (b[2] - b[0], b[3] - b[1])
            for k in kinds:
                if k == "front":
                    continue
                pv = self._define_view(sheet, product, k, f"_tmp_{k}", front_axes=None, parent=front, scale=1.0,
                                       hidden=False, extra={})
                made.append(pv)
                pv.GenerativeBehavior.Update()
                b = self._size_box(pv)
                sizes[k] = (b[2] - b[0], b[3] - b[1])
        finally:
            for m in reversed(made):
                self._remove_view(sheet, m)
        return sizes

    # ------------------------------------------------------------------ catia_drawing_create
    def _create(self, a: dict[str, Any]) -> dict[str, Any]:
        sheet_req = str(a.get("sheet", "A3"))
        scale_req = a.get("scale", "1:1")
        projection = a.get("projection", "first_angle")
        orientation = a.get("orientation")
        view_from = a.get("view_from", "-Y")
        kinds = list(a.get("views") or ["front", "top", "right"])
        title_h = float(a.get("title_block_height_mm", DEFAULT_TITLE_BLOCK_HEIGHT_MM))
        warnings = validate_sheet_request(sheet_req, scale_req, projection, orientation)
        auto = sheet_req.lower() == "auto" or str(scale_req).lower() == "auto"
        h, up = V.front_axes(view_from)
        for k in kinds:
            if k not in V.VIEW_KINDS:
                raise DraftingError(f"views: unknown kind {k!r}; expected {', '.join(V.VIEW_KINDS)}")
        if "front" not in kinds:
            kinds.insert(0, "front")
        if auto and not a.get("source"):
            raise DraftingError("sheet/scale 'auto' needs source=<name of an open CATPart/CATProduct> to measure")
        source = self._find_doc(a["source"]) if a.get("source") else None
        product = self._product(source) if source is not None else None

        doc = self.app.Documents.Add("Drawing")
        try:
            sheet = self._sheet(doc)
            first = std.get_sheet(sheet_req) if sheet_req.lower() != "auto" else std.get_sheet("A3")
            sheet.PaperSize = PAPER_CODE[first.name]
            sheet.Orientation = ORIENTATION_CODE[orientation or first.orientation]
            sheet.ProjectionMethod = PROJECTION_CODE[projection]
            plan = None
            if auto:
                sizes = self._measure_sizes(doc, product, kinds, h, up)
                plan = V.plan_layout(
                    sizes, projection, sheet=sheet_req, scale=scale_req, orientation=orientation,
                    title_block_height_mm=title_h, min_scale=a.get("min_scale", "1:2"))
                if not plan.fits:
                    raise DraftingError("no sheet holds the requested views: " + "; ".join(plan.notes))
                sheet_name, orient, sc = plan.sheet, plan.orientation, plan.ratio
            else:
                sheet_name = first.name
                orient = orientation or first.orientation
                sc = std.parse_scale(scale_req).ratio
            sheet.PaperSize = PAPER_CODE[sheet_name]
            sheet.Orientation = ORIENTATION_CODE[orient]
            sheet.Scale = sc
            w, hh = self._sheet_size(sheet)
            exp = std.get_sheet(sheet_name).size(orient)
            if abs(w - exp[0]) > 0.01 or abs(hh - exp[1]) > 0.01:
                raise DraftingError(f"CATIA sheet is {w:g} x {hh:g} mm, ISO 5457 says {exp[0]:g} x {exp[1]:g}")
            if a.get("frame", True):
                self._draw_frame(doc, sheet, sheet_name, orient)
            else:
                self._restore_main_view(sheet)
        except Exception:
            try:
                doc.Close()
            except Exception:
                pass
            raise
        self._last = doc.FullName
        self._drawings[doc.FullName] = {"source": source.Name if source is not None else None, "view_from": view_from,
                                        "title_h": title_h, "plan": plan, "projection": projection,
                                        "sheet": sheet_name, "orientation": orient, "kinds": kinds}
        fx0, fy0, fx1, fy1 = std.frame_rect(sheet_name, orient)
        out: dict[str, Any] = {
            "ok": True, "drawing": doc.Name,
            "sheet": {"size": sheet_name, "orientation": orient, "width_mm": w, "height_mm": hh},
            "scale": scale_label(sc), "projection": projection, "standard": "ISO",
            "frame_mm": [fx0, fy0, fx1, fy1],
            "warnings": warnings,
        }
        if plan is not None:
            out["plan"] = plan.to_dict()
        return out

    def _draw_frame(self, doc: Any, sheet: Any, sheet_name: str, orient: str) -> None:
        x0, y0, x1, y1 = std.frame_rect(sheet_name, orient)
        bg = sheet.Views.Item("Background View")
        f = self._activate_view(bg)
        try:
            for p, q in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
                self._draw_line(doc, f, p, q, width="wide")
        finally:
            self._restore_main_view(sheet)

    # ------------------------------------------------------------------ catia_drawing_add_view
    def _plan_position(self, doc: Any, kind: str) -> tuple[float, float] | None:
        plan = self._drawings.get(doc.FullName, {}).get("plan")
        if plan is not None and kind in plan.positions:
            return plan.positions[kind]
        return None

    def _add_view(self, a: dict[str, Any]) -> dict[str, Any]:
        kind = a["kind"]
        if kind not in VIEW_KINDS_ALL:
            raise DraftingError(f"kind must be one of {', '.join(VIEW_KINDS_ALL)}")
        doc = self._drawing_doc(a.get("drawing"))
        sheet = self._sheet(doc)
        dinfo = self._drawings.setdefault(doc.FullName, {"view_from": "-Y", "title_h": DEFAULT_TITLE_BLOCK_HEIGHT_MM,
                                                          "plan": None, "projection": None})
        source = self._source_doc(doc, a.get("source"))
        product = self._product(source)
        projection = self._projection_name(sheet)
        views = self._generative_views(sheet)
        label = a.get("label") or kind.capitalize()
        if any(v.Name == label or v.Name.startswith(label + "A") for v in views) and not a.get("label"):
            label = f"{label}{len([v for v in views if v.Name.startswith(label)]) + 1}"
        sheet_scale = float(sheet.Scale)
        gap = float(a.get("gap_mm", 25))
        hidden = bool(a.get("hidden_lines", False))

        # scale
        if a.get("scale"):
            try:
                scale = std.parse_scale(a["scale"]).ratio
            except ValueError as e:
                raise DraftingError(f"scale: {e}") from None
        elif kind == "detail":
            scale = std.parse_scale("5:1").ratio
        else:
            scale = sheet_scale

        # parent
        parent = None
        front_axes = None
        extra: dict[str, Any] = {}
        axes: tuple | None = None
        if kind == "front":
            if any((self._vreg.get((doc.FullName, v.Name)) or {}).get("kind") == "front" for v in views):
                raise DraftingError("this drawing already has a front view; add projections with kind top/right/..., or "
                                    "give a different label if you really want a second base view")
            vf = a.get("view_from") or dinfo.get("view_from", "-Y")
            front_axes = V.front_axes(vf)
            axes = front_axes
            dinfo["view_from"] = vf
        else:
            pname = a.get("parent")
            if pname:
                parent = self._view(sheet, pname)
            else:
                fronts = [v for v in views if (self._vreg.get((doc.FullName, v.Name)) or {}).get("kind") == "front"]
                if not fronts:
                    raise DraftingError("create the front view first (kind='front'): every other view is derived from it")
                parent = fronts[0]
            pinfo = self._vreg.get((doc.FullName, parent.Name)) or {}
            if kind in PROJ_VIEW_CODE or kind == "isometric":
                base = self._front_frame_axes(doc, views)
                if kind == "isometric":
                    axes = V.isometric_axes(*base)
                    extra["axes"] = axes
                else:
                    if pinfo.get("kind") != "front":
                        raise DraftingError("projected views must have the FRONT view as parent (ISO 5456-2 arrangement)")
                    axes = V.view_axes(kind, *base)
            if kind == "section":
                extra["profile"], extra["side"] = self._section_profile(a, parent, doc), a.get("side", 0)
            if kind == "detail":
                extra["centre"] = parse_point2(a.get("centre"), "centre")
                if not a.get("radius") or float(a["radius"]) <= 0:
                    raise DraftingError("detail needs radius > 0 (mm, model scale, in the parent view)")
                extra["radius"] = float(a["radius"])

        v = self._define_view(sheet, product, kind, label, front_axes=front_axes, parent=parent, scale=scale,
                              hidden=hidden, extra=extra)
        try:
            v.GenerativeBehavior.Update()
            if kind in ("section", "detail") and not a.get("label"):
                try:                      # CATIA names it '<label>A-A' / '<label>A': keep the ISO letters only
                    _pre, ident, suf = v.GetViewName("", "", "")
                    v.SetViewName("", ident, suf)
                except Exception:
                    pass
            box0 = self._size_box(v)
            size = (box0[2] - box0[0], box0[3] - box0[1])
            pos = self._choose_position(doc, sheet, kind, a.get("position", "auto"), size, parent, projection, gap)
            v.x, v.y = float(pos[0]), float(pos[1])
            v.GenerativeBehavior.Update()
            box = self._size_box(v)
            self._check_placement(doc, sheet, v, box, dinfo)
        except Exception:
            self._remove_view(sheet, v)
            raise
        self._vreg[(doc.FullName, v.Name)] = {"kind": kind, "parent": parent.Name if parent is not None else None,
                                               "right": axes[0] if axes else None, "up": axes[1] if axes else None,
                                               "hidden_lines": hidden}
        if kind == "front":
            dinfo["front_axes"] = front_axes
        out = self._view_summary(doc, v)
        out["ok"] = True
        out["projection"] = projection
        return out

    def _front_frame_axes(self, doc: Any, views: list[Any]) -> tuple[tuple, tuple]:
        info = self._drawings.get(doc.FullName, {})
        if info.get("front_axes"):
            return info["front_axes"]
        for v in views:
            vi = self._vreg.get((doc.FullName, v.Name)) or {}
            if vi.get("kind") == "front":
                return tuple(vi["right"]), tuple(vi["up"])
        raise DraftingError("create the front view first")

    def _projection_name(self, sheet: Any) -> str:
        return "third_angle" if int(sheet.ProjectionMethod) == 1 else "first_angle"

    def _section_profile(self, a: dict[str, Any], parent: Any, doc: Any) -> list[float]:
        if a.get("cutting_line"):
            (u1, w1), (u2, w2) = (parse_point2(p, "cutting_line point") for p in a["cutting_line"])
            return [u1, w1, u2, w2]
        d, at = a.get("cut_direction"), a.get("cut_at")
        if d not in ("vertical", "horizontal") or at is None:
            raise DraftingError("section needs cutting_line, or cut_direction ('vertical'|'horizontal') and cut_at (mm)")
        box = self._size_box(parent)
        sc = float(parent.Scale)
        ox, oy = float(parent.xAxisData), float(parent.yAxisData)
        # parent's model extents around the origin, with a 5 mm (paper) overshoot
        umin, umax = (box[0] - ox) / sc - 5 / sc, (box[2] - ox) / sc + 5 / sc
        wmin, wmax = (box[1] - oy) / sc - 5 / sc, (box[3] - oy) / sc + 5 / sc
        return [float(at), wmin, float(at), wmax] if d == "vertical" else [umin, float(at), umax, float(at)]

    def _occupied(self, doc: Any, sheet: Any, exclude: Any | None = None) -> list[tuple[float, float, float, float]]:
        out = []
        for v in self._generative_views(sheet):
            if exclude is not None and v.Name == exclude.Name:
                continue
            out.append(self._size_box(v))
        return out

    def _free_area(self, doc: Any, sheet: Any) -> tuple[float, float, float, float]:
        info = self._drawings.get(doc.FullName, {})
        name = self._sheet_name(sheet)
        orient = "landscape" if int(sheet.Orientation) == 1 else "portrait"
        return V.free_area(name, orient, float(info.get("title_h", DEFAULT_TITLE_BLOCK_HEIGHT_MM)), 5.0)

    def _choose_position(self, doc: Any, sheet: Any, kind: str, position: Any, size: tuple[float, float], parent: Any,
                         projection: str, gap: float) -> tuple[float, float]:
        if position != "auto" and position is not None:
            return parse_point2(position, "position")
        planned = self._plan_position(doc, kind) if kind in V.VIEW_KINDS else None
        if planned is not None:
            return planned
        area = self._free_area(doc, sheet)
        occupied = self._occupied(doc, sheet, exclude=None)
        w, h = size
        if parent is None:                       # front view without a plan: centre of the free area
            cx, cy = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
            spot = find_free_spot(size, area, occupied, [(cx, cy)], gap=0.0)
        else:
            pb = self._size_box(parent)
            pcx, pcy = (pb[0] + pb[2]) / 2, (pb[1] + pb[3]) / 2
            pw, ph = pb[2] - pb[0], pb[3] - pb[1]
            prefer: list[tuple[float, float]] = []
            if kind in V.VIEW_KINDS and kind not in ("front", "rear"):
                side = std.expected_view_side(projection, kind)
                dx, dy = V._SIDE_VECTOR[side]  # type: ignore[index]
                prefer.append((pcx + dx * (pw / 2 + gap + w / 2), pcy + dy * (ph / 2 + gap + h / 2)))
            prefer += [(pcx + pw / 2 + gap + w / 2, pcy), (pcx, pcy - ph / 2 - gap - h / 2),
                       (pcx, pcy + ph / 2 + gap + h / 2), (pcx - pw / 2 - gap - w / 2, pcy)]
            spot = find_free_spot(size, area, occupied, prefer, gap=gap)
        if spot is None:
            raise DraftingError(f"no free room for a {w:.0f} x {h:.0f} mm view on this sheet (free area "
                                f"{area[2] - area[0]:.0f} x {area[3] - area[1]:.0f} mm): use a larger sheet, a smaller scale, "
                                f"or give position=[x, y]")
        return spot

    def _check_placement(self, doc: Any, sheet: Any, view: Any, box: tuple[float, float, float, float],
                         dinfo: dict[str, Any]) -> None:
        name = self._sheet_name(sheet)
        orient = "landscape" if int(sheet.Orientation) == 1 else "portrait"
        fx0, fy0, fx1, fy1 = std.frame_rect(name, orient)
        if box[0] < fx0 - 1e-6 or box[1] < fy0 - 1e-6 or box[2] > fx1 + 1e-6 or box[3] > fy1 + 1e-6:
            raise DraftingError(
                f"view {view.Name!r} would extend beyond the frame (box {[_r(b, 1) for b in box]}, frame "
                f"{[fx0, fy0, fx1, fy1]}); pick a smaller scale, a larger sheet or another position")
        for other in self._occupied(doc, sheet, exclude=view):
            if rects_overlap(box, other):
                raise DraftingError(f"view {view.Name!r} would overlap another view (box {[_r(b, 1) for b in box]}); "
                                    f"give position=[x, y] or a larger gap_mm")
        tb_h = float(dinfo.get("title_h", DEFAULT_TITLE_BLOCK_HEIGHT_MM))
        tb = std.title_block_rect(name, tb_h, orient)
        if rects_overlap(box, tb):
            raise DraftingError(f"view {view.Name!r} would overlap the title block area (bottom {tb_h:g} mm of the frame)")

    # ------------------------------------------------------------------ dimensions
    def _dim_list(self, doc: Any, sheet: Any) -> list[dict[str, Any]]:
        out = []
        for v in self._generative_views(sheet):
            dims = v.Dimensions
            for i in range(1, dims.Count + 1):
                dm = dims.Item(i)
                try:
                    val = float(dm.GetValue().Value)
                except Exception:
                    val = None
                out.append({"view": v.Name, "name": dm.Name, "type": DIM_TYPE_NAMES.get(int(dm.DimType), str(dm.DimType)),
                            "value_mm": None if val is None else _r(val)})
        return out

    def _generate_dimensions(self, a: dict[str, Any]) -> dict[str, Any]:
        doc = self._drawing_doc(a.get("drawing"))
        sheet = self._sheet(doc)
        before = len(self._dim_list(doc, sheet))
        sheet.GenerateDimensions()
        dims = self._dim_list(doc, sheet)
        return {"ok": True, "created": len(dims) - before, "dimensions": dims,
                "note": "Only dimensions driven by 3D constraints are generated (green in the PDF); overall sizes and hole "
                        "positions usually need catia_drawing_add_dimension."}

    def _add_dimension(self, a: dict[str, Any]) -> dict[str, Any]:
        doc = self._drawing_doc(a.get("drawing"))
        sheet = self._sheet(doc)
        view = self._view(sheet, a["view"])
        typ = a["type"]
        scale = float(view.Scale)
        off = float(a.get("offset_mm", 10)) / scale
        f = self._activate_view(view)
        made: list[Any] = []
        try:
            if typ == "linear":
                p1, p2 = parse_point2(a.get("from"), "from"), parse_point2(a.get("to"), "to")
                orient = a.get("orientation", "horizontal")
                if orient not in ("horizontal", "vertical", "aligned"):
                    raise DraftingError("orientation must be horizontal, vertical or aligned")
                expected = linear_value(orient, p1, p2)
                if expected < 1e-6:
                    raise DraftingError("the two points give a zero dimension for this orientation")
                pt1, pt2 = f.CreatePoint(*p1), f.CreatePoint(*p2)
                for pt in (pt1, pt2):
                    self._style(doc, pt, hide=True)
                made += [pt1, pt2]
                dm = view.Dimensions.Add(DIM_TYPE["distance"], (pt1, pt2), (p1[0], p1[1], p2[0], p2[1]), DIM_LINE_REP[orient])
                pos = dimension_line_position(orient, p1, p2, off, a.get("side"))
                dm.MoveValue(pos[0], pos[1], 0, 0)
            elif typ in ("diameter", "radius"):
                c = parse_point2(a.get("centre"), "centre")
                key = "diameter" if typ == "diameter" else "radius"
                if not a.get(key) or float(a[key]) <= 0:
                    raise DraftingError(f"{typ} dimension needs {key} > 0 (mm)")
                r = float(a[key]) / (2 if typ == "diameter" else 1)
                expected = float(a[key])
                if not a.get("allow_duplicate"):
                    twin = self._same_dimension(view, typ, expected)
                    if twin:
                        raise DraftingError(
                            f"view {view.Name!r} already has {typ} dimension {twin} of {expected:g} mm (created by "
                            f"catia_drawing_generate_dimensions or an earlier call). ISO 129-1:2018 4.1.1: dimension a "
                            f"feature once, use a prefix such as '4x ' for identical features; pass allow_duplicate=true "
                            f"only for a genuinely different feature")
                circ = f.CreateClosedCircle(c[0], c[1], r)
                self._style(doc, circ, hide=True)
                made.append(circ)
                ang = math.radians(float(a.get("leader_angle_deg", 45)))
                dm = view.Dimensions.Add(DIM_TYPE[typ], (circ,), (c[0] + r, c[1]), DIM_LINE_REP["auto"])
                dm.MoveValue(c[0] + (r + off) * math.cos(ang), c[1] + (r + off) * math.sin(ang), 0, 0)
            else:
                raise DraftingError("type must be linear, diameter or radius")
            value = float(dm.GetValue().Value)
            if abs(value - expected) > 0.005:
                try:
                    view.Dimensions.Remove(view.Dimensions.Count)
                except Exception:
                    pass
                raise DraftingError(f"CATIA measured {value:.4f} mm but {expected:.4f} mm was requested; dimension removed")
            if a.get("prefix"):
                dv = dm.GetValue()
                # The default prefix is a placeholder token that cannot be re-inserted (a literal '<DIAMETER>' is
                # printed), so the symbol is written as text: U+00D8 for a diameter, 'R' for a radius.
                sym = {"diameter": "\u00d8", "radius": "R"}.get(typ, "")
                dv.SetPSText(1, str(a["prefix"]) + sym, "")
            try:
                g = self.app.SystemService.Evaluate(_VBS_DIMLINE, 0, "CATMain", [dm])
                line = [_r(g[0]), _r(g[1]), _r(g[2]), _r(g[3])]
            except Exception:
                line = None
            return {"ok": True, "dimension": dm.Name, "view": view.Name, "type": typ, "value_mm": _r(value),
                    "expected_mm": _r(expected), "dimension_line_view_mm": line, "unit": "mm"}
        finally:
            self._restore_main_view(sheet)

    @staticmethod
    def _same_dimension(view: Any, typ: str, value: float) -> str | None:
        dims = view.Dimensions
        for i in range(1, dims.Count + 1):
            dm = dims.Item(i)
            try:
                if DIM_TYPE_NAMES.get(int(dm.DimType)) == typ and abs(float(dm.GetValue().Value) - value) <= 0.005:
                    return dm.Name
            except Exception:
                continue
        return None

    def _add_centerlines(self, a: dict[str, Any]) -> dict[str, Any]:
        doc = self._drawing_doc(a.get("drawing"))
        sheet = self._sheet(doc)
        view = self._view(sheet, a["view"])
        ext = float(a.get("extension_mm", 3)) / float(view.Scale)
        f = self._activate_view(view)
        n = 0
        try:
            for c in a["centres"]:
                u, w, r = float(c["u"]), float(c["w"]), float(c["radius"])
                if r <= 0:
                    raise DraftingError("radius must be > 0")
                self._draw_line(doc, f, (u - r - ext, w), (u + r + ext, w), width="thin", line="chain")
                self._draw_line(doc, f, (u, w - r - ext), (u, w + r + ext), width="thin", line="chain")
                n += 2
        finally:
            self._restore_main_view(sheet)
        return {"ok": True, "lines_created": n, "line_type": "chain (ISO 128 04.1)", "width": "thin"}

    # ------------------------------------------------------------------ title block
    def _title_block(self, a: dict[str, Any]) -> dict[str, Any]:
        doc = self._drawing_doc(a.get("drawing"))
        sheet = self._sheet(doc)
        fields = {k: v for k, v in dict(a["fields"]).items() if v not in (None, "")}
        name = self._sheet_name(sheet)
        orient = "landscape" if int(sheet.Orientation) == 1 else "portrait"
        projection = self._projection_name(sheet)
        scale_txt = scale_label(float(sheet.Scale))
        rows = title_block_rows(fields, general_tolerance=a.get("general_tolerance", "m"), scale=scale_txt,
                                projection=projection, paper_size=name)
        spec = {"title_block": dict(fields, height_mm=len(rows) * TITLE_ROW_HEIGHT_MM, width_mm=sum(TITLE_COLS_MM)),
                "sheet": {"size": name, "orientation": orient}, "scale": scale_txt, "projection": projection}
        issues = std.validate_drawing_spec(spec)
        errors = [i for i in issues if i.severity == "error" and i.code.startswith("title_block")]
        if errors:
            raise DraftingError("title block refused: " + "; ".join(i.message for i in errors)
                                + ". Mandatory fields: " + ", ".join(MANDATORY_TITLE_FIELDS))
        warnings = [str(i) for i in issues if i.severity == "warning" and i.code.startswith("title_block")]
        x0, y0, x1, _ = std.frame_rect(name, orient)
        width = min(sum(TITLE_COLS_MM), x1 - x0)
        left = x1 - width
        height = len(rows) * TITLE_ROW_HEIGHT_MM
        sym_h = 2 * 3.5
        zone = (left, y0, x1, y0 + height + 3.0 + sym_h)          # block + projection symbol above it
        for other in self._occupied(doc, sheet):
            if rects_overlap(zone, other):
                raise DraftingError(
                    f"the title block zone {[_r(z, 1) for z in zone]} overlaps a view; the block needs the bottom "
                    f"{height + 3 + sym_h:.0f} mm of the frame: recreate the drawing with title_block_height_mm >= "
                    f"{height + 3 + sym_h + 10:.0f}, a smaller scale or a larger sheet")
        bg = sheet.Views.Item("Background View")
        f = self._activate_view(bg)
        try:
            tbl = bg.Tables.Add(left, y0, len(rows), 4, TITLE_ROW_HEIGHT_MM, TITLE_COLS_MM[0])
            for c, wcol in enumerate(TITLE_COLS_MM, 1):
                tbl.SetColumnSize(c, wcol * width / sum(TITLE_COLS_MM))
            for r, row in enumerate(rows, 1):
                full = row[0].startswith("*")
                if full:
                    tbl.MergeCells(r, 2, 1, 3)
                    row = [row[0][1:], row[1], "", ""]
                for c, txt in enumerate(row, 1):
                    if txt:
                        tbl.SetCellString(r, c, txt)
                        tbl.GetCellObject(r, c).SetFontSize(0, 0, TITLE_FONT_MM)
                tbl.SetRowSize(r, TITLE_ROW_HEIGHT_MM)
            tbl.AnchorPoint = 2          # CatTablePosition catTableBottomLeft: x, y = bottom-left corner
            tbl.x, tbl.y = left, y0
            sym = projection_symbol_geometry(projection, x1 - 6.5 * 3.5 - 1.0, y0 + height + 3.0)
            for p, q in sym["lines"]:
                self._draw_line(doc, f, p, q, width="medium")
            self._draw_line(doc, f, sym["axis"][0], sym["axis"][1], width="thin", line="chain")
            for cx, cy, rr in sym["circles"]:
                circ = f.CreateClosedCircle(cx, cy, rr)
                self._style(doc, circ, width="medium")
        finally:
            self._restore_main_view(sheet)
        self._drawings.setdefault(doc.FullName, {})["title_h"] = max(
            float(self._drawings.get(doc.FullName, {}).get("title_h", 0)), height + 3.0 + sym_h + 3.0)
        return {"ok": True, "block_rect_mm": [left, y0, x1, y0 + height], "rows": [[c.lstrip("*") for c in r] for r in rows],
                "general_tolerance": std.general_tolerance_designation(a.get("general_tolerance", "m")),
                "projection_symbol": {"projection": projection, "cone_x_mm": [_r(v) for v in sym["cone_x"]],
                                      "circles_x_mm": _r(sym["circle_x"]), "circle_diameters_mm": [7.0, 3.5]},
                "warnings": warnings}

    # ------------------------------------------------------------------ export / info
    def _export(self, doc: Any, path: str) -> dict[str, Any]:
        if not path.lower().endswith(".pdf"):
            raise DraftingError("path must end with .pdf")
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)
        doc.ExportData(path, "pdf")
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise DraftingError(f"CATIA reported success but {path} was not written")
        sheet = self._sheet(doc)
        sw, sh = self._sheet_size(sheet)
        out: dict[str, Any] = {"ok": True, "path": path, "bytes": os.path.getsize(path), "sheet_mm": [sw, sh]}
        try:
            from catia_mcp.drawing import geometry as geo
            pymupdf = geo.require_pymupdf()
            pdf = pymupdf.open(path)
            try:
                rect = pdf[0].rect
                pw, ph = rect.width * geo.PT, rect.height * geo.PT
            finally:
                pdf.close()
            out["page_mm"] = [_r(pw, 2), _r(ph, 2)]
            out["page_matches_sheet"] = abs(pw - sw) <= 0.5 and abs(ph - sh) <= 0.5
        except Exception as e:      # PyMuPDF is optional
            out["page_mm"] = None
            out["page_matches_sheet"] = None
            out["note"] = f"page size not checked: {e}"
        return out

    def _export_pdf(self, a: dict[str, Any]) -> dict[str, Any]:
        return self._export(self._drawing_doc(a.get("drawing")), a["path"])

    def _close(self, a: dict[str, Any]) -> dict[str, Any]:
        doc = self._drawing_doc(a.get("drawing"))
        name, full = doc.Name, doc.FullName
        if a.get("save"):
            doc.Save()
        doc.Close()
        self._drawings.pop(full, None)
        for key in [k for k in self._vreg if k[0] == full]:
            del self._vreg[key]
        if self._last == full:
            self._last = None
        return {"ok": True, "closed": name, "saved": bool(a.get("save"))}

    def _info(self, a: dict[str, Any]) -> dict[str, Any]:
        doc = self._drawing_doc(a.get("drawing"))
        sheet = self._sheet(doc)
        w, h = self._sheet_size(sheet)
        return {
            "drawing": doc.Name,
            "sheet": {"size": self._sheet_name(sheet), "orientation": "landscape" if int(sheet.Orientation) == 1 else "portrait",
                      "width_mm": w, "height_mm": h},
            "scale": scale_label(float(sheet.Scale)), "projection": self._projection_name(sheet),
            "standard": {0: "ANSI", 1: "ISO", 2: "JIS"}.get(int(doc.Standard), str(doc.Standard)),
            "views": [self._view_summary(doc, v) for v in self._generative_views(sheet)],
            "dimensions": self._dim_list(doc, sheet),
        }

    # ------------------------------------------------------------------ closed-loop check
    def _expected_from_3d(self, doc: Any, source: Any, names: list[str]) -> dict[str, dict[str, Any]]:
        """Circles and outline box of each orthographic view, from the 3D model of the active body."""
        from catia_mcp.tools.measurement import MeasurementTools
        from catia_mcp.tools.part_design import PartDesignTools

        if not source.Name.lower().endswith(".catpart"):
            raise DraftingError("automatic 3D expectations need a CATPart source; for a CATProduct pass `expected`")
        faces = bb = None
        for attempt in range(3):       # the part tools use the ACTIVE document, which another client may change
            try:
                source.Activate()
                faces = json.loads(PartDesignTools(self.conn).execute("catia_list_faces", {}))["faces"]
                bb = json.loads(MeasurementTools(self.conn).execute("catia_get_bounding_box", {}))
                break
            except RuntimeError as e:
                if attempt == 2 or "not a Part" not in str(e):
                    raise
                time.sleep(1.0)
            finally:
                try:
                    doc.Activate()
                except Exception:
                    pass
        bbox = [bb["x"][0], bb["y"][0], bb["z"][0], bb["x"][1], bb["y"][1], bb["z"][1]]
        out: dict[str, dict[str, Any]] = {}
        sheet = self._sheet(doc)
        for n in names:
            info = self._vreg.get((doc.FullName, n)) or {}
            if not info.get("right") or info.get("kind") not in V.VIEW_KINDS:
                continue
            r, u = info["right"], info["up"]
            box = V.projected_box(V.box_corners(bbox), r, u)
            out[n] = {"circles": V.cylinder_circles(faces, r, u), "box": list(box)}
        del sheet
        return out

    def _check(self, a: dict[str, Any]) -> dict[str, Any]:
        doc = self._drawing_doc(a.get("drawing"))
        sheet = self._sheet(doc)
        d_tol = float(a.get("diameter_tol_mm", 0.02))
        c_tol = float(a.get("centre_tol_mm", 0.05))
        all_views = {v.Name: v for v in self._generative_views(sheet)}
        wanted = list(a.get("views") or [n for n in all_views
                                          if (self._vreg.get((doc.FullName, n)) or {}).get("kind") in V.VIEW_KINDS])
        for n in wanted:
            if n not in all_views:
                raise DraftingError(f"no view named {n!r} (views: {', '.join(all_views) or 'none'})")
        expected = dict(a.get("expected") or {})
        missing = [n for n in wanted if n not in expected]
        if missing:
            source = self._source_doc(doc, a.get("source"))
            expected.update(self._expected_from_3d(doc, source, missing))
        pdf = a.get("pdf")
        tmp = None
        if not pdf:
            tmp = tempfile.mkdtemp(prefix="catia_drawing_check_")
            pdf = os.path.join(tmp, "check.pdf")
            self._export(doc, pdf)
        results: dict[str, Any] = {}
        ok = True
        for n in wanted:
            exp = expected.get(n)
            if exp is None:
                results[n] = {"skipped": "no expectation for this view (isometric, section and detail views are not checked)"}
                continue
            view = all_views[n]
            frame = self._frame(doc, view)
            res = V.check_view_pdf(pdf, frame, expected_circles=exp.get("circles", []), expected_box=exp.get("box"),
                                   d_tol=d_tol, centre_tol=c_tol)
            if exp.get("box"):
                bx = self._size_box(view)
                ew = (exp["box"][2] - exp["box"][0]) * frame.scale
                eh = (exp["box"][3] - exp["box"][1]) * frame.scale
                res["paper_size"] = {"expected_mm": [_r(ew, 3), _r(eh, 3)],
                                     "measured_mm": [_r(bx[2] - bx[0], 3), _r(bx[3] - bx[1], 3)],
                                     "ok": abs((bx[2] - bx[0]) - ew) <= 0.05 and abs((bx[3] - bx[1]) - eh) <= 0.05}
                res["ok"] = bool(res["ok"] and res["paper_size"]["ok"])
            ok = ok and res["ok"]
            results[n] = res
        dim_audit = self._audit_dimensions(doc, sheet, pdf, all_views, d_tol)
        ok = ok and dim_audit["ok"]
        matched = sum(r.get("circles", {}).get("matched", 0) for r in results.values() if isinstance(r, dict))
        need = int(a.get("require_circles") or 0)
        if matched < need:
            ok = False
        report = {"ok": ok, "pdf": pdf, "circles_matched": matched, "views": results, "dimensions": dim_audit,
                  "tolerances": {"diameter_mm": d_tol, "centre_mm": c_tol}}
        if a.get("fail_on_mismatch") and not ok:
            bad = [n for n, r in results.items() if isinstance(r, dict) and r.get("ok") is False]
            raise DraftingError(f"drawing does not match the 3D model (views: {', '.join(bad) or 'none'}; dimensions ok: "
                                f"{dim_audit['ok']}). Report: {json.dumps(report, ensure_ascii=False)[:1500]}")
        return report

    def _audit_dimensions(self, doc: Any, sheet: Any, pdf: str, views: dict[str, Any], d_tol: float) -> dict[str, Any]:
        """Every diameter/radius dimension must match a circle really drawn in its view of the PDF."""
        items = []
        ok = True
        for name, view in views.items():
            dims = view.Dimensions
            if dims.Count == 0:
                continue
            frame = self._frame(doc, view)
            arcs = V.find_circles(pdf, 0, frame.region_paper(200, 200), frame.k(), frame.origin_paper(), min_r=1.0,
                                  include_arcs=True)
            for i in range(1, dims.Count + 1):
                dm = dims.Item(i)
                t = DIM_TYPE_NAMES.get(int(dm.DimType), "?")
                if not t.startswith(("diameter", "radius")):
                    continue
                val = float(dm.GetValue().Value)
                target = val if t.startswith("diameter") else 2 * val
                hit = next((c for c in arcs if abs(c["d"] - target) <= d_tol), None)
                items.append({"view": name, "dimension": dm.Name, "type": t, "value_mm": _r(val),
                              "matching_circle_mm": None if hit is None else _r(hit["d"]), "ok": hit is not None})
                ok = ok and hit is not None
        return {"ok": ok, "checked": items}
