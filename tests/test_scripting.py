"""Offline tests of the scripting DSL: every method emits steps valid against the REAL tool schemas,
default names are refused, and pose tracking matches catia_move_component. No CATIA needed."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from catia_mcp.scripting import (
    AssemblyScript,
    PartScript,
    ScriptError,
    arc_seg,
    axis,
    edge,
    face,
    inspect_steps,
    line_seg,
    load_schemas,
    origin_plane,
)
from catia_mcp.scripting import (
    verify_results as verify,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def schemas():
    return load_schemas()


def valid(script, schemas):
    problems = script.validate(schemas)
    assert problems == [], problems


def tools(script):
    return [s["tool"] for s in script.steps(**({"require_save": False} if isinstance(script, PartScript) else {}))]


def new_part(tmp_path, name="Flange"):
    return PartScript(name, tmp_path / name)


def base_disc(p, r=40.0, h=10.0):
    with p.sketch("xy", "Sk_Disc") as sk:
        sk.circle(0, 0, r)
    p.pad(f"Disc_D{int(2 * r)}_T{int(h)}", h)


# ── part: every method produces schema-valid steps ───────────────────────────────────────
def test_full_part_valid_against_real_schemas(tmp_path, schemas):
    p = new_part(tmp_path)
    base_disc(p)
    with p.sketch("xy", "Sk_Boss") as sk:
        sk.rect(-10, -10, 10, 10)
    p.pad("Boss_20x20", 5, symmetric=False, direction="reverse", second_height=1)
    with p.sketch(p.plane("xy", offset=10), "Sk_Slot") as sk:
        sk.centered_rect(0, 0, 30, 4)
    p.pocket("Slot_W4", 3)
    with p.sketch(p.plane(face_point=(30, 0, 10)), "Sk_Step") as sk:
        sk.poly([(0, 0), (5, 0), (5, 5)])
    p.pocket("Step_Cut", limit="up_to_next")
    p.hole("Hole_D6", (25, 0, 10), 6, 5)
    p.hole("Hole_D6_thru", (-25, 0, 10), 6, up_to_point=(-25, 0, 0))
    p.hole("Hole_Cbore", (0, 25, 10), 5, 8, kind="counterbored", head_diameter=9, head_depth=3)
    p.hole("Hole_Csk", (0, -25, 10), 5, 8, kind="countersunk", head_diameter=10, head_angle=90)
    p.hole("Hole_Taper", (30, 30, 10), 5, 8, kind="tapered", taper_angle=5, threaded=False)
    p.fillet("Round_R2", 2, [(40, 0, 5)])
    p.chamfer("Chamfer_2x45", 2, [(40, 0, 10)])
    p.chamfer("Chamfer_2x3", 2, [(-40, 0, 10)], length2=3)
    p.pattern("circ", "Holes_x6", "Hole_D6", count=6, axis_plane="xy")
    p.pattern("circ", "Holes_x3_face", "Hole_Csk", count=3, axis_face_point=(40, 0, 5), angular=90, reverse=True)
    p.pattern("rect", "Holes_2x3", "Hole_Cbore", count=2, spacing=5, direction="x", count2=3, spacing2=4, direction2="y")
    p.mirror("Mirror_YZ", plane="yz")
    p.mirror("Mirror_Face", plane_face_point=(0, 40, 5))
    # revolved body + boolean
    p.body("Body_Turned")
    with p.sketch("xy", "Sk_Turned") as sk:
        sk.polyline([(0, 0), (0, 5), (10, 5), (10, 0)], closed=True)
    p.shaft("Turned_D10", "h")
    with p.sketch("xy", "Sk_Groove") as sk:
        sk.profile((2, 5), [line_seg((4, 5)), line_seg((4, 3)), arc_seg((2, 3), (3, 3), "ccw")])
    p.groove("Groove_R1", "h", angle=360)
    with p.sketch("xy", "Sk_Axis") as sk:
        sk.line(0, 0, 0, 20, axis=True)
        sk.rect(1, 2, 3, 8)
        sk.line(5, 1, 6, 7).arc(0, 0, 3, 0, 90).point(1, 1)
    p.shaft("Turned_Axis", "sketch_line", 180)
    p.combine(p.result, "Body_Turned", "add", "Add_Turned")
    p.raw("catia_get_parameters", {})
    p.save()
    p.checks(volume=1234.5, bbox=(80, 80, 20), mass=0.5)
    valid(p, schemas)
    scen = p.scenario()
    assert scen["steps"][0]["tool"] == "catia_close_all"
    assert set(scen["checks"]) >= {"features", "audit", "volume", "mass", "bbox"}


def test_steps_have_expected_order_like_proven_scripts(tmp_path):
    p = new_part(tmp_path, "Tow")
    base_disc(p)
    p.body("Body_Holes")
    with p.sketch("xy", "Sk_Holes") as sk:
        sk.circle(10, 0, 3)
    p.pad("Holes_Cyl", 30, symmetric=True)
    p.combine(p.result, "Body_Holes", "remove", "Remove_Holes")
    p.save(views=("isometric",))
    t = tools(p)
    assert t[:3] == ["catia_close_all", "catia_new_part", "catia_rename_body"]
    assert t[3:6] == ["catia_create_sketch", "catia_sketch_circle", "catia_close_sketch"]
    assert t[6] == "catia_pad" and t[7:9] == ["catia_new_body", "catia_activate_body"]
    assert t[-8:-2] == ["catia_update_part", "catia_get_inertia", "catia_get_bounding_box", "catia_get_tree",
                        "catia_save_document", "catia_set_view"]
    # the boolean re-activates the target body so the state is deterministic
    i = t.index("catia_boolean_operation")
    assert t[i + 1] == "catia_activate_body"


def test_new_part_renames_main_body_without_naming_the_old_one(tmp_path):
    p = new_part(tmp_path)
    ren = p.steps(require_save=False)[2]
    assert ren["args"] == {"new_name": "Flange_Resultat"}  # works whatever the UI language


# ── names ────────────────────────────────────────────────────────────────────────────────
BAD_NAMES = ["", "  ", None, "Pad.1", "Sketch.3", "Extrusion.2", "PartBody", "Corps principal", "pad", "Hole_1",
             "my pad", "Esquisse.4", "12", "Pocket", 5]


@pytest.mark.parametrize("bad", BAD_NAMES)
def test_default_or_empty_names_refused_everywhere(tmp_path, bad):
    p = new_part(tmp_path)
    with pytest.raises(ScriptError):
        p.sketch("xy", bad)
    with pytest.raises(ScriptError):
        p.body(bad)
    base_disc(p)
    with p.sketch("xy", "Sk_A") as sk:
        sk.circle(0, 0, 1)
    with pytest.raises(ScriptError):
        p.pad(bad, 1)
    with pytest.raises(ScriptError):
        p.raw("catia_pad", {"height": 1, "name": bad})
    with pytest.raises(ScriptError):
        PartScript(bad, tmp_path)


def test_default_names_refused_for_other_features(tmp_path):
    p = new_part(tmp_path)
    base_disc(p)
    for call in (lambda: p.hole("Hole.1", (0, 0, 10), 4, 3),
                 lambda: p.fillet("Fillet.2", 1, [(40, 0, 5)]),
                 lambda: p.chamfer("Chamfer.1", 1, [(40, 0, 5)]),
                 lambda: p.mirror("Mirror.1", plane="xy"),
                 lambda: p.pattern("rect", "RectPattern.1", "Disc_D80_T10", count=2, spacing=5)):
        with pytest.raises(ScriptError):
            call()
    p.body("Body_X")
    with pytest.raises(ScriptError):
        p.combine(p.result, "Body_X", "add", "Assemble.1")


def test_names_must_be_unique_and_meaningful(tmp_path):
    p = new_part(tmp_path)
    with p.sketch("xy", "Sk_Disc") as sk:
        sk.circle(0, 0, 1)
    with pytest.raises(ScriptError, match="already used"):
        p.sketch("xy", "Sk_Disc")
    with pytest.raises(ScriptError, match="already used"):
        p.body("Flange_Resultat")
    p.pad("Base_T1", 1)
    with p.sketch("xy", "Sk_Two") as sk:
        sk.circle(0, 0, 2)
    with pytest.raises(ScriptError, match="already used"):
        p.pad("Base_T1", 1)
    for ok in ("Base_L120", "Bore_D25", "Esq_Collerette_R57.5", "Slot-W8"):
        assert check(ok) == ok


def check(n):
    from catia_mcp.scripting import check_name

    return check_name(n, "x")


# ── types, units, ranges ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["5", None, True, float("nan"), float("inf"), 1e9, -3, 0])
def test_bad_numbers_refused(tmp_path, bad):
    p = new_part(tmp_path)
    with p.sketch("xy", "Sk_A") as sk:
        with pytest.raises(ScriptError):
            sk.circle(0, 0, bad)
        sk.circle(0, 0, 3)
    with pytest.raises(ScriptError):
        p.pad("Pad_Bad", bad)


def test_units_are_millimetres_message(tmp_path):
    p = new_part(tmp_path)
    with p.sketch("xy", "Sk_A") as sk:
        sk.circle(0, 0, 3)
    with pytest.raises(ScriptError, match="MILLIMETRES"):
        p.pad("Pad_Huge", 5e6)


def test_pad_and_pocket_argument_rules(tmp_path):
    p = new_part(tmp_path)
    with p.sketch("xy", "Sk_A") as sk:
        sk.circle(0, 0, 3)
    with pytest.raises(ScriptError, match="second_height"):
        p.pad("Pad_Sym", 5, symmetric=True, second_height=1)
    with pytest.raises(ScriptError):
        p.pad("Pad_Dir", 5, direction="sideways")
    p.pad("Pad_Ok", 5)
    with p.sketch("xy", "Sk_B") as sk:
        sk.circle(0, 0, 1)
    with pytest.raises(ScriptError, match="depth is required"):
        p.pocket("Pocket_NoDepth")
    with pytest.raises(ScriptError, match="must not be given"):
        p.pocket("Pocket_Both", 2, limit="up_to_next")
    with pytest.raises(ScriptError):
        p.hole("Hole_Both", (0, 0, 5), 2, 3, up_to_point=(0, 0, 0))
    with pytest.raises(ScriptError):
        p.hole("Hole_None", (0, 0, 5), 2)
    with pytest.raises(ScriptError, match="head_diameter"):
        p.hole("Hole_Cb", (0, 0, 5), 2, 3, kind="counterbored", head_diameter=1, head_depth=1)
    with pytest.raises(ScriptError):
        p.fillet("Fillet_None", 1, [])
    with pytest.raises(ScriptError):
        p.pattern("circ", "Pat_Bad", "Pad_Ok", count=3)  # no axis
    with pytest.raises(ScriptError, match="unknown feature"):
        p.pattern("rect", "Pat_Typo", "Pad_Okk", count=3, spacing=2)
    with pytest.raises(ScriptError):
        p.mirror("Mirror_Both", plane="xy", plane_face_point=(0, 0, 0))


def test_sketch_lifecycle_rules(tmp_path):
    p = new_part(tmp_path)
    with pytest.raises(ScriptError, match="no free sketch"):
        p.pad("Pad_NoSketch", 5)
    sk = p.sketch("xy", "Sk_Empty")
    with pytest.raises(ScriptError, match="empty"):
        sk.close()
    sk.circle(0, 0, 4)
    p.pad("Pad_AutoClose", 3)  # the open sketch is closed for you
    assert sk.closed
    with pytest.raises(ScriptError, match="already closed"):
        sk.circle(0, 0, 1)
    with pytest.raises(ScriptError, match="no free sketch"):
        p.pad("Pad_Twice", 3)  # one sketch feeds one feature
    with pytest.raises(ScriptError):
        p.sketch("ab", "Sk_BadPlane")
    with pytest.raises(ScriptError):
        p.plane("xy", offset=5, face_point=(0, 0, 0))


def test_explicit_sketch_argument(tmp_path, schemas):
    p = new_part(tmp_path)
    with p.sketch("xy", "Sk_First") as sk1:
        sk1.circle(0, 0, 4)
    with p.sketch("xy", "Sk_Second") as sk2:
        sk2.circle(0, 0, 8)
    p.pad("Pad_Big", 3, sketch=sk2)
    p.pad("Pad_Small", 3, sketch="Sk_First")
    st = p.steps(require_save=False)
    pads = [s for s in st if s["tool"] == "catia_pad"]
    assert [s["args"]["sketch_name"] for s in pads] == ["Sk_Second", "Sk_First"]
    with pytest.raises(ScriptError, match="unknown sketch"):
        p.pad("Pad_X", 1, sketch="Nope")
    valid(_finish(p), schemas)


def _finish(p):
    p.save()
    return p


# ── profiles ─────────────────────────────────────────────────────────────────────────────
def test_profile_checks(tmp_path):
    p = new_part(tmp_path)
    sk = p.sketch("xy", "Sk_Profiles")
    sk.poly([(0, 0), (10, 0), (10, 10), (0, 10)])
    with pytest.raises(ScriptError, match="crosses itself"):
        sk.poly([(0, 0), (12, 8), (10, 0), (0, 10)])  # bow tie
    with pytest.raises(ScriptError, match="at least 3"):
        sk.poly([(0, 0), (1, 1)])
    with pytest.raises(ScriptError, match="no area"):
        sk.poly([(0, 0), (1, 1), (2, 2)])
    with pytest.raises(ScriptError, match="end points are"):
        sk.profile((0, 0), [arc_seg((10, 0), (3, 0)), line_seg((0, 5))])  # center not equidistant
    with pytest.raises(ScriptError, match="LAST segment"):
        sk.profile((0, 0), [{"type": "line"}, line_seg((1, 1))])
    with pytest.raises(ScriptError):
        sk.profile((0, 0), [{"type": "arc", "to": [1, 1]}])  # arc without center
    with pytest.raises(ScriptError):
        sk.rect(0, 0, 0, 5)
    with pytest.raises(ScriptError):
        sk.line(1, 1, 1, 1)
    with pytest.raises(ScriptError):
        sk.arc(0, 0, 5, 30, 30)
    with pytest.raises(ScriptError):
        sk.point("a", 1)


def test_poly_closes_itself_and_polyline_stays_open(tmp_path):
    p = new_part(tmp_path)
    sk = p.sketch("xy", "Sk_P")
    sk.poly([(0, 0), (4, 0), (4, 3)])
    sk.polyline([(10, 0), (12, 0), (12, 2)])
    steps = p.steps(require_save=False)
    prof = [s for s in steps if s["tool"] == "catia_sketch_profile"]
    assert prof[0]["args"]["closed"] is True and prof[0]["args"]["segments"][-1] == {"type": "line"}
    assert prof[1]["args"]["closed"] is False and all("to" in s for s in prof[1]["args"]["segments"])


def test_long_arc_warns_about_direction(tmp_path):
    p = new_part(tmp_path)
    sk = p.sketch("xy", "Sk_Arc")
    sk.profile((5, 0), [arc_seg((-5, 0), (0, 0), "cw"), arc_seg((5, 0), (0, 0), "cw")])
    assert not p.warnings or "sweeps" in p.warnings[0]
    sk2 = p.sketch("xy", "Sk_Arc2")
    sk2.profile((5, 0), [arc_seg((0, 5), (0, 0), "ccw"), line_seg((0, 0))])
    n = len(p.warnings)
    sk3 = p.sketch("xy", "Sk_Arc3")
    sk3.profile((5, 0), [arc_seg((0, 5), (0, 0), "cw"), line_seg((0, 0))])  # 270 degrees the long way
    assert len(p.warnings) == n + 1 and "direction" in p.warnings[-1]


def test_revolution_profile_must_not_cross_the_axis(tmp_path):
    p = new_part(tmp_path)
    with p.sketch("xy", "Sk_Crossing") as sk:
        sk.polyline([(0, -5), (0, 5), (10, 5), (10, -5)], closed=True)
    with pytest.raises(ScriptError, match="crosses the revolution axis"):
        p.shaft("Turned_Bad", "h")
    # the same profile revolved about V (H=0) touches the axis but stays on one side
    p.shaft("Turned_Ok", "v")
    with p.sketch("xy", "Sk_NoAxis") as sk:
        sk.rect(1, 1, 2, 2)
    with pytest.raises(ScriptError, match="sketch_line"):
        p.shaft("Turned_NoAxis", "sketch_line")
    with pytest.raises(ScriptError):
        p.shaft("Turned_Angle", "h", angle=400)


# ── bodies / booleans / save ─────────────────────────────────────────────────────────────
def test_combine_rules(tmp_path):
    p = new_part(tmp_path)
    base_disc(p)
    p.body("Body_Empty")
    with pytest.raises(ScriptError, match="holds no solid"):
        p.combine(p.result, "Body_Empty", "remove", "Remove_Empty")
    with pytest.raises(ScriptError, match="unknown"):
        p.combine(p.result, "Body_Nope", "remove", "Remove_Nope")
    with pytest.raises(ScriptError):
        p.combine(p.result, "Body_Empty", "subtract", "Sub_X")
    with pytest.raises(ScriptError, match="same body"):
        p.combine(p.result, p.result, "add", "Add_Self")
    with pytest.raises(ScriptError, match="never merged"):
        p.save()


def test_save_needs_material_and_is_final(tmp_path):
    p = new_part(tmp_path)
    with pytest.raises(ScriptError, match="empty"):
        p.save()
    base_disc(p)
    with pytest.raises(ScriptError, match="save"):
        p.steps()
    p.save()
    with pytest.raises(ScriptError, match="already"):
        p.save()
    assert p.steps()[-1]["tool"] == "catia_screenshot"


def test_checks_validation(tmp_path):
    p = new_part(tmp_path)
    base_disc(p)
    with pytest.raises(ScriptError):
        p.checks(volume=-1)
    with pytest.raises(ScriptError):
        p.checks(bbox=(1, 2))
    p.checks(volume=125663.7, bbox=(80, 80, None))
    p.save()
    spec = p.checks_spec()
    assert spec["volume"]["expected"] == 125663.7 and spec["bbox"]["expected"] == [80.0, 80.0, None]
    inertia_step = spec["volume"]["step"]
    assert p.steps()[inertia_step - 1]["tool"] == "catia_get_inertia"
    assert p.steps()[spec["audit"]["step"] - 1]["tool"] == "catia_get_tree"


def test_screens_and_file_paths(tmp_path):
    p = new_part(tmp_path)
    base_disc(p)
    p.save(views=("front", "top"))
    st = p.steps()
    save = [s for s in st if s["tool"] == "catia_save_document"][0]
    assert save["args"]["file_path"].endswith("Flange.CATPart")
    shots = [s["args"]["file_path"] for s in st if s["tool"] == "catia_screenshot"]
    assert shots[0].endswith("01_front.png") and shots[1].endswith("02_top.png")
    with pytest.raises(ScriptError):
        new_part(tmp_path, "Other").save(views=("diagonal",))


# ── built-in verification (volume change, inertia, bbox, audit) ──────────────────────────
def _results(p, overrides=None):
    """Fake per-step outputs for a run of ``p`` (all OK)."""
    overrides = overrides or {}
    out = []
    for i, s in enumerate(p.steps(), 1):
        text = overrides.get(i)
        if text is None:
            text = {
                "catia_pad": "Pad created, volume change +1000.00 mm3.",
                "catia_pocket": "Pocket created, volume change -200.00 mm3.",
                "catia_get_tree": "AUDIT: OK - no default name left.",
            }.get(s["tool"], "ok")
        out.append(SimpleNamespace(index=i, tool=s["tool"], ok=True, output=text))
    return out


def part_with_checks(tmp_path):
    p = new_part(tmp_path)
    base_disc(p)
    with p.sketch("xy", "Sk_Slot") as sk:
        sk.rect(0, 0, 5, 5)
    p.pocket("Slot_5x5", 2)
    p.save(views=())
    p.checks(volume=800.0, bbox=(80, 80, 10))
    return p


def test_verify_passes_and_flags_each_kind_of_error(tmp_path):
    p = part_with_checks(tmp_path)
    spec = p.checks_spec()
    st = spec["volume"]["step"]
    bb = spec["bbox"]["step"]
    good = {st: json.dumps({"volume_mm3": 805.0}), bb: json.dumps({"size": [80.0, 80.2, 10.0]})}
    assert verify(spec, _results(p, good)) == []
    # wrong volume
    bad = {**good, st: json.dumps({"volume_mm3": 1000.0})}
    assert any("volume" in x for x in verify(spec, _results(p, bad)))
    # wrong bbox
    bad = {**good, bb: json.dumps({"size": [80, 60, 10]})}
    assert any("size along Y" in x for x in verify(spec, _results(p, bad)))
    # pocket that did nothing / that added material
    pocket_step = [f for f in spec["features"] if f["name"] == "Slot_5x5"][0]["step"]
    assert any("did nothing" in x for x in verify(spec, _results(p, {**good, pocket_step: "volume change +0.00 mm3"})))
    assert any("REMOVED" in x for x in verify(spec, _results(p, {**good, pocket_step: "volume change +50.00 mm3"})))
    # naming audit
    tree = spec["audit"]["step"]
    assert any("naming audit" in x for x in verify(spec, _results(p, {**good, tree: "AUDIT: 2 default name(s) to fix"})))
    # not executed
    assert any("not executed" in x for x in verify(spec, _results(p, good)[:5]))


def test_expected_feature_volume_change(tmp_path):
    p = new_part(tmp_path)
    with p.sketch("xy", "Sk_Disc") as sk:
        sk.circle(0, 0, 10)
    p.pad("Disc_R10", 10, expect_dvol=3141.6)
    p.save(views=())
    spec = p.checks_spec()
    pad_step = spec["features"][0]["step"]
    assert verify(spec, _results(p, {pad_step: "volume change +3141.59 mm3"})) == []
    assert any("expected +3141.60" in x for x in verify(spec, _results(p, {pad_step: "volume change +2000.00 mm3"})))


def test_pocket_in_body_without_material_has_no_sign_check(tmp_path):
    p = new_part(tmp_path)
    p.body("Body_Cut")
    with p.sketch("xy", "Sk_C") as sk:
        sk.circle(0, 0, 3)
    p.pocket("Pocket_First", 1)
    spec = p.checks_spec()
    assert spec["features"][0]["sign"] == "any"


# ── run() with a fake server ─────────────────────────────────────────────────────────────
class FakeServer:
    def __init__(self, schemas, outputs=None, fail_tool=None):
        self._schemas = schemas
        self.calls = []
        self.outputs = outputs or {}
        self.fail_tool = fail_tool

    def tool_definitions(self):
        return [{"name": n, "inputSchema": s} for n, s in self._schemas.items()]

    def dispatch(self, name, args, trace=False):
        self.calls.append((name, args))
        if name == self.fail_tool:
            raise RuntimeError("boom")
        return self.outputs.get(name, "ok")


def test_run_dry_run_executes_nothing(tmp_path, schemas):
    p = part_with_checks(tmp_path)
    srv = FakeServer(schemas)
    res = p.run(server=srv, dry_run=True)
    assert res.ok and srv.calls == [] and "DRY RUN OK" in res.text()
    assert not (tmp_path / "Flange" / "Flange.log").exists()


def test_run_executes_verifies_and_writes_log(tmp_path, schemas):
    p = part_with_checks(tmp_path)
    spec = p.checks_spec()
    srv = FakeServer(schemas, {
        "catia_pad": "volume change +1000.00 mm3", "catia_pocket": "volume change -200.00 mm3",
        "catia_get_inertia": json.dumps({"volume_mm3": 800.0}), "catia_get_bounding_box": json.dumps({"size": [80, 80, 10]}),
        "catia_get_tree": "AUDIT: OK - no default name left."})
    res = p.run(server=srv)
    assert res.ok, res.text()
    assert len(srv.calls) == len(p.steps())
    assert res.log_path and res.log_path.is_file() and "CHECKS: all passed" in res.log_path.read_text(encoding="utf-8")
    srv2 = FakeServer(schemas, {"catia_get_inertia": json.dumps({"volume_mm3": 5.0}),
                                "catia_get_bounding_box": json.dumps({"size": [80, 80, 10]}),
                                "catia_get_tree": "AUDIT: OK"})
    res2 = p.run(server=srv2)
    assert not res2.ok and any("volume" in x for x in res2.check_problems)
    assert spec  # spec untouched by running


def test_run_stops_at_first_error(tmp_path, schemas):
    p = part_with_checks(tmp_path)
    srv = FakeServer(schemas, fail_tool="catia_pad")
    res = p.run(server=srv)
    assert not res.ok and res.report.stopped_early and srv.calls[-1][0] == "catia_pad"


def test_write_json_roundtrip_and_schema_validity(tmp_path, schemas):
    from catia_mcp.runner import load_scenario

    p = part_with_checks(tmp_path)
    path = p.write_json(tmp_path / "scenario.json")
    raw, checks = load_scenario(path)
    assert raw == p.steps() and checks == p.checks_spec()


# ── raw() and the schema safety net ──────────────────────────────────────────────────────
def test_raw_is_validated_by_schema(tmp_path, schemas):
    p = new_part(tmp_path)
    base_disc(p)
    p.raw("catia_get_parameters", {"nonsense": 1})
    p.save()
    assert any("unknown argument 'nonsense'" in x for x in p.validate(schemas))
    with pytest.raises(ScriptError):
        p.raw("get_parameters")


# ═══════════════════════════ assembly ════════════════════════════════════════════════════
def new_asm(tmp_path):
    return AssemblyScript("Gearbox", tmp_path / "Gearbox")


def test_full_assembly_valid_against_real_schemas(tmp_path, schemas):
    a = new_asm(tmp_path)
    housing = a.add(tmp_path / "Housing.CATPart")
    shaft = a.add(tmp_path / "Shaft.CATPart")
    bolt1 = a.add(tmp_path / "Bolt.CATPart")
    bolt2 = a.add(tmp_path / "Bolt.CATPart")
    brg = a.add(tmp_path / "vendor_bearing.CATPart", part_number="Bearing_6204")
    sub = a.add(tmp_path / "Cover.CATProduct")
    child = a.add(tmp_path / "Gasket.CATPart", parent=sub)
    assert (housing, shaft, bolt1, bolt2, brg, sub, child) == (
        "Housing.1", "Shaft.1", "Bolt.1", "Bolt.2", "Bearing_6204.1", "Cover.1", "Cover.1/Gasket.1")
    a.fix(housing, "Fix_Housing")
    a.move(shaft, tx=25)
    a.move(shaft, rz=90)
    a.move(bolt1, pose={"tx": 5, "ty": 6, "rx": 180})
    a.move(bolt2, tz=3)
    a.move(brg, ty=1)
    a.coincidence(shaft, axis(0, 0, 10), housing, axis(30, 0, 40), "Coax_Shaft_Housing")
    a.contact(shaft, face(0, 0, 5), housing, face(20, 0, 5), "Contact_Shaft_Housing")
    a.offset(bolt1, face(1, 1, 0), housing, face(1, 1, 9), 4, "Dist_Bolt1_Housing_4", orientation="opposite")
    a.offset(bolt2, origin_plane("xy"), housing, origin_plane("xy"), -2, "Dist_Bolt2_Housing_2", orientation="same")
    a.angle(bolt2, edge(1, 0, 0), housing, edge(0, 1, 0), 45, "Angle_Bolt2_Housing_45")
    a.coincidence(brg, ("plane", "xy"), housing, ("plane", "xy"), "Coplan_Bearing_Housing")
    a.coincidence(child, axis(1, 0, 0), housing, axis(2, 0, 0), "Coax_Gasket_Housing")
    a.clean_display(hide_tree=True)
    a.finish(views=("isometric", "front", "top"), allow_clashes=[("Bearing_6204.1", "Shaft.1")])
    valid(a, schemas)
    tl = tools(a)
    assert tl[-1] == "catia_clean_display"
    i = tl.index("catia_update_assembly")
    assert tl[i:i + 5] == ["catia_update_assembly", "catia_list_constraints", "catia_list_components",
                           "catia_clash_analysis", "catia_save_all"]
    spec = a.checks_spec()
    assert spec["clash"]["allowed"] == [["Bearing_6204.1", "Shaft.1"]]
    assert set(spec["components"]["poses"]) == {"Housing.1", "Shaft.1", "Bolt.1", "Bolt.2", "Bearing_6204.1"}
    clash_steps = [s for s in a.steps() if s["tool"] == "catia_clash_analysis"]
    assert clash_steps[0]["timeout_s"] == 900.0  # long step: its own hang limit


def test_instance_names_and_unknown_instances(tmp_path):
    a = new_asm(tmp_path)
    h = a.add(tmp_path / "Housing.CATPart")
    a.add(tmp_path / "Shaft.CATPart")
    with pytest.raises(ScriptError, match="not a .CATPart"):
        a.add(tmp_path / "notes.txt")
    with pytest.raises(ScriptError, match="must look like"):
        a.add(tmp_path / "X.CATPart", instance_name="NoCounter")
    with pytest.raises(ScriptError, match="unknown parent"):
        a.add(tmp_path / "X.CATPart", parent="Nope.1")
    with pytest.raises(ScriptError, match="unknown instance"):
        a.fix("Housng.1", "Fix_Typo")
    with pytest.raises(ScriptError, match="unknown instance"):
        a.contact(h, face(0, 0, 1), "Shaft.2", face(0, 0, 1), "Contact_Typo")
    strict = AssemblyScript("Strict", tmp_path / "s", strict_paths=True)
    with pytest.raises(ScriptError, match="not found"):
        strict.add(tmp_path / "missing.CATPart")
    explicit = a.add(tmp_path / "catalog.CATPart", "Catalog_Part_A.1")
    assert explicit == "Catalog_Part_A.1"


def test_constraint_names_and_pairing_rules(tmp_path):
    a = new_asm(tmp_path)
    h, s = a.add(tmp_path / "H.CATPart"), a.add(tmp_path / "S.CATPart")
    for bad in ("", "Coincidence.1", "Contact.2", "Offset.3", "Fixed", None):
        with pytest.raises(ScriptError):
            a.coincidence(h, axis(0, 0, 1), s, axis(0, 0, 1), bad)
    a.coincidence(h, axis(0, 0, 1), s, axis(0, 0, 1), "Coax_H_S")
    with pytest.raises(ScriptError, match="already used"):
        a.contact(h, face(0, 0, 1), s, face(0, 0, 1), "Coax_H_S")
    with pytest.raises(ScriptError, match="cannot pair"):
        a.coincidence(h, axis(0, 0, 1), s, face(0, 0, 1), "Coax_Mixed")
    with pytest.raises(ScriptError, match="cannot pair"):
        a.contact(h, axis(0, 0, 1), s, axis(0, 0, 1), "Contact_Axes")
    with pytest.raises(ScriptError, match="different components"):
        a.contact(h, face(0, 0, 1), h, face(0, 0, 2), "Contact_Self")
    with pytest.raises(ScriptError):
        a.contact(h, {"face_point": [1, 2]}, s, face(0, 0, 1), "Contact_Short")
    with pytest.raises(ScriptError):
        a.contact(h, {"bogus": [1, 2, 3]}, s, face(0, 0, 1), "Contact_Bogus")
    with pytest.raises(ScriptError):
        a.contact(h, ("face", [1, 2, "x"]), s, face(0, 0, 1), "Contact_Str")


def test_offset_requires_orientation(tmp_path, schemas):
    a = new_asm(tmp_path)
    h, s = a.add(tmp_path / "H.CATPart"), a.add(tmp_path / "S.CATPart")
    with pytest.raises(ScriptError, match="orientation"):
        a.offset(h, face(0, 0, 1), s, face(0, 0, 1), 5, "Dist_H_S_5")
    with pytest.raises(ScriptError):
        a.offset(h, face(0, 0, 1), s, face(0, 0, 1), 5, "Dist_H_S_5", orientation="up")
    with pytest.raises(ScriptError, match="must not be 0"):
        a.offset(h, face(0, 0, 1), s, face(0, 0, 1), 0, "Dist_H_S_0", orientation="same")
    a.offset(h, face(0, 0, 1), s, face(0, 0, 1), 5, "Dist_H_S_5", allow_unsigned=True)
    steps = a.steps()
    assert "orientation" not in steps[-1]["args"]
    valid(a, schemas)


def test_move_rules(tmp_path):
    a = new_asm(tmp_path)
    h, s = a.add(tmp_path / "H.CATPart"), a.add(tmp_path / "S.CATPart")
    with pytest.raises(ScriptError, match="nothing to do"):
        a.move(s)
    with pytest.raises(ScriptError, match="unknown pose key"):
        a.move(s, pose={"tq": 1})
    with pytest.raises(ScriptError):
        a.move(s, tx="5")
    a.fix(h, "Fix_H")
    with pytest.raises(ScriptError, match="BEFORE fix"):
        a.move(h, tx=1)
    a.move(s, tx=1)
    a.coincidence(s, axis(0, 0, 1), h, axis(0, 0, 1), "Coax_S_H")
    with pytest.raises(ScriptError, match="BEFORE constraining"):
        a.move(s, tx=1)
    sub = a.add(tmp_path / "Sub.CATProduct")
    child = a.add(tmp_path / "C.CATPart", parent=sub)
    with pytest.raises(ScriptError, match="top-level"):
        a.move(child, tx=1)


# ── pose tracking == catia_move_component ────────────────────────────────────────────────
MOVES = [
    [dict(tx=10)],
    [dict(rx=90)],
    [dict(ry=-90), dict(tx=5, ty=-3, tz=2)],
    [dict(rz=90), dict(tx=443)],
    [dict(rx=30, ry=45, rz=60, tx=1, ty=2, tz=3)],
    [dict(rx=180), dict(rz=-90), dict(tx=-528, tz=7.5), dict(ry=13.7, rx=-41)],
]


def _real_pose_after(moves):
    """Run the REAL AssemblyTools._move with a fake component and return its 12 components."""
    from catia_mcp.tools.assembly import AssemblyTools

    tools_ = AssemblyTools(connection=None)
    state = [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0.0, 0.0, 0.0]  # axes X, Y, Z then origin

    def set_components(m):
        state[:] = list(m)

    comp = SimpleNamespace(Position=SimpleNamespace(SetComponents=set_components))
    tools_._component = lambda name: comp
    tools_._position = lambda c: list(state)
    for mv in moves:
        tools_._move({"component": "X.1", **mv})
    return state


@pytest.mark.parametrize("moves", MOVES)
def test_pose_tracking_matches_catia_move_component(tmp_path, moves):
    a = new_asm(tmp_path)
    inst = a.add(tmp_path / "X.CATPart")
    for mv in moves:
        a.move(inst, **mv)
    mine = a.pose(inst)
    real = _real_pose_after(moves)
    assert [v for ax in mine.axes for v in ax] == pytest.approx(real[:9], abs=1e-12)
    assert mine.origin == pytest.approx(real[9:], abs=1e-12)
    # and the emitted steps carry only non-zero values
    for s, mv in zip([s for s in a.steps() if s["tool"] == "catia_move_component"], moves):
        assert s["args"] == {"component": inst, **{k: float(v) for k, v in mv.items() if v}}


def components_json(entries):
    return json.dumps([{"path": p, "part_number": p.split(".")[0], "file": None, "origin": o, "axes": ax}
                       for p, o, ax in entries])


def test_verify_poses_detects_a_moved_component(tmp_path):
    a = new_asm(tmp_path)
    h, s = a.add(tmp_path / "H.CATPart"), a.add(tmp_path / "S.CATPart")
    a.fix(h, "Fix_H")
    a.move(s, tx=100)
    a.move(s, rz=180)
    ident = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    flipped = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]
    good = components_json([("H.1", [0, 0, 0], ident), ("S.1", [100, 0, 0], flipped)])
    assert a.verify_poses(good) == []
    swapped_side = components_json([("H.1", [0, 0, 0], ident), ("S.1", [-100, 0, 0], flipped)])
    problems = a.verify_poses(swapped_side)
    assert len(problems) == 1 and "S.1" in problems[0] and "200.000 mm" in problems[0]
    turned = components_json([("H.1", [0, 0, 0], ident), ("S.1", [100, 0, 0], ident)])
    assert a.verify_poses(turned)
    assert any("missing" in x for x in a.verify_poses(components_json([("H.1", [0, 0, 0], ident)])))
    assert a.verify_poses("not json")


def test_assembly_checks_flag_solver_failures_and_clashes(tmp_path):
    a = new_asm(tmp_path)
    h, s = a.add(tmp_path / "H.CATPart"), a.add(tmp_path / "S.CATPart")
    a.fix(h, "Fix_H")
    a.move(s, tx=10)
    a.finish(views=(), allow_clashes=[("S.1", "H.1")])
    spec = a.checks_spec()
    ident = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    comps = components_json([("H.1", [0, 0, 0], ident), ("S.1", [10, 0, 0], ident)])
    clash_ok = 'NO CLASH, 0 contact(s).\n{"clashes": 0, "contacts": 0, "list": []}'
    clash_bad = '1 CLASH(ES), 0 contact(s).\n{"clashes": 1, "contacts": 0, "list": [{"between": ["S.1", "X.1"], "catia_value": 0.9}]}'
    n = len(a.steps())

    def res(over):
        out = []
        for i, st in enumerate(a.steps(), 1):
            out.append(SimpleNamespace(index=i, tool=st["tool"], ok=True, output=over.get(i, "ok")))
        return out

    base = {spec["solve"]["step"]: "Assembly updated. 3 constraint(s), all OK.",
            spec["components"]["step"]: comps, spec["clash"]["step"]: clash_ok}
    assert verify(spec, res(base)) == []
    assert any("NOT OK" in p for p in verify(spec, res({**base, spec["solve"]["step"]: "1 constraint(s), NOT OK: ['Coax']"})))
    assert any("unexpected clash" in p for p in verify(spec, res({**base, spec["clash"]["step"]: clash_bad})))
    moved = components_json([("H.1", [0, 0, 0], ident), ("S.1", [-10, 0, 0], ident)])
    assert any(p.startswith("pose:") for p in verify(spec, res({**base, spec["components"]["step"]: moved})))
    assert n > 5


def test_assembly_run_with_fake_server(tmp_path, schemas):
    a = new_asm(tmp_path)
    h, s = a.add(tmp_path / "H.CATPart"), a.add(tmp_path / "S.CATPart")
    a.fix(h, "Fix_H")
    a.move(s, tx=10)
    a.finish(views=("isometric",))
    ident = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    srv = FakeServer(schemas, {
        "catia_update_assembly": "Assembly updated. 1 constraint(s), all OK.",
        "catia_list_components": components_json([("H.1", [0, 0, 0], ident), ("S.1", [10, 0, 0], ident)]),
        "catia_clash_analysis": 'NO CLASH, 0 contact(s).\n{"clashes": 0, "contacts": 0, "list": []}'})
    res = a.run(server=srv)
    assert res.ok, res.text()
    assert a.run(server=srv, dry_run=True).ok


# ── templates ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("template", ["part_template.py", "assembly_template.py"])
def test_templates_run_offline_and_validate(template, tmp_path, monkeypatch, schemas, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CATIA_OUT_DIR", str(tmp_path / "out"))
    path = REPO / "templates" / template
    assert path.is_file(), path
    monkeypatch.setattr("sys.argv", [str(path), "--dry-run"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(path), run_name="__main__")
    out = capsys.readouterr().out
    assert e.value.code == 0, out
    assert "DRY RUN OK" in out
    text = path.read_text(encoding="utf-8")
    assert "# FILL:" in text
    for forbidden in ("C:\\Users", "McMaster"):
        assert forbidden not in text


def test_inspect_steps_valid_and_refuses_non_parts(tmp_path, schemas):
    from catia_mcp import batch

    steps = inspect_steps([tmp_path / "A.CATPart", tmp_path / "B.CATPart"], faces=True)
    norm, problems = batch.normalize_steps(steps)
    assert problems == [] and batch.validate_batch(norm, schemas) == []
    assert [s["tool"] for s in steps].count("catia_list_faces") == 2
    assert inspect_steps([tmp_path / "A.CATPart"])[-2]["tool"] == "catia_get_bounding_box"
    with pytest.raises(ScriptError):
        inspect_steps([tmp_path / "A.CATProduct"])
    with pytest.raises(ScriptError):
        inspect_steps([])
