"""Tests for the optional drawing module (synthetic vector PDF built with PyMuPDF)."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from catia_mcp.drawing import cli, geometry, render
from catia_mcp.drawing.geometry import DrawingDependencyError
from catia_mcp.tools.drawing import DrawingTools

PT = 25.4 / 72.0
K = 2.0  # drawing scale 1:2 -> 1 mm on paper = 2 mm on the part
ORIGIN = (100.0, 150.0)  # paper mm point used as model origin (centre of the big circle)


def P(x_mm: float, y_mm: float):
    """paper mm -> PDF points"""
    return (x_mm / PT, y_mm / PT)


def _arc_beziers(cx, cy, r, a0, a1):
    """Cubic Bezier pieces (paper mm, y down) for a circular arc, like CAD exporters do."""
    n = max(1, math.ceil(abs(a1 - a0) / (math.pi / 2) - 1e-9))
    out = []
    for i in range(n):
        t0 = a0 + (a1 - a0) * i / n
        t1 = a0 + (a1 - a0) * (i + 1) / n
        h = 4 / 3 * math.tan((t1 - t0) / 4)
        p0 = (cx + r * math.cos(t0), cy + r * math.sin(t0))
        p3 = (cx + r * math.cos(t1), cy + r * math.sin(t1))
        p1 = (p0[0] - h * r * math.sin(t0), p0[1] + h * r * math.cos(t0))
        p2 = (p3[0] + h * r * math.sin(t1), p3[1] - h * r * math.cos(t1))
        out.append((p0, p1, p2, p3))
    return out


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> str:
    fitz = pytest.importorskip("fitz")
    path = tmp_path_factory.mktemp("drw") / "synthetic.pdf"
    doc = fitz.open()
    pg = doc.new_page(width=420 / PT, height=297 / PT)  # A3 landscape
    sh = pg.new_shape()

    # 1) big circle R=100 model (50 paper) drawn as 4 Beziers, centred on ORIGIN
    for b in _arc_beziers(ORIGIN[0], ORIGIN[1], 50.0, 0, 2 * math.pi):
        sh.draw_bezier(*[fitz.Point(*P(*q)) for q in b])
    sh.finish(color=(0, 0, 0), width=0.5)

    # 2) small circle R=12.5 model (6.25 paper), centre paper (130, 150)
    for b in _arc_beziers(130.0, 150.0, 6.25, 0, 2 * math.pi):
        sh.draw_bezier(*[fitz.Point(*P(*q)) for q in b])
    sh.finish(color=(0, 0, 0), width=0.5)

    # 3) single 90 degree Bezier arc, R=30 model (15 paper), centre paper (40, 60)
    for b in _arc_beziers(40.0, 60.0, 15.0, 0, math.pi / 2):
        sh.draw_bezier(*[fitz.Point(*P(*q)) for q in b])
    sh.finish(color=(0, 0, 0), width=0.5)

    # 4) 120 degree arc as a 5-degree polyline, R=40 model (20 paper), centre paper (200, 60)
    pts = [(200 + 20 * math.cos(math.radians(a)), 60 + 20 * math.sin(math.radians(a)))
           for a in range(0, 121, 5)]
    sh.draw_polyline([fitz.Point(*P(*q)) for q in pts])
    sh.finish(color=(0, 0, 0), width=0.5, closePath=False)

    # 5) regular hexagon (side 2 paper mm, vertices on a circle): must NOT become a circle
    hexa = [(300 + 2 * math.cos(math.radians(a)), 60 + 2 * math.sin(math.radians(a)))
            for a in range(0, 360, 60)]
    sh.draw_polyline([fitz.Point(*P(*q)) for q in hexa + [hexa[0]]])
    sh.finish(color=(0, 0, 0), width=0.5, closePath=False)

    # 6) a horizontal line 60 paper mm long (120 model) and a 3-4-5 slanted one
    sh.draw_line(fitz.Point(*P(50, 230)), fitz.Point(*P(110, 230)))
    sh.finish(color=(0, 0, 0), width=0.5)
    sh.draw_line(fitz.Point(*P(150, 230)), fitz.Point(*P(180, 250)))  # 30 x 20 paper
    sh.finish(color=(0, 0, 0), width=0.5)
    sh.commit()

    pg.insert_text(fitz.Point(*P(330, 280)), "SCALE 1:2", fontsize=10)
    doc.save(path)
    doc.close()
    return str(path)


def _by_r(items, r, tol=0.01):
    hits = [e for e in items if abs(e["r"] - r) <= tol]
    assert hits, f"no entity with R={r} in {[e['r'] for e in items]}"
    return hits[0]


# -- extraction --------------------------------------------------------------------------
def test_scale_from_title_block_and_override(pdf):
    res = geometry.extract(pdf, origin=ORIGIN)
    assert res["scale"] == pytest.approx(2.0)
    assert res["scale_source"] == "title block text"
    res = geometry.extract(pdf, scale="1:4", origin=ORIGIN)
    assert res["scale_source"] == "argument"
    assert res["scale"] == pytest.approx(4.0)
    assert geometry.parse_scale("2:1") == pytest.approx(0.5)


def test_circle_radii_within_0_01_mm(pdf):
    res = geometry.extract(pdf, scale="1:2", origin=ORIGIN)
    big = _by_r(res["circles"], 100.0)
    small = _by_r(res["circles"], 12.5)
    assert big["kind"] == "circle"
    assert big["d"] == pytest.approx(200.0, abs=0.02)
    # centres in model mm: big at (0, 0); small at (+30 paper * 2, 0)
    assert (big["cx"], big["cy"]) == (pytest.approx(0, abs=0.01), pytest.approx(0, abs=0.01))
    assert small["cx"] == pytest.approx(60.0, abs=0.01)
    assert small["cy"] == pytest.approx(0.0, abs=0.01)
    assert big["paper"] == [pytest.approx(100.0, abs=0.01), pytest.approx(150.0, abs=0.01)]
    # exactly one entity per circle (four Beziers were fused)
    assert len([c for c in res["circles"] if abs(c["r"] - 100.0) < 0.5]) == 1


def test_bezier_arc_exact(pdf):
    res = geometry.extract(pdf, scale="1:2", origin=(0, 0))
    arc = _by_r(res["arcs"], 30.0)
    assert arc["kind"] == "arc"
    assert arc["span_deg"] == pytest.approx(90.0, abs=0.1)
    assert arc["cx"] == pytest.approx(80.0, abs=0.01)  # paper 40 * 2
    assert arc["cy"] == pytest.approx(-120.0, abs=0.01)  # y flipped: -(60 * 2)
    # paper y grows downwards: increasing paper angle is clockwise in model space
    assert arc["direction"] == "cw"


def test_polyline_arc_least_squares(pdf):
    res = geometry.extract(pdf, scale="1:2")
    arc = _by_r(res["arcs"], 40.0)
    assert arc["span_deg"] == pytest.approx(120.0, abs=0.5)
    assert arc["cx"] == pytest.approx(400.0, abs=0.01)


def test_hexagon_is_not_an_arc(pdf):
    res = geometry.extract(pdf, scale="1:2", region=[290, 50, 310, 70])
    assert res["counts"]["arcs"] == 0 and res["counts"]["circles"] == 0
    assert res["counts"]["lines"] == 6


def test_lines_and_region_filter(pdf):
    res = geometry.extract(pdf, scale="1:2", region=[40, 220, 200, 260])
    lengths = sorted(round(l["length"], 2) for l in res["lines"])
    assert lengths == [pytest.approx(72.11, abs=0.01), pytest.approx(120.0, abs=0.01)]
    slanted = [l for l in res["lines"] if l["length"] < 100][0]
    assert slanted["angle_deg"] == pytest.approx(180 - math.degrees(math.atan2(20, 30)), abs=0.05)
    assert res["counts"]["circles"] == 0  # region excludes them
    assert res["bbox"][0] == pytest.approx(100.0, abs=0.01)


def test_min_r_filters_small_arcs(pdf):
    res = geometry.extract(pdf, scale="1:2", min_r=10.0)  # paper mm: drops R6.25
    assert not any(abs(c["r"] - 12.5) < 0.1 for c in res["circles"])


def test_bad_inputs(pdf, tmp_path):
    with pytest.raises(ValueError, match="out of range"):
        geometry.extract(pdf, page=3)
    with pytest.raises(ValueError, match="cannot open"):
        geometry.extract(str(tmp_path / "nope.pdf"))
    with pytest.raises(ValueError):
        geometry.parse_scale("0:1")


def test_blank_page_warns(tmp_path):
    fitz = pytest.importorskip("fitz")
    p = tmp_path / "blank.pdf"
    d = fitz.open()
    d.new_page()
    d.save(p)
    res = geometry.extract(str(p))
    assert any("no vector" in w for w in res["warnings"])
    assert any("scale not found" in w for w in res["warnings"])


# -- render / overlay --------------------------------------------------------------------
def _size(png):
    fitz = pytest.importorskip("fitz")
    pix = fitz.Pixmap(str(png))
    return pix.width, pix.height, pix


def test_render_page_region_and_grid(pdf, tmp_path):
    full = render.render_view(pdf, 0, None, str(tmp_path / "full.png"), 50)
    w, h, _ = _size(full)
    assert (w, h) == (pytest.approx(420 / 25.4 * 50, abs=2), pytest.approx(297 / 25.4 * 50, abs=2))
    crop = render.render_view(pdf, 0, [40, 40, 140, 100], str(tmp_path / "crop.png"), 100)
    w, h, _ = _size(crop)
    assert (w, h) == (pytest.approx(100 / 25.4 * 100, abs=2), pytest.approx(60 / 25.4 * 100, abs=2))
    gridded = render.render_view(pdf, 0, [40, 100, 160, 200], str(tmp_path / "g.png"), 80,
                                 scale="1:2", origin=ORIGIN, grid=20)
    plain = render.render_view(pdf, 0, [40, 100, 160, 200], str(tmp_path / "p.png"), 80)
    assert Path(gridded).read_bytes() != Path(plain).read_bytes()
    with pytest.raises(ValueError, match="origin"):
        render.render_view(pdf, 0, None, str(tmp_path / "x.png"), 50, grid=10)
    with pytest.raises(ValueError, match="dpi"):
        render.render_view(pdf, 0, None, str(tmp_path / "x.png"), 5)


def _count_color(pix, rgb, tol=40):
    s, n = pix.samples, pix.n
    c = 0
    for i in range(0, len(s), n):
        if all(abs(s[i + j] - rgb[j]) <= tol for j in range(3)):
            c += 1
    return c


def test_overlay_draws_sketch_on_the_drawing(pdf, tmp_path):
    region = [40, 90, 160, 210]
    base = render.render_view(pdf, 0, region, str(tmp_path / "b.png"), 100)
    sketch = [{"type": "circle", "cx": 0, "cy": 0, "radius": 100}]
    out = render.overlay(pdf, 0, sketch, str(tmp_path / "o.png"), region, "1:2", ORIGIN, 100)
    _, _, pb = _size(base)
    _, _, po = _size(out)
    assert _count_color(pb, (0, 170, 0)) == 0
    assert _count_color(po, (0, 170, 0)) > 200  # green sketch circle
    # the sketch circle sits ON the black circle: pixel at model (100, 0) is green-ish
    px = int((ORIGIN[0] + 100 / K - region[0]) / 25.4 * 100)
    py = int((ORIGIN[1] - region[1]) / 25.4 * 100)
    near = [po.pixel(px + dx, py + dy) for dx in range(-3, 4) for dy in range(-3, 4)]
    assert any(g > 120 and r < 100 and b < 100 for r, g, b in near)


def test_overlay_profile_and_hv_and_json_file(pdf, tmp_path):
    profile = {"start": [0, 0], "segments": [
        {"type": "line", "to": [50, 0]},
        {"type": "arc", "to": [0, 50], "center": [0, 0], "direction": "ccw"},
        {"type": "line"},
    ]}
    f = tmp_path / "sk.json"
    f.write_text(json.dumps([["catia_sketch_profile", profile]]))
    a = render.overlay(pdf, 0, str(f), str(tmp_path / "a.png"), [40, 90, 160, 210], "1:2", ORIGIN, 80)
    b = render.overlay(pdf, 0, str(f), str(tmp_path / "b2.png"), [40, 90, 160, 210], "1:2", ORIGIN, 80,
                       hv="-h,v")
    assert Path(a).read_bytes() != Path(b).read_bytes()
    polys = render.load_sketch(profile)
    assert polys[0][0] == (0.0, 0.0) and polys[0][-1] == (0.0, 0.0)
    assert any(math.dist(p, (0, 0)) == pytest.approx(50, abs=1e-6) for p in polys[0][2:-2])


def test_hv_parser_is_safe_and_correct():
    f = render.parse_hv("-h,v")
    assert f(3, 4) == (-3, 4)
    assert render.parse_hv("v,-h")(3, 4) == (4, -3)
    for bad in ("__import__('os'),v", "h", "2h,v", "h+1,v"):
        with pytest.raises(ValueError):
            render.parse_hv(bad)


def test_sketch_errors():
    with pytest.raises(ValueError):
        render.load_sketch([])
    with pytest.raises(ValueError, match="unsupported"):
        render.load_sketch({"type": "spline"})


# -- CLI ---------------------------------------------------------------------------------
def test_cli_geom_render_overlay(pdf, tmp_path, capsys):
    assert cli.main(["geom", pdf, "--scale", "1:2", "--origin", "100,150"]) == 0
    text = capsys.readouterr().out
    assert "R=100.000" in text and "D=200.000" in text
    assert cli.main(["render", pdf, "--dpi", "40", "--out", str(tmp_path / "c.png")]) == 0
    assert (tmp_path / "c.png").exists()
    sk = tmp_path / "s.json"
    sk.write_text(json.dumps({"type": "circle", "center": [0, 0], "radius": 100}))
    assert cli.main(["overlay", pdf, "--sketch", str(sk), "--scale", "1:2", "--origin", "100,150",
                     "--hv=-h,v", "--out", str(tmp_path / "d.png")]) == 0
    assert (tmp_path / "d.png").exists()
    assert cli.main(["geom", str(tmp_path / "missing.pdf")]) == 1


# -- tools -------------------------------------------------------------------------------
def test_tool_definitions_match_module_interface():
    tools = DrawingTools(None)
    defs = tools.get_tool_definitions()
    assert [d["name"] for d in defs] == [
        "drawing_extract_geometry", "drawing_render", "drawing_overlay"]
    for d in defs:
        assert set(d) == {"name", "description", "inputSchema"}
        assert d["inputSchema"]["type"] == "object" and "pdf_path" in d["inputSchema"]["required"]
    assert "vector" in defs[0]["description"].lower()


def test_tool_execute(pdf, tmp_path):
    tools = DrawingTools(None)
    txt = tools.execute("drawing_extract_geometry",
                        {"pdf_path": pdf, "scale": "1:2", "origin": [100, 150], "max_items": 3})
    assert "R=100.000 D=200.000" in txt and "C1: centre=(" in txt
    assert "more" in txt  # truncated
    out = tmp_path / "t.png"
    r = tools.execute("drawing_render", {"pdf_path": pdf, "out_png": str(out), "dpi": 40})
    assert str(out) in r and out.exists()
    o = tmp_path / "ov.png"
    r = tools.execute("drawing_overlay", {
        "pdf_path": pdf, "out_png": str(o), "scale": "1:2", "origin": [100, 150], "dpi": 40,
        "sketch": {"type": "circle", "cx": 0, "cy": 0, "radius": 100}})
    assert str(o) in r and o.exists()
    with pytest.raises(ValueError):
        tools.execute("drawing_overlay", {"pdf_path": pdf})
    with pytest.raises(ValueError):
        tools.execute("nope", {})


def test_tool_reports_bad_pdf_path_as_error(tmp_path):
    pytest.importorskip("fitz")
    with pytest.raises(ValueError, match="cannot open"):
        DrawingTools(None).execute("drawing_render", {"pdf_path": str(tmp_path / "x.pdf")})


# -- without PyMuPDF ---------------------------------------------------------------------
@pytest.fixture
def no_pymupdf(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)


def test_without_pymupdf_clear_message(no_pymupdf, tmp_path):
    assert geometry.is_available() is False
    with pytest.raises(DrawingDependencyError, match=r"pip install \"catia-v5-mcp-server\[drawing\]\""):
        geometry.extract("whatever.pdf")
    with pytest.raises(DrawingDependencyError):
        render.render_view("whatever.pdf")
    tools = DrawingTools(None)
    for name, args in [
        ("drawing_extract_geometry", {"pdf_path": "x.pdf"}),
        ("drawing_render", {"pdf_path": "x.pdf"}),
        ("drawing_overlay", {"pdf_path": "x.pdf", "sketch": {"type": "circle", "cx": 0, "cy": 0, "radius": 1}}),
    ]:
        msg = tools.execute(name, args)
        assert 'pip install "catia-v5-mcp-server[drawing]"' in msg
    assert cli.main(["geom", "x.pdf"]) == 2


def test_import_never_needs_pymupdf():
    code = (
        "import sys; sys.modules['pymupdf']=None; sys.modules['fitz']=None;"
        "import catia_mcp.drawing, catia_mcp.drawing.cli, catia_mcp.tools.drawing;"
        "print('ok')"
    )
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr
