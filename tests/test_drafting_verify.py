"""Offline tests of catia_mcp.drawing.verify (closed-loop check helpers). PDF cases use PyMuPDF if installed."""

from __future__ import annotations

import math
import random

import pytest

from catia_mcp.drawing import verify as V
from catia_mcp.standards import PREFERRED_SCALES

PT = 25.4 / 72.0


# ---------------------------------------------------------------------------- 3D -> view maths (measured live)

# A bracket with holes on three different axes was drawn in CATIA V5 R19 and read back from the PDF;
# these are the (u, w) each hole landed at (docs/DRAFTING.md, experiment 5).
POINT_B = (30.0, 5.0, 25.0)      # hole along X
POINT_A = (-15.0, -8.0, 8.0)     # hole along Z
POINT_C = (-22.0, 20.0, 4.0)     # hole along Y


@pytest.mark.parametrize("kind,point,expected", [
    ("front", POINT_C, (-22.0, 4.0)),
    ("right", POINT_B, (5.0, 25.0)),
    ("left", POINT_B, (-5.0, 25.0)),
    ("top", POINT_A, (-15.0, -8.0)),
    ("bottom", POINT_A, (-15.0, 8.0)),
    ("rear", POINT_C, (22.0, 4.0)),
])
def test_view_axes_match_live_measurements(kind, point, expected):
    h, v = V.front_axes("-Y")
    r, u = V.view_axes(kind, h, v)
    assert V.project(point, r, u) == pytest.approx(expected)


def test_view_axes_unknown_kind():
    with pytest.raises(ValueError, match="unknown view kind"):
        V.view_axes("side", (1, 0, 0), (0, 0, 1))


@pytest.mark.parametrize("view_from", list(V.VIEW_FROM))
def test_front_axes_normal_points_at_the_viewer(view_from):
    h, v = V.front_axes(view_from)
    n = V._cross(h, v)
    axis = {"X": 0, "Y": 1, "Z": 2}[view_from[1]]
    sign = 1 if view_from[0] == "+" else -1
    expected = [0, 0, 0]
    expected[axis] = sign
    assert n == tuple(expected)
    assert sum(a * b for a, b in zip(h, v)) == 0            # orthogonal


def test_front_axes_accepts_short_names_and_rejects_junk():
    assert V.front_axes("z") == V.front_axes("+Z")
    with pytest.raises(ValueError, match="view_from"):
        V.front_axes("up")


def test_isometric_axes_are_orthonormal_and_between_the_faces():
    h, v = V.front_axes("-Y")
    r, u = V.isometric_axes(h, v)
    assert math.isclose(sum(x * x for x in r), 1) and math.isclose(sum(x * x for x in u), 1)
    assert abs(sum(a * b for a, b in zip(r, u))) < 1e-12
    n = V._cross(r, u)                                         # towards the viewer: +X, -Y, +Z corner
    assert n[0] > 0 and n[1] < 0 and n[2] > 0
    assert u[2] > 0                                            # Z still up on the sheet


def test_view_frame_conversions():
    fr = V.ViewFrame(100.0, 200.0, 0.5, (90.0, 180.0), 297.0)
    assert fr.origin_paper() == (90.0, 117.0)                  # y flipped to the PDF frame
    assert fr.k() == 2.0
    assert fr.region_paper(10, 5, margin=0) == [90.0, 92.0, 110.0, 102.0]


def test_projected_box_and_size():
    bbox = (-30, -20, 0, 30, 20, 38)
    r, u = V.view_axes("right", *V.front_axes("-Y"))
    assert V.view_size_from_bbox(bbox, r, u) == pytest.approx((40.0, 38.0))
    assert V.projected_box(V.box_corners(bbox), r, u) == pytest.approx((-20.0, 0.0, 20.0, 38.0))


# ---------------------------------------------------------------------------- layout

def test_plan_positions_first_and_third_angle():
    sizes = {"front": (60, 38), "top": (60, 40), "right": (40, 38)}
    first, (cw, ch) = V.plan_positions(sizes, "first_angle", 1.0, 20.0)
    assert first["top"][1] < first["front"][1]                 # top view BELOW the front view
    assert first["right"][0] < first["front"][0]               # right view on its LEFT
    assert first["top"][0] == first["front"][0] and first["right"][1] == first["front"][1]   # aligned
    third, _ = V.plan_positions(sizes, "third_angle", 1.0, 20.0)
    assert third["top"][1] > third["front"][1]
    assert third["right"][0] > third["front"][0]
    assert cw == pytest.approx(60 + 20 + 40) and ch == pytest.approx(38 + 20 + 40)


def test_plan_positions_gap_and_rear():
    pos, _ = V.plan_positions({"front": (10, 10), "left": (10, 10), "rear": (10, 10)}, "first_angle", 1.0, 5.0)
    assert pos["left"][0] - pos["front"][0] == pytest.approx(15)        # 10/2 + 5 + 10/2
    assert pos["rear"][0] > pos["left"][0]
    with pytest.raises(ValueError):
        V.plan_positions({"top": (1, 1)}, "first_angle", 1.0)
    with pytest.raises(ValueError):
        V.plan_positions({"front": (1, 1)}, "sideways", 1.0)


def test_plan_layout_picks_smallest_sheet_and_biggest_scale():
    p = V.plan_layout({"front": (60, 38), "top": (60, 40), "right": (40, 38)})
    assert p.fits and p.sheet == "A4" and p.scale == "1:1"
    x0, y0, x1, y1 = p.free_area
    for k, (cx, cy) in p.positions.items():
        assert x0 <= cx <= x1 and y0 <= cy <= y1


def test_plan_layout_grows_when_needed():
    p = V.plan_layout({"front": (400, 300), "top": (400, 200), "right": (200, 300)}, "third_angle")
    assert p.fits and p.scale in {"1:2", "1:1"} and p.sheet in {"A1", "A2"}
    small = V.plan_layout({"front": (400, 300), "top": (400, 200), "right": (200, 300)}, "third_angle", min_scale="1:5")
    assert small.fits and small.sheet != "A0"


def test_plan_layout_fixed_choices_are_never_shrunk():
    p = V.plan_layout({"front": (300, 200)}, sheet="A4", scale="1:1")
    assert not p.fits and p.sheet == "A4" and p.scale == "1:1" and p.notes
    q = V.plan_layout({"front": (300, 200)}, sheet="A3", scale="1:2")
    assert q.fits and q.scale == "1:2"


def test_plan_layout_impossible_says_so():
    p = V.plan_layout({"front": (5000, 5000)}, min_scale="1:2")
    assert not p.fits and p.notes


def test_plan_layout_uses_only_recommended_scales():
    for w in (77, 130, 233, 411, 705):
        p = V.plan_layout({"front": (w, w * 0.7)})
        assert p.scale in {s.label for s in PREFERRED_SCALES}


# ---------------------------------------------------------------------------- expected data from the 3D side

FACES = [
    {"index": 1, "type": "plane", "cog": [0, 0, 0]},
    {"index": 9, "type": "cylinder", "radius": 5.0, "axis": {"direction": [-1, 0, 0], "point": [29.9, 5.0, 25.0]}},
    {"index": 10, "type": "cylinder", "radius": 4.0, "axis": {"direction": [0, 0, -1], "point": [-15.0, -8.0, 7.9]}},
    {"index": 11, "type": "cylinder", "radius": 2.0, "axis": {"direction": [0, -1, 0], "point": [-22.0, 19.9, 4.0]}},
    {"index": 12, "type": "cylinder", "radius": 2.0, "axis": {"direction": [0, 1, 0], "point": [-22.0, -19.9, 4.0]}},  # other end
    {"index": 13, "type": "cone", "radius": 1.0, "axis": {"direction": [0, 0, 1], "point": [0, 0, 0]}},
]


def test_cylinder_circles_per_view():
    h, v = V.front_axes("-Y")
    front = V.cylinder_circles(FACES, *V.view_axes("front", h, v))
    assert [(c["cx"], c["cy"], c["d"]) for c in front] == [(-22.0, 4.0, 4.0)]          # merged, cone ignored
    top = V.cylinder_circles(FACES, *V.view_axes("top", h, v))
    assert [(c["cx"], c["cy"], c["d"]) for c in top] == [(-15.0, -8.0, 8.0)]
    right = V.cylinder_circles(FACES, *V.view_axes("right", h, v))
    assert [(c["cx"], c["cy"], c["d"]) for c in right] == [(5.0, 25.0, 10.0)]
    iso = V.cylinder_circles(FACES, *V.isometric_axes(h, v))
    assert iso == []                                                                     # nothing is round in an iso view


# ---------------------------------------------------------------------------- comparison

def test_match_circles_pairs_by_centre_not_by_diameter():
    exp = [{"cx": 0, "cy": 0, "d": 10.0, "label": "a"}, {"cx": 30, "cy": 0, "d": 6.0, "label": "b"}]
    meas = [{"cx": 30.01, "cy": 0.0, "d": 6.05}, {"cx": 0.0, "cy": 0.02, "d": 10.0}, {"cx": 60, "cy": 5, "d": 3}]
    rep = V.match_circles(exp, meas)
    assert [m.ok for m in rep.matches] == [True, False]         # b: diameter 0.05 out
    assert rep.matches[1].d_error == pytest.approx(0.05)
    assert len(rep.unexpected) == 1 and rep.unexpected[0]["cx"] == 60
    assert not rep.ok
    d, c = rep.worst()
    assert d == pytest.approx(0.05) and c == pytest.approx(0.02, abs=1e-3)


def test_match_circles_missing_and_far():
    rep = V.match_circles([{"cx": 0, "cy": 0, "d": 5}], [{"cx": 50, "cy": 50, "d": 5}])
    assert rep.matches[0].measured is None and not rep.ok


def test_match_circles_is_one_to_one():
    exp = [{"cx": 0, "cy": 0, "d": 5}, {"cx": 0.1, "cy": 0, "d": 5}]
    rep = V.match_circles(exp, [{"cx": 0.0, "cy": 0.0, "d": 5}])
    assert sum(m.measured is not None for m in rep.matches) == 1


def test_pitch_errors():
    exp = [{"cx": 0, "cy": 0, "d": 5, "label": "p"}, {"cx": 60, "cy": 0, "d": 5, "label": "q"}]
    meas = [{"cx": 0, "cy": 0, "d": 5}, {"cx": 60.03, "cy": 0, "d": 5}]
    p = V.pitch_errors(V.match_circles(exp, meas))
    assert len(p) == 1 and p[0]["between"] == ["p", "q"] and p[0]["error"] == pytest.approx(0.03)


def test_edge_presence():
    lines = [{"x0": -30, "y0": -20, "x1": -30, "y1": 20}, {"x0": -30, "y0": 20, "x1": 30, "y1": 20},
             {"x0": 30.02, "y0": -20, "x1": 30.02, "y1": 20}]
    res = V.edge_presence(lines, [{"cx": 0, "cy": 0, "r": 20}], (-30, -20, 30, 20))
    assert res["left"]["found"] and res["top"]["found"] and res["right"]["found"]
    assert res["bottom"]["found"] and res["bottom"]["by"] == "circle"       # tangent circle counts
    assert res["ok"]
    res = V.edge_presence(lines, [], (-30, -20, 30, 20))
    assert not res["bottom"]["found"] and not res["ok"]


# ---------------------------------------------------------------------------- PDF reading

fitz = pytest.importorskip("pymupdf", reason="PyMuPDF (optional drawing extra) is not installed")


def _polyline_circle_pdf(path, cx, cy, r, n, jitter, page=(420.0, 297.0)):
    """A circle drawn as ``n`` chords with PDF-like coordinate jitter (what CATIA exports)."""
    rnd = random.Random(1)
    doc = fitz.open()
    pg = doc.new_page(width=page[0] / PT, height=page[1] / PT)
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * (i % n) / n
        pts.append(((cx + r * math.cos(a) + rnd.uniform(-jitter, jitter)) / PT,
                    (cy + r * math.sin(a) + rnd.uniform(-jitter, jitter)) / PT))
    pts[-1] = pts[0]
    shape = pg.new_shape()
    for p, q in zip(pts, pts[1:]):
        shape.draw_line(fitz.Point(*p), fitz.Point(*q))
    shape.finish(width=0.7, closePath=False)
    shape.commit()
    doc.save(path)
    doc.close()


def test_polyline_circles_survive_pdf_jitter(tmp_path):
    """Regression: large circles exported as hundreds of chords were missed by extract()."""
    pdf = str(tmp_path / "c.pdf")
    _polyline_circle_pdf(pdf, 150.0, 147.0, 40.0, 1000, 0.004)
    found = V.polyline_circles(pdf, 0, None, 1.0, (150.0, 147.0))
    circles = [c for c in found if c["kind"] == "circle"]
    assert len(circles) == 1
    assert circles[0]["d"] == pytest.approx(80.0, abs=0.02)
    assert (circles[0]["cx"], circles[0]["cy"]) == pytest.approx((0.0, 0.0), abs=0.02)


def test_find_circles_needs_explicit_scale(tmp_path):
    pdf = str(tmp_path / "c.pdf")
    _polyline_circle_pdf(pdf, 100.0, 100.0, 10.0, 200, 0.0)
    with pytest.raises(ValueError, match="scale is required"):
        V.find_circles(pdf, 0, None, None, None)


def test_find_circles_scale_and_origin(tmp_path):
    pdf = str(tmp_path / "c.pdf")
    _polyline_circle_pdf(pdf, 100.0, 100.0, 10.0, 200, 0.0)           # 20 mm on paper
    found = V.find_circles(pdf, 0, None, "2:1", [100.0, 100.0])       # paper:model = 2:1 -> model = 10 mm
    assert len(found) == 1 and found[0]["d"] == pytest.approx(10.0, abs=0.02)
    found = V.find_circles(pdf, 0, None, 2.0, [100.0, 100.0])         # numeric = model/paper = 2 -> 40 mm
    assert found[0]["d"] == pytest.approx(40.0, abs=0.05)


def test_check_view_pdf_end_to_end(tmp_path):
    pdf = str(tmp_path / "v.pdf")
    _polyline_circle_pdf(pdf, 150.0, 150.0, 4.0, 60, 0.0)             # a hole d=8 at the view origin
    frame = V.ViewFrame(150.0, 147.0, 1.0, (150.0, 147.0), 297.0)     # origin on the circle centre (PDF y = 297-147=150)
    ok = V.check_view_pdf(pdf, frame, expected_circles=[{"cx": 0, "cy": 0, "d": 8.0, "label": "h"}])
    assert ok["ok"] and ok["circles"]["matched"] == 1
    bad = V.check_view_pdf(pdf, frame, expected_circles=[{"cx": 0, "cy": 0, "d": 8.2, "label": "h"}])
    assert not bad["ok"] and bad["circles"]["worst_diameter_error_mm"] == pytest.approx(0.2, abs=0.02)
    miss = V.check_view_pdf(pdf, frame, expected_circles=[{"cx": 20, "cy": 0, "d": 8.0}])
    assert not miss["ok"]


def test_infer_cylinder_axes_for_full_cylinders_without_axis():
    """catia_list_faces gave no axis for the 360-degree bosses and bores of a flange (seen live)."""
    def plane(name, off):
        return {"type": "plane", "parallel_to": {"plane": name, "offset": off}}

    def cyl(r, length, z):
        return {"type": "cylinder", "radius": r, "area_mm2": 2 * math.pi * r * length, "cog": [0.0, 0.0, z], "axis": None}

    faces = [plane("xy", 0.0), plane("xy", 10.0), plane("xy", 22.0), plane("yz", 5.0),
             cyl(40, 10, 5.0), cyl(22, 12, 16.0), cyl(15, 22, 11.0), cyl(9, 3, 7.0)]
    out = V.infer_cylinder_axes(faces)
    inferred = [f["axis"]["direction"] if f["axis"] else None for f in out if f["type"] == "cylinder"]
    assert inferred == [[0.0, 0.0, 1.0]] * 3 + [None]           # the last one has no planes at its ends
    assert faces[4]["axis"] is None                              # input is not modified
    circles = V.cylinder_circles(faces, *V.view_axes("front", *V.front_axes("+Z")))
    assert sorted(c["d"] for c in circles) == [30.0, 44.0, 80.0]
    assert V.cylinder_circles(faces, *V.view_axes("front", *V.front_axes("-Y"))) == []
