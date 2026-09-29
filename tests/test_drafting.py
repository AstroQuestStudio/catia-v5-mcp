"""Offline tests of the drafting tools: pure logic, schemas and registration (never touches CATIA)."""

from __future__ import annotations

import json
import math

import pytest

from catia_mcp import standards as std
from catia_mcp.connection import CATIAConnection
from catia_mcp.tools import drafting as D


@pytest.fixture(scope="module")
def tools():
    return D.DraftingTools(CATIAConnection())


@pytest.fixture(scope="module")
def defs(tools):
    return {d["name"]: d for d in tools.get_tool_definitions()}


# ---------------------------------------------------------------------------- schemas / registration

EXPECTED_TOOLS = {
    "catia_drawing_create", "catia_drawing_add_view", "catia_drawing_generate_dimensions",
    "catia_drawing_add_dimension", "catia_drawing_add_centerlines", "catia_drawing_title_block",
    "catia_drawing_export_pdf", "catia_drawing_info", "catia_drawing_check", "catia_drawing_close",
}


def test_tool_names(defs):
    assert set(defs) == EXPECTED_TOOLS


def test_schemas_are_truthful(defs):
    for name, d in defs.items():
        json.dumps(d)                                   # serialisable
        assert d["description"].strip() and len(d["description"]) > 60, name
        schema = d["inputSchema"]
        assert schema["type"] == "object"
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            assert req in props, f"{name}: required {req!r} is not a property"


def test_units_and_frames_are_documented(defs):
    text = defs["catia_drawing_add_view"]["description"]
    for word in ("mm", "position", "section", "detail", "first angle"):
        assert word in text, word


def test_server_registers_the_module():
    from catia_mcp.server import CATIAMCPServer

    names = {d["name"] for d in CATIAMCPServer().tool_definitions()}
    assert EXPECTED_TOOLS <= names


def test_execute_unknown_tool(tools):
    with pytest.raises(ValueError):
        tools.execute("catia_drawing_nope", {})


def test_offline_execute_refuses_to_connect(tools):
    with pytest.raises(RuntimeError, match="OFFLINE"):
        tools.execute("catia_drawing_info", {})


# ---------------------------------------------------------------------------- request validation

def test_validate_sheet_request_ok():
    assert D.validate_sheet_request("A3", "1:2", "first_angle", None) == []


def test_validate_sheet_request_errors():
    with pytest.raises(D.DraftingError, match="sheet size"):
        D.validate_sheet_request("A5", "1:1", "first_angle", None)
    with pytest.raises(D.DraftingError, match="scale"):
        D.validate_sheet_request("A3", "banana", "first_angle", None)
    with pytest.raises(D.DraftingError, match="projection"):
        D.validate_sheet_request("A3", "1:1", "second_angle", None)
    with pytest.raises(D.DraftingError, match="orientation"):
        D.validate_sheet_request("A3", "1:1", "first_angle", "diagonal")


def test_validate_sheet_request_warnings():
    w = D.validate_sheet_request("A3", "1:3", "first_angle", "portrait")
    assert any("recommended series" in x for x in w)
    assert any("prescribes landscape" in x for x in w)


def test_scale_label():
    assert D.scale_label(0.5) == "1:2"
    assert D.scale_label(1.0) == "1:1"
    assert D.scale_label(5.0) == "5:1"
    assert D.scale_label(0.02) == "1:50"


def test_parse_point2():
    assert D.parse_point2([1, 2.5], "p") == (1.0, 2.5)
    for bad in (None, [1], [1, 2, 3], ["a", 1], True):
        with pytest.raises(D.DraftingError):
            D.parse_point2(bad, "p")


# ---------------------------------------------------------------------------- title block

FIELDS = {"legal_owner": "ACME", "identification_number": "BR-001", "date_of_issue": "2026-01-01",
          "sheet_number": "1", "number_of_sheets": "2", "title": "Bracket", "approval_person": "A",
          "creator": "B", "document_type": "Part drawing"}


def test_title_block_rows_contain_only_given_data():
    rows = D.title_block_rows(FIELDS, general_tolerance="m", scale="1:2", projection="first_angle", paper_size="A3")
    flat = [c for r in rows for c in r]
    assert rows[0][0].startswith("*") and rows[0][1] == "Bracket"          # title row spans the block
    assert "ISO 2768-m" in flat and "1:2" in flat and "1st angle" in flat
    assert "1/2" in flat                                                   # sheet number / number of sheets
    assert "ACME" in flat and "BR-001" in flat and "A3" in flat
    assert all(len(r) == 4 for r in rows)
    assert not any("None" in c for c in flat)


def test_title_block_rows_optional_fields_add_rows():
    base = D.title_block_rows(FIELDS)
    more = D.title_block_rows(dict(FIELDS, supplementary_title="Left hand", document_status="Released",
                                   technical_reference="TR-7", classification="steel"))
    assert len(more) == len(base) + 3      # supplementary row + two pairs


def test_title_block_width_is_iso():
    assert sum(D.TITLE_COLS_MM) == std.TITLE_BLOCK_WIDTH_MM


def test_mandatory_fields_match_the_standard():
    assert set(D.MANDATORY_TITLE_FIELDS) == {f.key for f in std.TITLE_BLOCK_FIELDS if f.mandatory}
    assert len(D.MANDATORY_TITLE_FIELDS) == 8


def test_title_block_validation_flags_missing():
    spec = {"title_block": {"title": "x"}}
    codes = {i.code for i in std.validate_drawing_spec(spec)}
    assert "title_block.missing_mandatory" in codes


# ---------------------------------------------------------------------------- symbol / dimensions / placement

@pytest.mark.parametrize("projection", ["first_angle", "third_angle"])
def test_projection_symbol_geometry(projection):
    g = D.projection_symbol_geometry(projection, 10.0, 20.0)
    (cone0, cone1), cx = g["cone_x"], g["circle_x"]
    assert cone1 - cone0 == pytest.approx(3 * 3.5)
    radii = sorted(r for _, _, r in g["circles"])
    assert radii == [pytest.approx(1.75), pytest.approx(3.5)]
    if projection == "first_angle":
        assert cx > cone1                    # end view to the right of the elevation
    else:
        assert cx < cone0                    # third angle: mirrored
    assert len(g["lines"]) == 4


def test_projection_symbol_rejects_unknown():
    with pytest.raises(D.DraftingError):
        D.projection_symbol_geometry("oblique", 0, 0)


def test_linear_value_and_line_position():
    p1, p2 = (0.0, 0.0), (30.0, 40.0)
    assert D.linear_value("horizontal", p1, p2) == 30
    assert D.linear_value("vertical", p1, p2) == 40
    assert D.linear_value("aligned", p1, p2) == 50
    assert D.dimension_line_position("horizontal", p1, p2, 10, None) == (15.0, -10.0)
    assert D.dimension_line_position("horizontal", p1, p2, 10, "above") == (15.0, 50.0)
    assert D.dimension_line_position("vertical", p1, p2, 10, None) == (40.0, 20.0)
    assert D.dimension_line_position("vertical", p1, p2, 10, "left") == (-10.0, 20.0)
    x, y = D.dimension_line_position("aligned", p1, p2, 10, "left")   # left of the direction (0.6, 0.8): (-0.8, 0.6)
    assert (x, y) == pytest.approx((15 - 8, 20 + 6))
    x, y = D.dimension_line_position("aligned", p1, p2, 10, "right")
    assert (x, y) == pytest.approx((15 + 8, 20 - 6))
    with pytest.raises(D.DraftingError):
        D.dimension_line_position("aligned", p1, p1, 10, None)


def test_rects_overlap():
    a = (0, 0, 10, 10)
    assert D.rects_overlap(a, (5, 5, 15, 15))
    assert not D.rects_overlap(a, (10, 0, 20, 10))        # touching is not overlapping
    assert D.rects_overlap(a, (10, 0, 20, 10), gap=1)
    assert not D.rects_overlap(a, (11, 11, 20, 20))


def test_find_free_spot_prefers_then_scans():
    area = (0, 0, 100, 100)
    occ = [(0, 0, 100, 40)]                                # bottom band taken
    assert D.find_free_spot((20, 20), area, occ, [(50, 20), (50, 80)], gap=0) == (50, 80)
    spot = D.find_free_spot((20, 20), area, occ, [], gap=5)
    assert spot is not None and spot[1] - 10 >= 45 - 1e-9
    assert D.find_free_spot((200, 20), area, occ, [], gap=0) is None
    assert D.find_free_spot((20, 70), area, occ, [], gap=0) is None


def test_view_kind_constants_match_type_library():
    # values read from the DRAFTINGITF type library (enum CatProjViewType / CatPaperSize / CatDimType)
    assert D.PROJ_VIEW_CODE == {"right": 0, "left": 1, "top": 2, "bottom": 3, "rear": 4}
    assert D.PAPER_CODE == {"A0": 2, "A1": 3, "A2": 4, "A3": 5, "A4": 6}
    assert D.PROJECTION_CODE == {"first_angle": 0, "third_angle": 1}
    assert D.ORIENTATION_CODE == {"portrait": 0, "landscape": 1}
    assert D.DIM_TYPE == {"distance": 0, "diameter": 9, "radius": 5}
    assert D.DIM_LINE_REP["horizontal"] == 1 and D.DIM_LINE_REP["vertical"] == 2


def test_dimension_math_is_consistent_with_scale():
    # a 10 mm paper offset on a 1:2 view is 20 mm in model units
    off = 10 / 0.5
    assert D.dimension_line_position("horizontal", (0, 0), (10, 0), off, None) == (5.0, -20.0)
    assert math.isclose(off, 20.0)
