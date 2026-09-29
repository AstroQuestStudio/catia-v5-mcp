"""Offline tests of catia_mcp.standards: values are those of the cited standards."""

import math

import pytest

from catia_mcp import standards as st

# ---------------------------------------------------------------- ISO 5457:1999 sheets

@pytest.mark.parametrize("name,short,long_,untrimmed", [
    ("A0", 841, 1189, (880, 1230)),
    ("A1", 594, 841, (625, 880)),
    ("A2", 420, 594, (450, 625)),
    ("A3", 297, 420, (330, 450)),
    ("A4", 210, 297, (240, 330)),
])
def test_sheet_sizes_table1(name, short, long_, untrimmed):
    s = st.get_sheet(name)
    assert (s.short, s.long) == (short, long_)
    assert s.untrimmed == untrimmed


@pytest.mark.parametrize("name,space", [
    # ISO 5457:1999 Table 1: drawing space (a2 x b2, short x long) in the prescribed orientation
    ("A0", (1159, 821)), ("A1", (811, 574)), ("A2", (564, 400)), ("A3", (390, 277)), ("A4", (180, 277)),
])
def test_drawing_space_matches_table1(name, space):
    assert st.drawing_space(name) == space


def test_frame_20_left_10_elsewhere():
    assert st.frame_rect("A3") == (20, 10, 410, 287)
    assert st.frame_rect("A4") == (20, 10, 200, 287)
    assert st.get_sheet("A4").orientation == "portrait"
    assert st.get_sheet("A2").orientation == "landscape"


def test_grid_field_counts_table2():
    assert st.SHEETS["A0"].grid_fields == (24, 16)
    assert st.SHEETS["A3"].grid_fields == (8, 6)
    assert st.SHEETS["A4"].grid_fields == (6, 4)


def test_unknown_sheet():
    with pytest.raises(ValueError):
        st.get_sheet("A5")


def test_title_block_180_wide_bottom_right():
    assert st.title_block_rect("A3", 40) == (230, 10, 410, 50)


# ---------------------------------------------------------------- ISO 5455:1979 scales

def test_preferred_scale_list_is_the_standard_one():
    assert [s.label for s in st.PREFERRED_SCALES] == [
        "50:1", "20:1", "10:1", "5:1", "2:1", "1:1", "1:2", "1:5", "1:10", "1:20", "1:50",
        "1:100", "1:200", "1:500", "1:1000", "1:2000", "1:5000", "1:10000"]


def test_parse_scale():
    assert st.parse_scale("1:2").ratio == 0.5
    assert st.parse_scale("2:1").ratio == 2
    assert st.parse_scale(0.5).label == "1:2"
    assert st.parse_scale((5, 1)).label == "5:1"
    for bad in ("1-2", "0:1", "", None, True):
        with pytest.raises(ValueError):
            st.parse_scale(bad)


def test_preferred_scale_modes():
    assert st.preferred_scale(0.3).label == "1:5"            # fit: largest scale <= 0.3
    assert st.preferred_scale(0.3, "up").label == "1:2"
    assert st.preferred_scale(0.3, "nearest").label == "1:5"
    assert st.preferred_scale(1).label == "1:1"
    assert st.preferred_scale(0.5).label == "1:2"
    assert st.preferred_scale(0.49).label == "1:5"
    assert st.preferred_scale(3).label == "2:1"
    assert st.preferred_scale(100).label == "50:1"
    with pytest.raises(ValueError):
        st.preferred_scale(1e-6)
    assert st.preferred_scale(1e-6, extended=True).label == "1:1000000"


def test_is_preferred_scale():
    assert st.is_preferred_scale("1:2")
    assert not st.is_preferred_scale("1:3")
    assert not st.is_preferred_scale("1:20000")
    assert st.is_preferred_scale("1:20000", extended=True)     # power of ten of 1:2
    assert not st.is_preferred_scale("1:3", extended=True)


# ---------------------------------------------------------------- choose_sheet

def test_choose_sheet_small_part_a4_full_size():
    c = st.choose_sheet(50, 30)
    assert c.sheet.name == "A4" and c.scale.label == "1:1"
    assert c.orientation == "portrait"


def test_choose_sheet_grows_with_part():
    c = st.choose_sheet(400, 250, n_views=1, margins=10, title_block_height_mm=40, min_scale="1:1")
    # 1:1 needs 420 x 270 (+ 40 strip): A2 drawing space is 564 x 400 -> A2; A3 is too small at 1:1
    assert c.sheet.name == "A2" and c.scale.label == "1:1"


def test_choose_sheet_min_scale_pushes_to_bigger_sheet():
    small = st.choose_sheet(300, 200, min_scale="1:10")
    strict = st.choose_sheet(300, 200, min_scale="1:2")
    assert st.SHEETS[strict.sheet.name].short >= st.SHEETS[small.sheet.name].short
    assert strict.scale.ratio >= 0.5 or strict.notes


def test_choose_sheet_result_actually_fits():
    for w, h, n in [(120, 80, 3), (700, 300, 2), (55, 20, 4), (1500, 900, 1)]:
        c = st.choose_sheet(w, h, n)
        assert c.required_mm[0] <= c.available_mm[0] + 1e-9
        assert c.required_mm[1] <= c.available_mm[1] + 1e-9
        assert st.is_preferred_scale(c.scale)


def test_choose_sheet_max_scale_enlarges_small_parts():
    assert st.choose_sheet(10, 10, max_scale="10:1").scale.label == "10:1"


def test_choose_sheet_too_big():
    with pytest.raises(ValueError):
        st.choose_sheet(1e9, 1e9)


def test_choose_sheet_view_sizes_and_bad_input():
    c = st.choose_sheet(0, 0, view_sizes=[(100, 60), (100, 40), (60, 40)])
    assert c.cols == 2 and c.rows == 2
    with pytest.raises(ValueError):
        st.choose_sheet(-1, 10)


# ---------------------------------------------------------------- ISO 128 lines

def test_line_widths_series_and_ratio():
    assert st.LINE_WIDTHS == (0.13, 0.18, 0.25, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0)
    for a, b in zip(st.LINE_WIDTHS, st.LINE_WIDTHS[1:]):
        assert b / a == pytest.approx(math.sqrt(2), rel=0.08)   # nominal values are rounded


def test_line_groups_wide_is_twice_narrow():
    assert st.LINE_GROUPS[0.5] == (0.5, 0.25)
    assert st.LINE_GROUPS[0.7] == (0.7, 0.35)
    assert st.LINE_GROUPS[0.25] == (0.25, 0.13)
    for wide, narrow in st.LINE_GROUPS.values():
        assert wide / narrow == pytest.approx(2, rel=0.08)
        assert st.is_standard_line_width(wide) and st.is_standard_line_width(narrow)
    assert st.PREFERRED_LINE_GROUPS == (0.5, 0.7)
    assert st.line_pair_for_group(0.7) == (0.7, 0.35)


def test_line_usage_types():
    assert st.line_type_for_usage("visible_outline") == "01.2"
    assert st.line_type_for_usage("hidden_outline") == "02.1"
    assert st.line_type_for_usage("centre_line") == "04.1"
    assert st.line_type_for_usage("cutting_plane") == "04.2"
    assert st.line_type_for_usage("dimension_line") == "01.1"
    assert st.line_type_for_usage("hatching") == "01.1"
    assert st.line_type_for_usage("adjacent_part") == "05.1"
    assert st.LINE_TYPES["01.2"].wide and not st.LINE_TYPES["01.1"].wide
    with pytest.raises(ValueError):
        st.line_type_for_usage("nonsense")


# ---------------------------------------------------------------- ISO 2768-1 tolerances

# Table 1: rows (size, f, m, c, v)
LINEAR_ROWS = [
    (0.5, 0.05, 0.1, 0.2, None), (3, 0.05, 0.1, 0.2, None),
    (3.1, 0.05, 0.1, 0.3, 0.5), (6, 0.05, 0.1, 0.3, 0.5),
    (6.1, 0.1, 0.2, 0.5, 1.0), (30, 0.1, 0.2, 0.5, 1.0),
    (31, 0.15, 0.3, 0.8, 1.5), (120, 0.15, 0.3, 0.8, 1.5),
    (121, 0.2, 0.5, 1.2, 2.5), (400, 0.2, 0.5, 1.2, 2.5),
    (401, 0.3, 0.8, 2.0, 4.0), (1000, 0.3, 0.8, 2.0, 4.0),
    (1001, 0.5, 1.2, 3.0, 6.0), (2000, 0.5, 1.2, 3.0, 6.0),
    (2001, None, 2.0, 4.0, 8.0), (4000, None, 2.0, 4.0, 8.0),
]


@pytest.mark.parametrize("size,f,m,c,v", LINEAR_ROWS)
def test_general_tolerance_linear_table1(size, f, m, c, v):
    for cls, want in zip("fmcv", (f, m, c, v)):
        if want is None:
            with pytest.raises(ValueError):
                st.general_tolerance(size, cls)
        else:
            assert st.general_tolerance(size, cls) == want


def test_general_tolerance_default_is_m_and_class_forms():
    assert st.general_tolerance(50) == 0.3
    assert st.general_tolerance(50, "M") == 0.3
    assert st.general_tolerance(50, "ISO 2768-m") == 0.3
    assert st.general_tolerance_designation("m") == "ISO 2768-m"


def test_general_tolerance_range_errors():
    with pytest.raises(ValueError):
        st.general_tolerance(0.4)
    with pytest.raises(ValueError):
        st.general_tolerance(4001)
    with pytest.raises(ValueError):
        st.general_tolerance(10, "x")


def test_general_tolerance_broken_edges_table2():
    g = lambda s, c: st.general_tolerance(s, c, "broken_edge")  # noqa: E731
    assert (g(1, "f"), g(1, "m"), g(1, "c"), g(1, "v")) == (0.2, 0.2, 0.4, 0.4)
    assert (g(5, "f"), g(5, "m"), g(5, "c"), g(5, "v")) == (0.5, 0.5, 1.0, 1.0)
    assert (g(10, "f"), g(10, "m"), g(10, "c"), g(10, "v")) == (1.0, 1.0, 2.0, 2.0)
    assert g(3, "c") == 0.4 and g(3.01, "c") == 1.0


def test_general_tolerance_angular_table3():
    a = lambda s, c: st.general_tolerance(s, c, "angular")  # noqa: E731
    assert a(10, "f") == 1.0 and a(10, "v") == 3.0 and a(10, "c") == 1.5
    assert a(30, "m") == 0.5 and a(30, "v") == 2.0
    assert a(100, "f") == pytest.approx(20 / 60) and a(100, "c") == 0.5
    assert a(200, "m") == pytest.approx(10 / 60) and a(200, "v") == 0.5
    assert a(500, "f") == pytest.approx(5 / 60) and a(500, "v") == pytest.approx(20 / 60)


# ---------------------------------------------------------------- ISO 5456-2 projection

def test_view_sides_first_and_third_angle():
    assert st.expected_view_side("first_angle", "top") == "below"
    assert st.expected_view_side("first_angle", "bottom") == "above"
    assert st.expected_view_side("first_angle", "left") == "right"
    assert st.expected_view_side("first_angle", "right") == "left"
    assert st.expected_view_side("third_angle", "top") == "above"
    assert st.expected_view_side("third_angle", "bottom") == "below"
    assert st.expected_view_side("third_angle", "left") == "left"
    assert st.expected_view_side("third_angle", "right") == "right"
    assert st.expected_view_side("first_angle", "front") is None
    with pytest.raises(ValueError):
        st.expected_view_side("second_angle", "top")


# ---------------------------------------------------------------- ISO 261 threads

@pytest.mark.parametrize("d,p", [
    (1, 0.25), (1.6, 0.35), (2, 0.4), (2.5, 0.45), (3, 0.5), (4, 0.7), (5, 0.8), (6, 1), (8, 1.25),
    (10, 1.5), (12, 1.75), (16, 2), (20, 2.5), (24, 3), (30, 3.5), (36, 4), (42, 4.5), (48, 5),
    (56, 5.5), (64, 6),
])
def test_coarse_pitches(d, p):
    assert st.COARSE_PITCH[d] == p
    assert st.thread_designation(d) == f"M{d:g}"


def test_thread_designation_forms():
    assert st.thread_designation(8) == "M8"
    assert st.thread_designation(8.0) == "M8"
    assert st.thread_designation("M8") == "M8"
    assert st.thread_designation("8") == "M8"
    assert st.thread_designation(8, 1) == "M8x1"
    assert st.thread_designation("M8x1") == "M8x1"
    assert st.thread_designation("M8 x 1") == "M8x1"
    assert st.thread_designation(8, 1.25) == "M8"          # coarse pitch is implied
    assert st.thread_designation(10, 1.25) == "M10x1.25"
    assert st.thread_designation(2.5) == "M2.5"


def test_thread_designation_rejects():
    for args in [(7.5,), (65,), (8, 1.5), (8, 0.9), (8, -1), ("M", ), ("X8",)]:
        with pytest.raises(st.ThreadError):
            st.thread_designation(*args)
    assert issubclass(st.ThreadError, ValueError)


def test_thread_info_basic_dimensions():
    i = st.thread_info(8)          # M8: P = 1.25
    assert i.coarse and i.pitch == 1.25
    assert i.height_h == pytest.approx(0.866025 * 1.25, abs=1e-5)
    assert i.pitch_diameter == pytest.approx(7.188, abs=1e-3)   # 8 - 0.649519*1.25
    assert i.minor_diameter == pytest.approx(6.647, abs=1e-3)   # 8 - 1.082532*1.25
    f = st.thread_info(8, 1)
    assert not f.coarse and f.selected_fine
    assert not st.thread_info(10, 0.5).selected_fine            # in the series, not confirmed selected


def test_parse_thread_designation():
    assert st.parse_thread_designation("M8x1") == (8.0, 1.0)
    assert st.parse_thread_designation("M8") == (8.0, None)
    assert st.parse_thread_designation("M2,5x0,35") == (2.5, 0.35)


# ---------------------------------------------------------------- ISO 7200 title block

def test_title_block_mandatory_fields():
    mandatory = {f.key for f in st.TITLE_BLOCK_FIELDS if f.mandatory}
    assert mandatory == {"legal_owner", "identification_number", "date_of_issue", "sheet_number",
                         "title", "approval_person", "creator", "document_type"}
    optional = {f.key for f in st.TITLE_BLOCK_FIELDS if not f.mandatory}
    assert {"revision_index", "number_of_sheets", "language_code", "supplementary_title"} <= optional
    assert st.TITLE_BLOCK_WIDTH_MM == 180


# ---------------------------------------------------------------- validate_drawing_spec

def _good_spec():
    return {
        "sheet": {"size": "A3", "orientation": "landscape"},
        "scale": "1:2",
        "projection": "first_angle",
        "general_tolerance": "m",
        "unit": "mm",
        "views": [
            {"id": "front", "kind": "front", "position": [120, 180], "width_mm": 100, "height_mm": 60},
            {"id": "top", "kind": "top", "position": [120, 110], "width_mm": 100, "height_mm": 40},
            {"id": "left", "kind": "left", "position": [210, 180], "width_mm": 40, "height_mm": 60},
        ],
        "lines": [
            {"usage": "visible_outline", "width_mm": 0.5},
            {"usage": "hidden_outline", "width_mm": 0.25},
            {"usage": "dimension_line", "width_mm": 0.25},
        ],
        "features": [{"id": "h1", "kind": "hole"}],
        "dimensions": [
            {"id": "d1", "view": "front", "feature": "h1", "type": "diameter", "value": 8,
             "drawn_length_mm": 4.0, "thread": "M8"},
        ],
        "title_block": {
            "legal_owner": "ACME", "identification_number": "P-0001", "date_of_issue": "2026-01-01",
            "sheet_number": 1, "number_of_sheets": 1, "title": "Bracket",
            "approval_person": "A. B.", "creator": "C. D.", "document_type": "Part drawing",
            "height_mm": 40,
        },
    }


def _codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def test_good_spec_has_no_issues():
    issues = st.validate_drawing_spec(_good_spec())
    assert issues == [], [str(i) for i in issues]


def test_empty_spec_has_no_errors():
    assert _codes(st.validate_drawing_spec({}), "error") == set()


def test_issue_carries_reference_and_serialises():
    spec = _good_spec()
    spec["sheet"]["size"] = "A5"
    (issue,) = [i for i in st.validate_drawing_spec(spec) if i.code == "sheet.unknown_size"]
    assert issue.severity == "error" and "ISO 5457:1999" in issue.reference
    assert issue.to_dict()["code"] == "sheet.unknown_size"
    assert "ISO 5457:1999" in str(issue)


def test_sheet_orientation_warning():
    spec = _good_spec()
    spec["sheet"]["size"] = "A4"
    spec["sheet"]["orientation"] = "landscape"
    assert "sheet.orientation" in _codes(st.validate_drawing_spec(spec), "warning")


def test_scale_checks():
    spec = _good_spec()
    spec["scale"] = "1:3"
    assert "scale.not_preferred" in _codes(st.validate_drawing_spec(spec), "warning")
    spec["scale"] = "abc"
    assert "scale.invalid" in _codes(st.validate_drawing_spec(spec), "error")
    spec["scale"] = "1:20000"     # power-of-ten extension: still a warning, message says allowed
    (w,) = [i for i in st.validate_drawing_spec(spec) if i.code == "scale.not_preferred"]
    assert "extension" in w.message
    del spec["scale"]
    assert "scale.missing" in _codes(st.validate_drawing_spec(spec))


def test_view_scale_needs_label():
    spec = _good_spec()
    spec["views"][0]["scale"] = "1:1"
    spec["views"][0]["position"] = [100, 180]
    assert "scale.view_label_missing" in _codes(st.validate_drawing_spec(spec))
    spec["views"][0]["scale_label"] = True
    assert "scale.view_label_missing" not in _codes(st.validate_drawing_spec(spec))


def test_projection_missing_and_invalid():
    spec = _good_spec()
    del spec["projection"]
    assert "projection.missing" in _codes(st.validate_drawing_spec(spec), "error")
    spec["projection"] = "second_angle"
    assert "projection.invalid" in _codes(st.validate_drawing_spec(spec), "error")


def test_projection_arrangement_first_vs_third_angle():
    spec = _good_spec()          # laid out in first angle: top under front, left on the right
    assert "projection.view_arrangement" not in _codes(st.validate_drawing_spec(spec))
    spec["projection"] = "third_angle"
    issues = st.validate_drawing_spec(spec)
    bad = [i for i in issues if i.code == "projection.view_arrangement"]
    assert len(bad) == 2 and all(i.severity == "error" for i in bad)
    assert all("ISO 5456-2:1996" in i.reference for i in bad)


def test_projection_alignment_warning():
    spec = _good_spec()
    spec["views"][1]["position"] = [135, 110]
    assert "projection.view_alignment" in _codes(st.validate_drawing_spec(spec), "warning")


def test_view_outside_frame_and_overlap():
    spec = _good_spec()
    spec["views"][0]["position"] = [15, 180]
    assert "view.outside_frame" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    spec["views"][1]["position"] = [120, 175]
    assert "view.overlap" in _codes(st.validate_drawing_spec(spec), "error")


def test_view_overlapping_title_block():
    spec = _good_spec()
    spec["views"][2]["position"] = [340, 40]
    codes = _codes(st.validate_drawing_spec(spec), "error")
    assert "view.overlaps_title_block" in codes


def test_line_rules():
    spec = _good_spec()
    spec["lines"][0]["width_mm"] = 0.6
    assert "line.width_not_standard" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    spec["lines"][1]["type"] = "04.1"          # hidden outline must be 02.1
    assert "line.wrong_type" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    spec["lines"][1]["width_mm"] = 0.35        # 0.5 / 0.35 is not 2:1
    spec["lines"][2]["width_mm"] = 0.35
    assert "line.ratio" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    spec["lines"].append({"usage": "visible_edge", "width_mm": 0.7})
    assert "line.inconsistent_widths" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    for ln, w in zip(spec["lines"], (1.0, 0.5, 0.5)):
        ln["width_mm"] = w
    assert "line.group_not_preferred" in _codes(st.validate_drawing_spec(spec), "warning")


def test_dimension_redundancy_and_auxiliary():
    spec = _good_spec()
    spec["dimensions"].append({"id": "d2", "view": "top", "feature": "h1", "type": "diameter", "value": 8})
    assert "dimension.redundant" in _codes(st.validate_drawing_spec(spec), "error")
    spec["dimensions"][1]["auxiliary"] = True         # repeat as (8): allowed
    assert "dimension.redundant" not in _codes(st.validate_drawing_spec(spec))


def test_dimension_unknown_view_and_units():
    spec = _good_spec()
    spec["dimensions"][0]["view"] = "nope"
    assert "dimension.unknown_view" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    spec["dimensions"].append({"id": "a", "view": "front", "type": "angular", "value": 45})
    assert "dimension.angular_unit_missing" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    spec["dimensions"][0]["unit"] = "in"
    assert "dimension.mixed_unit" in _codes(st.validate_drawing_spec(spec), "warning")


def test_dimension_hidden_inside_and_decimal_marker():
    spec = _good_spec()
    d = spec["dimensions"][0]
    d.update(on_hidden=True, inside_contour=True, text="8.5")
    codes = _codes(st.validate_drawing_spec(spec), "warning")
    assert {"dimension.on_hidden", "dimension.inside_contour", "dimension.decimal_marker"} <= codes


def test_dimension_out_of_scale():
    spec = _good_spec()
    spec["dimensions"][0]["drawn_length_mm"] = 8.0     # should be 4.0 at 1:2
    assert "dimension.out_of_scale" in _codes(st.validate_drawing_spec(spec), "error")
    spec["dimensions"][0]["underlined"] = True
    assert "dimension.out_of_scale" not in _codes(st.validate_drawing_spec(spec))


def test_dimension_outside_frame_and_missing_dimension():
    spec = _good_spec()
    spec["dimensions"][0]["position"] = [5, 5]
    assert "dimension.outside_frame" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    spec["features"].append({"id": "h2", "kind": "hole"})
    assert "dimension.feature_undimensioned" in _codes(st.validate_drawing_spec(spec), "warning")


def test_thread_checks_in_dimensions():
    spec = _good_spec()
    spec["dimensions"][0]["thread"] = "M7.5"
    assert "thread.invalid" in _codes(st.validate_drawing_spec(spec), "error")
    spec["dimensions"][0]["thread"] = "M8x1.25"
    assert "thread.coarse_pitch_written" in _codes(st.validate_drawing_spec(spec), "warning")


def test_general_tolerance_checks():
    spec = _good_spec()
    spec["general_tolerance"] = "z"
    assert "tolerance.class_invalid" in _codes(st.validate_drawing_spec(spec), "error")
    spec = _good_spec()
    del spec["general_tolerance"]
    assert "tolerance.general_missing" in _codes(st.validate_drawing_spec(spec), "warning")
    spec["dimensions"][0]["tolerance"] = "+0.1/0"     # individually toleranced: nothing to warn
    assert "tolerance.general_missing" not in _codes(st.validate_drawing_spec(spec))


def test_title_block_checks():
    spec = _good_spec()
    del spec["title_block"]["creator"]
    spec["title_block"]["title"] = "  "
    issues = st.validate_drawing_spec(spec)
    miss = [i for i in issues if i.code == "title_block.missing_mandatory"]
    assert {i.path for i in miss} == {"title_block.creator", "title_block.title"}
    assert all(i.severity == "error" and "ISO 7200:2004" in i.reference for i in miss)

    spec = _good_spec()
    spec["title_block"]["title"] = "x" * 40
    spec["title_block"]["colour"] = "red"
    spec["title_block"]["width_mm"] = 200
    spec["title_block"]["sheet_number"] = 3
    codes = _codes(st.validate_drawing_spec(spec))
    assert {"title_block.too_long", "title_block.unknown_field", "title_block.too_wide",
            "title_block.sheet_number"} <= codes


def test_missing_title_block_is_reported():
    spec = _good_spec()
    del spec["title_block"]
    assert "title_block.missing" in _codes(st.validate_drawing_spec(spec), "warning")


def test_validator_rejects_non_dict():
    with pytest.raises(TypeError):
        st.validate_drawing_spec([])  # type: ignore[arg-type]
