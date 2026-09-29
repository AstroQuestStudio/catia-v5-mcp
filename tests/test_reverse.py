"""Offline tests of the reverse-engineering module: pure logic on hand-made reader output and a fake COM
object graph. No CATIA needed (CATIA_MCP_OFFLINE=1 is set by conftest)."""

from __future__ import annotations

import json
import math
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from catia_mcp import batch
from catia_mcp.scripting import PartScript, ScriptError, load_schemas
from catia_mcp.tools import reverse as rv

AX_XY = [0, 0, 0, 1, 0, 0, 0, 1, 0]


@pytest.fixture(scope="module")
def schemas():
    return load_schemas()


# ---- fixtures: raw reader output ------------------------------------------------------------


def circle(name, cx, cy, r, construction=False):
    return {"name": name, "kind": "circle", "construction": construction, "center": [cx, cy], "radius": r}


def line(name, s, e, construction=False):
    return {"name": name, "kind": "line", "construction": construction, "start": list(s), "end": list(e)}


def arc(name, c, r, a0, a1):
    s = [c[0] + r * math.cos(a0), c[1] + r * math.sin(a0)]
    e = [c[0] + r * math.cos(a1), c[1] + r * math.sin(a1)]
    return {"name": name, "kind": "arc", "construction": False, "center": list(c), "radius": r,
            "a0": a0, "a1": a1, "start": s, "end": e}


def point(name, at, construction=True):
    return {"name": name, "kind": "point", "construction": construction, "at": list(at)}


def sketch(name, elements, axis=AX_XY, constraints=None, center_line=None):
    return {"name": name, "axis": list(axis), "elements": elements, "constraints": constraints or [],
            "center_line": center_line, "visible": False, "errors": []}


def feat(name, typ, sk=None, **data):
    return {"name": name, "type": typ, "index": 0, "up_to_date": True, "inactive": False, "visible": True,
            "sketch": sk, "data": data, "read_errors": []}


def pad(name, sk, length, **kw):
    d = dict(direction_type=0, orientation=0, symmetric=False, thin=False, l1_mode=0, l1_value=length,
             l2_mode=0, l2_value=0.0)
    d.update(kw)
    return feat(name, "Pad", sk, **d)


def pocket(name, sk, depth, **kw):
    d = dict(direction_type=0, orientation=1, symmetric=False, thin=False, l1_mode=0, l1_value=depth,
             l2_mode=0, l2_value=0.0)
    d.update(kw)
    return feat(name, "Pocket", sk, **d)


def body(name, shapes=(), sketches=(), main=False, visible=True, volume=None):
    return {"name": name, "main": main, "shapes": list(shapes), "sketches": list(sketches),
            "hybrid_shapes": [], "visible": visible, "volume_mm3": volume}


def raw_part(bodies, name="Sample", **extra):
    raw = {"document": {"name": name + ".CATPart", "full_name": None, "type": "Part"},
           "part": {"name": name, "density": 1000.0}, "bodies": bodies, "hybrid_bodies": [], "parameters": [],
           "relations": [], "errors": [], "measures": {}}
    raw.update(extra)
    return raw


def l_profile():
    pts = [(0, 0), (60, 0), (60, 8), (8, 8), (8, 50), (0, 50)]
    els = [point(f"Point.{i + 1}", p) for i, p in enumerate(pts)]
    els += [line(f"Droite.{i + 1}", pts[i], pts[(i + 1) % 6]) for i in range(6)]
    return els


# ---- geometry helpers -----------------------------------------------------------------------


@pytest.mark.parametrize("axis,plane,sign,offset", [
    ([0, 0, 0, 1, 0, 0, 0, 1, 0], "xy", 1, 0.0),
    ([0, 0, 10, 1, 0, 0, 0, 1, 0], "xy", 1, 10.0),
    ([0, 0, 0, 0, 1, 0, 0, 0, 1], "yz", 1, 0.0),
    ([0, 0, 0, 0, 0, 1, 1, 0, 0], "zx", 1, 0.0),
    ([5, 0, 0, 0, 1, 0, 0, 0, 1], "yz", 1, 5.0),
    ([0, 0, 0, -1, 0, 0, 0, 1, 0], "xy", -1, 0.0),
])
def test_frame_to_plane(axis, plane, sign, offset):
    info = rv.frame_to_plane(axis)
    assert info["plane"] == plane and info["normal_sign"] == sign and info["offset"] == offset


def test_frame_to_plane_tilted_is_none():
    c = math.cos(math.radians(30))
    s = math.sin(math.radians(30))
    assert rv.frame_to_plane([0, 0, 0, 1, 0, 0, 0, c, s]) is None


def test_map_point_identity_offset_and_reflection():
    m = rv.frame_to_plane([0, 0, 10, 1, 0, 0, 0, 1, 0])["matrix"]
    assert rv.map_point(m, 3, 4) == [3.0, 4.0]                 # offset plane keeps the in-plane coordinates
    m = rv.frame_to_plane([0, 0, 0, -1, 0, 0, 0, 1, 0])["matrix"]
    assert rv.map_point(m, 3, 4) == [-3.0, 4.0] and rv.is_reflection(m)
    m = rv.frame_to_plane([0, 2, 0, 0, 1, 0, 0, 0, 1])["matrix"]     # yz plane, origin shifted along Y
    assert rv.map_point(m, 1, 1) == [3.0, 1.0]


def test_sanitize_and_replay_name_are_accepted_by_the_dsl():
    used: set[str] = set()
    names = [rv.replay_name(n, used) for n in ("Pad.1", "Extrusion.3", "Corps principal", "PartBody", "Esquisse é.2",
                                               "Base_L20")]
    assert names[-1] == "Base_L20"
    assert len(set(names)) == len(names)
    for n in names:
        from catia_mcp.scripting import check_name

        check_name(n, "x")
    assert rv.replay_name("Pad.1", used) not in names           # second time: unique again


def test_chain_profiles_closed_rectangle_and_open_chain():
    pts = [(0, 0), (10, 0), (10, 5), (0, 5)]
    curves = [{"kind": "line", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]), "src": f"L{i}"}
              for i in range(4)]
    chains, left = rv.chain_profiles(curves)
    assert len(chains) == 1 and chains[0]["closed"] and not left
    assert len(chains[0]["segments"]) == 4
    chains, left = rv.chain_profiles(curves[:3])
    assert len(chains) == 1 and not chains[0]["closed"]


def test_chain_profiles_arc_direction_follows_travel():
    a = {"kind": "arc", "start": [10, 0], "end": [0, 10], "center": [0, 0], "radius": 10, "ccw": True, "src": "A"}
    ln1 = {"kind": "line", "start": [0, 10], "end": [0, 0], "src": "L1"}
    ln2 = {"kind": "line", "start": [0, 0], "end": [10, 0], "src": "L2"}
    (chain,), left = rv.chain_profiles([ln2, a, ln1])
    assert chain["closed"] and not left
    dirs = [s.get("direction") for s in chain["segments"] if s["type"] == "arc"]
    # walked start->end when entered at its start, reversed when entered at its end
    assert dirs in (["ccw"], ["cw"])
    # entering the chain by the other end flips the direction
    (chain2,), _ = rv.chain_profiles([ln1, a, ln2])
    assert chain2["closed"]


def test_chain_profiles_branching_vertex_is_left_alone():
    curves = [{"kind": "line", "start": [0, 0], "end": [10, 0], "src": "a"},
              {"kind": "line", "start": [0, 0], "end": [0, 10], "src": "b"},
              {"kind": "line", "start": [0, 0], "end": [-10, 0], "src": "c"}]
    chains, left = rv.chain_profiles(curves)
    assert not chains and len(left) == 3


def test_open_ends():
    els = [line("a", (0, 0), (10, 0)), line("b", (10, 0), (10, 10)), line("c", (10, 10), (0, 10))]
    assert len(rv.open_ends(els)) == 2
    els.append(line("d", (0, 10), (0, 0)))
    assert rv.open_ends(els) == []
    assert rv.open_ends([circle("c", 0, 0, 5)]) == []


def test_classify_drops_vertex_points_keeps_free_ones():
    els = l_profile() + [point("Free", (30, 30), construction=False), point("Free2", (31, 31), construction=True)]
    g = rv.classify_elements(els)
    assert len(g["curves"]) == 6 and len(g["implicit_points"]) == 6
    assert {p["name"] for p in g["points"]} == {"Free", "Free2"}


# ---- build_spec + script --------------------------------------------------------------------


def bracket_raw():
    sk = sketch("Sk_L_Profile", l_profile())
    hole1 = feat("Hole_Base_D9", "Hole", "Esq_Position_Hole_Base_D9", type=0, anchor_mode=0, bottom_type=0,
                 threading_mode=1, diameter=9.0, bottom_limit_mode=0, depth=8.0, origin=[40, 0, 15],
                 direction=[0, 1, 0])
    hole_sk = sketch("Esq_Position_Hole_Base_D9", [point("Point.1", (40, 15), construction=False)],
                     axis=[0, 0, 0, 1, 0, 0, 0, 0, 1])
    prof_sk = sketch("Esq_Profil_Hole_Base_D9", [line("Droite.1", (0, 0), (0, 8)), line("Droite.2", (0, 0), (4, 0))])
    fil = feat("Round_Inner_R4", "ConstRadEdgeFillet", radius=4.0, propagation=1, points=[[8, 8, 15]],
               unreadable_edges=0)
    cha = feat("Chamfer_1", "Chamfer", mode=1, propagation=0, orientation=0, length1=1.0, length2=1.0, angle=45.0,
               points=[[60, 8, 15]], unreadable_edges=0)
    return raw_part([body("Bracket_L_Resultat", [pad("Body_L_W30", "Sk_L_Profile", 30), hole1, fil, cha],
                          [sk, hole_sk, prof_sk], main=True)], name="Bracket_L")


def test_build_spec_bracket_recognises_everything_and_replays(schemas):
    spec = rv.build_spec(bracket_raw())
    assert spec["replay"]["complete"] is True and spec["opaque"] == []
    kinds = [f["kind"] for f in spec["bodies"][0]["features"]]
    assert kinds == ["pad", "hole", "fillet", "chamfer"]
    ops = [o["op"] for o in spec["_ops"]]
    assert ops == ["sketch", "pad", "hole", "fillet", "chamfer"]        # hole sketches are not redrawn
    sk_op = spec["_ops"][0]
    assert sk_op["elements"][0]["kind"] == "profile" and sk_op["elements"][0]["closed"] is True
    assert len(sk_op["elements"][0]["segments"]) == 6
    res = rv.validate_replay(spec, schemas)
    assert res["valid"], res
    assert spec["sketches"]["Sk_L_Profile"]["dof"]["state"] == "unknown"


def test_script_renders_compiles_and_writes_the_scenario(tmp_path, monkeypatch, schemas):
    spec = rv.build_spec(bracket_raw())
    spec["checks"] = {"result": {"volume_mm3": 23616.106, "bbox": {"size": [60.0, 50.0, 30.0]}}}
    text = rv.render_script(spec)
    compile(text, "replay.py", "exec")
    for banned in ("C:\\", "Users"):
        assert banned not in text
    script = tmp_path / "replay.py"
    script.write_text(text, encoding="utf-8")
    monkeypatch.setenv("CATIA_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(sys, "argv", [str(script), "--json"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(script), run_name="__main__")
    assert e.value.code == 0
    scenario = next((tmp_path / "out").rglob("*.json"))
    data = json.loads(scenario.read_text(encoding="utf-8"))
    steps, problems = batch.normalize_steps(data["steps"])
    assert not problems and not batch.validate_batch(steps, schemas)
    tools = [s["tool"] for s in data["steps"]]
    assert "catia_close_all" not in tools           # never closes somebody else's documents
    assert tools.count("catia_hole") == 1 and tools.count("catia_fillet") == 1


def flange_raw():
    disc = sketch("Sk_Disc", [circle("Cercle.1", 0, 0, 40)])
    hub_sk = sketch("Sk_Hub", [circle("Cercle.1", 0, 0, 22)], axis=[0, 0, 10, 1, 0, 0, 0, 1, 0])
    bore_sk = sketch("Sk_Bore", [circle("Cercle.1", 0, 0, 15)])
    hub = body("Body_Hub", [pad("Hub_D44_H12", "Sk_Hub", 12)], [hub_sk])
    bore = body("Body_Bore", [pad("Bore_Cyl_D30", "Sk_Bore", 44, symmetric=True, l1_value=22.0)], [bore_sk])
    add = feat("Add_Hub", "Assemble")
    add["operand"] = hub
    rem = feat("Remove_Bore_D30", "Remove")
    rem["operand"] = bore
    main = body("Flange_Resultat", [pad("Disc_D80_T10", "Sk_Disc", 10), add, rem],
                [disc], main=True)
    return raw_part([main], name="Flange")


def test_boolean_operand_bodies_are_replayed_in_order(schemas):
    spec = rv.build_spec(flange_raw())
    assert spec["replay"]["complete"], spec["opaque"]
    ops = [(o["op"], o.get("name")) for o in spec["_ops"]]
    names = [n for _, n in ops]
    assert [o for o, _ in ops] == ["sketch", "pad", "body", "sketch", "pad", "combine", "body", "sketch", "pad",
                                   "combine"]
    assert names.index("Body_Hub") < names.index("Add_Hub") < names.index("Body_Bore")
    sk_hub = next(o for o in spec["_ops"] if o["op"] == "sketch" and o["source"] == "Sk_Hub")
    assert sk_hub["offset"] == 10.0 and sk_hub["plane"] == "xy"
    bore_pad = [o for o in spec["_ops"] if o["op"] == "pad"][-1]
    assert bore_pad["symmetric"] and bore_pad["height"] == 44.0        # symmetric length is per side in CATIA
    assert all(o["target"] == "Flange_Resultat" for o in spec["_ops"] if o["op"] == "combine")
    assert rv.validate_replay(spec, schemas)["valid"]


def test_directions_follow_the_replay_plane_normal():
    # yz plane sketch, regular orientation: normal +X, so "normal"
    sk = sketch("Sk", [circle("c", 0, 0, 5)], axis=[0, 0, 0, 0, 1, 0, 0, 0, 1])
    spec = rv.build_spec(raw_part([body("R", [pad("P1", "Sk", 10, orientation=1)], [sk], main=True)]))
    assert spec["_ops"][1]["direction"] == "reverse"
    spec = rv.build_spec(raw_part([body("R", [pad("P1", "Sk", 10, orientation=0)], [sk], main=True)]))
    assert "direction" not in spec["_ops"][1]
    # sketch normal flipped (H = -X): regular orientation extrudes toward -Z, so the replay must reverse
    flipped = sketch("Sk", [circle("c", 3, 0, 5)], axis=[0, 0, 0, -1, 0, 0, 0, 1, 0])
    spec = rv.build_spec(raw_part([body("R", [pad("P1", "Sk", 10, orientation=0)], [flipped], main=True)]))
    op = spec["_ops"][0]["elements"][0]
    assert op["cx"] == -3.0                                            # mirrored into the replay frame
    assert spec["_ops"][1]["direction"] == "reverse"


def test_pocket_direction_mapping():
    base = pad("P", "Sk0", 10)
    sk0 = sketch("Sk0", [circle("c", 0, 0, 20)])
    sk1 = sketch("Sk1", [circle("c", 0, 0, 3)], axis=[0, 0, 10, 1, 0, 0, 0, 1, 0])
    for orient, expected in ((1, "normal"), (0, "reverse")):
        spec = rv.build_spec(raw_part([body("R", [base, pocket("K", "Sk1", 4, orientation=orient)], [sk0, sk1],
                                            main=True)]))
        assert spec["_ops"][-1]["direction"] == expected
    up = rv.build_spec(raw_part([body("R", [base, pocket("K", "Sk1", 0, l1_mode=1, l1_value=0.0)], [sk0, sk1],
                                      main=True)]))
    assert up["_ops"][-1]["limit"] == "up_to_next" and "depth" not in up["_ops"][-1]


def revolve_raw(center_line):
    els = [point(f"Point.{i}", p) for i, p in enumerate([(0, 0), (0, 10), (30, 10), (30, 0)])]
    pts = [(0, 0), (0, 10), (30, 10), (30, 0)]
    els += [line(f"Droite.{i + 1}", pts[i], pts[(i + 1) % 4]) for i in range(4)]
    els.append(line("Axe horizontal", (-5, 0), (35, 0), construction=False))
    sk = sketch("Sk_Turned", els, center_line=center_line)
    shaft = feat("Turned_Body", "Shaft", "Sk_Turned", angle1=360.0, angle2=0.0, thin=False)
    return raw_part([body("Pin_Resultat", [shaft], [sk], main=True)], name="Pin")


def test_revolution_axis_h_line_and_generic(schemas):
    h = rv.build_spec(revolve_raw({"kind": "h", "name": "Axe horizontal"}))
    assert h["replay"]["complete"], h["opaque"]
    shaft = [o for o in h["_ops"] if o["op"] == "shaft"][0]
    assert shaft["axis"] == "h" and shaft["angle"] == 360.0
    # the DSL refuses profiles crossing the axis: the 'Axe horizontal' line is a member of the sketch; here it
    # must not have been drawn as a profile line
    assert rv.validate_replay(h, schemas)["valid"]
    ln = rv.build_spec(revolve_raw({"kind": "line", "name": "Axe horizontal"}))
    # the horizontal centre line through the origin is equivalent to the absolute axis
    assert [o for o in ln["_ops"] if o["op"] == "shaft"][0]["axis"] == "h"
    none = rv.build_spec(revolve_raw(None))
    assert not none["replay"]["complete"]
    assert "no readable revolution axis" in none["opaque"][0]["reason"]


def test_opaque_features_are_named_explained_and_never_dropped():
    sk = sketch("Sk", [circle("c", 0, 0, 20)])
    good = pad("Base_T10", "Sk", 10)
    thin = pad("Thin_Pad", "Sk", 2, thin=True)
    rect = feat("Pattern_Rect", "RectPattern", item="Hole_A", count1=3, spacing1=10.0, count2=1, spacing2=0.0)
    unknown = feat("Fancy", "SomethingNew")
    stale = pad("Stale_Pad", "Sk", 3)
    stale["up_to_date"] = False
    inactive = pad("Off_Pad", "Sk", 3)
    inactive["inactive"] = True
    spline_sk = sketch("Sk_Spline", [{"name": "Spline.1", "kind": "spline", "construction": False,
                                      "n_control_points": 4}])
    on_spline = pad("Spline_Pad", "Sk_Spline", 5)
    two = pad("TwoLimits", "Sk", 5, l2_value=2.0)
    raw = raw_part([body("R", [good, thin, rect, unknown, stale, inactive, on_spline, two],
                         [sk, spline_sk], main=True)])
    spec = rv.build_spec(raw)
    by = {o["name"]: o for o in spec["opaque"]}
    assert set(by) >= {"Thin_Pad", "Pattern_Rect", "Fancy", "Off_Pad", "Spline_Pad"}
    assert "Stale_Pad" not in by                  # stale values are still described, with a note
    assert any("Stale_Pad" in n and "not up to date" in n for n in spec["notes"])
    assert "thin" in by["Thin_Pad"]["reason"]
    assert by["Pattern_Rect"]["known"]["count1"] == 3
    assert "deactivated" in by["Off_Pad"]["reason"]
    assert "unsupported geometry" in by["Spline_Pad"]["reason"]
    assert spec["replay"]["complete"] is False and spec["replay"]["opaque_solid_features"] >= 5
    feats = {f["name"]: f for f in spec["bodies"][0]["features"]}
    assert feats["Base_T10"]["recognised"] and not feats["Fancy"]["recognised"]
    assert feats["TwoLimits"]["recognised"]       # a second height IS supported (same direction)
    script = rv.render_script(spec)
    assert "INCOMPLETE" in script


def test_wireframe_elements_are_listed_not_replayed():
    raw = bracket_raw()
    raw["bodies"][0]["hybrid_shapes"] = [{"name": "Plane_Offset", "type": "HybridShapePlaneOffset"}]
    raw["hybrid_bodies"] = [{"name": "Surfaces", "visible": True,
                             "elements": [{"name": "Extrude.1", "type": "HybridShapeExtrude"}]}]
    spec = rv.build_spec(raw)
    assert {w["name"] for w in spec["wireframe"]} == {"Plane_Offset", "Extrude.1"}
    assert all(not o["affects_solid"] for o in spec["opaque"])
    assert spec["replay"]["complete"] is True      # wireframe does not change the solid


def test_tilted_sketch_plane_is_opaque_with_reason():
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    sk = sketch("Sk", [circle("c", 0, 0, 5)], axis=[0, 0, 0, 1, 0, 0, 0, c, s])
    spec = rv.build_spec(raw_part([body("R", [pad("P", "Sk", 5)], [sk], main=True)]))
    assert "not parallel to an origin plane" in spec["opaque"][0]["reason"]


def test_unused_sketch_replayed_and_unmerged_body_flag(schemas):
    sk = sketch("Sk", [circle("c", 0, 0, 20)])
    spare = sketch("Spare", [circle("c", 5, 5, 2)])
    other = body("Aux", [pad("Aux_Pad", "SkA", 3)], [sketch("SkA", [circle("c", 0, 0, 3)])])
    spec = rv.build_spec(raw_part([body("R", [pad("P", "Sk", 10)], [sk, spare], main=True), other]))
    assert spec["replay"]["unmerged_bodies"] == ["Aux"]
    ops = [(o["op"], o.get("name")) for o in spec["_ops"]]
    assert ("sketch", "Spare") in ops and ops.index(("sketch", "Spare")) > ops.index(("pad", "P"))
    assert rv.validate_replay(spec, schemas)["valid"]
    assert "allow_unmerged=True" in rv.render_script(spec)


def test_sketch_spec_carries_constraints_by_element_id():
    csts = [{"name": "Longueur.1", "type": 5, "type_name": "length", "mode": "driving", "status": "OK",
             "value": 60.0, "elements": ["Droite.1"]},
            {"name": "Parallelisme.1", "type": 8, "type_name": "parallelism", "mode": "driving", "status": "OK",
             "elements": ["Droite.1", "Axe horizontal"]}]
    spec = rv.build_spec(raw_part([body("R", [pad("P", "S", 5)], [sketch("S", l_profile(), constraints=csts)],
                                        main=True)]))
    c = spec["sketches"]["S"]["constraints"]
    ids = {g["name"]: g["id"] for g in spec["sketches"]["S"]["geometry"]}
    assert c[0]["elements"] == [ids["Droite.1"]] and c[0]["value"] == 60.0
    assert c[1]["elements"] == [ids["Droite.1"], "Axe horizontal"]      # unknown reference kept by name
    assert spec["replay"]["sketch_constraints_replayed"] is False


def test_public_spec_hides_internal_keys_and_is_json():
    spec = rv.build_spec(bracket_raw())
    pub = rv.public_spec(spec)
    assert not any(k.startswith("_") for k in pub)
    assert json.loads(json.dumps(pub))["format"] == rv.FORMAT


def test_mirror_recognised_only_on_origin_plane(schemas):
    sk = sketch("S", [circle("c", 10, 0, 3)])
    m = feat("Mirror_YZ", "Mirror", plane="yz")
    spec = rv.build_spec(raw_part([body("R", [pad("P", "S", 5), m], [sk], main=True)]))
    assert spec["_ops"][-1] == {"op": "mirror", "plane": "yz", "name": "Mirror_YZ"}
    bad = feat("Mirror_Face", "Mirror", plane=None)
    spec = rv.build_spec(raw_part([body("R", [pad("P", "S", 5), bad], [sk], main=True)]))
    assert spec["opaque"][0]["name"] == "Mirror_Face"


def test_hole_variants_and_refusals():
    h = dict(type=0, anchor_mode=0, bottom_type=0, threading_mode=1, diameter=6.0, bottom_limit_mode=0, depth=5.0,
             origin=[1, 2, 3], direction=[0, 0, -1])
    op = rv._hole_op({"data": h})
    assert op["kind"] == "simple" and op["depth"] == 5.0 and "threaded" not in op
    cb = rv._hole_op({"data": {**h, "type": 2, "head_diameter": 10.0, "head_depth": 3.0}})
    assert cb["kind"] == "counterbored" and cb["head_depth"] == 3.0
    cs = rv._hole_op({"data": {**h, "type": 3, "head_angle": 90.0, "head_depth": 2.0}})
    assert cs["kind"] == "countersunk" and cs["head_diameter"] == pytest.approx(10.0)
    assert rv._hole_op({"data": {**h, "threading_mode": 0}})["threaded"] is True
    up = rv._hole_op({"data": {**h, "bottom_limit_mode": 3, "up_to_point": [1, 2, 0], "depth": None}})
    assert up["up_to_point"] == [1, 2, 0] and "depth" not in up
    for bad in ({"anchor_mode": 1}, {"bottom_type": 1}, {"bottom_limit_mode": 3}, {"bottom_limit_mode": 1},
                {"type": 4}, {"origin": None}):
        with pytest.raises(rv._Unrecognised):
            rv._hole_op({"data": {**h, **bad}})


def test_fillet_and_chamfer_refusals():
    good = {"radius": 2.0, "propagation": 1, "points": [[0, 0, 0]], "unreadable_edges": 0}
    assert rv._dress_op({"data": good}, "fillet")["radius"] == 2.0
    for bad in ({"propagation": 0}, {"points": []}, {"unreadable_edges": 1}):
        with pytest.raises(rv._Unrecognised):
            rv._dress_op({"data": {**good, **bad}}, "fillet")
    ch = {"mode": 0, "propagation": 0, "orientation": 0, "length1": 2.0, "length2": 3.0, "angle": 45.0,
          "points": [[0, 0, 0]], "unreadable_edges": 0}
    assert rv._dress_op({"data": ch}, "chamfer")["length2"] == 3.0
    ang = rv._dress_op({"data": {**ch, "mode": 1, "angle": 30.0}}, "chamfer")
    assert ang["angle"] == 30.0 and "length2" not in ang
    with pytest.raises(rv._Unrecognised):
        rv._dress_op({"data": {**ch, "orientation": 1}}, "chamfer")


# ---- compare --------------------------------------------------------------------------------


def _m(vol, size, cog=(1, 2, 3), moments=(1.0, 2.0, 3.0), mass=0.1):
    return {"volume_mm3": vol, "cog_mm": list(cog),
            "bbox": {"x": [0, size[0]], "y": [0, size[1]], "z": [0, size[2]]},
            "inertia": {"mass_kg": mass, "principal_moments_kg_mm2": list(moments)}}


def test_compare_measures_pass_and_fail():
    a = _m(1000.0, (10, 20, 30))
    assert rv.compare_measures(a, _m(1000.5, (10, 20, 30.01)))["ok"]
    bad = rv.compare_measures(a, _m(1010.0, (10, 20, 30)))
    assert not bad["ok"] and [c for c in bad["checks"] if not c["ok"]][0]["name"] == "volume"
    bad = rv.compare_measures(a, _m(1000.0, (10, 20.2, 30)))
    assert any(c["name"] == "bbox_max_y" and not c["ok"] for c in bad["checks"])
    # inertia is compared per unit of mass: a different density must not fail an identical shape
    assert rv.compare_measures(a, _m(1000.0, (10, 20, 30), moments=(2.0, 4.0, 6.0), mass=0.2))["ok"]


# ---- audit ----------------------------------------------------------------------------------


def defective_raw():
    good_sk = sketch("Sk_Good", [circle("Cercle.1", 0, 0, 10)])
    open_sk = sketch("Sk_Open", [line("Droite.1", (0, 0), (10, 0)), line("Droite.2", (10, 0), (10, 10)),
                                 line("Droite.3", (10, 10), (0, 10))])
    default_sk = sketch("Sketch.3", [circle("Cercle.1", 0, 0, 10)])
    unused = sketch("Sk_Unused", [circle("Cercle.1", 1, 1, 2)])
    broken = sketch("Sk_Broken", [line("Droite.1", (0, 0), (20, 0))],
                    constraints=[{"name": "Longueur.1", "type": 5, "type_name": "length", "mode": "driving",
                                  "status": "not satisfied", "elements": ["Droite.1"]}])
    fillet_first = feat("Fillet_Early", "ConstRadEdgeFillet", radius=1.0, propagation=1, points=[[0, 0, 0]],
                        unreadable_edges=0)
    bad_pad = pad("Pad_Stale", "Sk_Good", 5)
    bad_pad["up_to_date"] = False
    off = pocket("Pocket_Off", "Sk_Good", 1)
    off["inactive"] = True
    main = body("PartBody", [fillet_first, pad("Extrusion.1", "Sk_Good", 10), bad_pad, off,
                             pad("Pad_Open", "Sk_Open", 3), pad("Pad_Default", "Sketch.3", 2)],
                [good_sk, open_sk, default_sk, unused, broken], main=True, volume=1000.0)
    empty = body("Body.2", [], [], visible=True)
    hidden = body("Hidden_Body", [pad("Hb_Pad", "Sk_H", 1)], [sketch("Sk_H", [circle("c", 0, 0, 1)])],
                  visible=False)
    return raw_part([main, empty, hidden], name="Bad")


def test_audit_finds_each_defect_once_and_ranks_by_severity():
    res = rv.audit_part(defective_raw())
    issues = res["issues"]
    codes = [i["code"] for i in issues]
    sev = [i["severity"] for i in issues]
    assert sev == sorted(sev, key=rv.SEVERITIES.index)         # errors first
    assert "tree.default_name" in codes and "tree.empty_body" in codes
    assert "sketch.not_closed" in codes and "sketch.unused" in codes and "sketch.constraint_error" in codes
    assert "feature.not_up_to_date" in codes and "feature.inactive" in codes
    assert "tree.hidden_body" in codes and "tree.order" in codes and "tree.unmerged_body" in codes
    where = {(i["code"], i["where"]) for i in issues}
    assert ("sketch.not_closed", "PartBody/Sk_Open") in where
    assert ("sketch.unused", "PartBody/Sk_Unused") in where
    assert ("tree.empty_body", "Body.2") in where
    # one report per defect
    assert len(where) == len(issues)
    assert res["stats"]["error"] >= 3
    assert {n["code"] for n in res["not_checked"]} >= {"sketch.iso_constrained"}


def test_audit_fixes_are_valid_tool_calls(schemas):
    for issue in rv.audit_part(defective_raw())["issues"]:
        for step in issue.get("fix", []):
            assert step["tool"] in schemas, step
        if issue.get("fix"):
            steps, problems = batch.normalize_steps([[s["tool"], s["args"]] for s in issue["fix"]])
            assert not problems
            assert not batch.validate_batch(steps, schemas), issue
        assert issue.get("fix") or issue.get("manual"), issue     # every point says how to fix it


def test_audit_default_name_fix_activates_the_owning_body():
    issues = [i for i in rv.audit_part(defective_raw())["issues"]
              if i["code"] == "tree.default_name" and i["where"].endswith("Extrusion.1")]
    fix = issues[0]["fix"]
    assert fix[0] == {"tool": "catia_activate_body", "args": {"body_name": "PartBody"}}
    assert fix[1]["tool"] == "catia_rename_feature" and fix[1]["args"]["old_name"] == "Extrusion.1"
    from catia_mcp.naming import is_default_name

    assert not is_default_name(fix[1]["args"]["new_name"])


def test_audit_clean_part_reports_nothing_but_info():
    raw = bracket_raw()
    for b in raw["bodies"]:
        b["volume_mm3"] = 100.0
    res = rv.audit_part(raw)
    assert [i for i in res["issues"] if i["severity"] in ("error", "warning")] == []
    assert [i["code"] for i in res["issues"]] == ["sketch.unconstrained"]     # the only info: no constraint at all
    assert res["stats"]["error"] == 0


def test_audit_absorbed_body_default_names_have_no_tool_fix():
    add = feat("Add_Hub", "Assemble")
    add["operand"] = body("Body.7", [pad("Pad.4", "S2", 3)], [sketch("S2", [circle("c", 0, 0, 3)])])
    raw = raw_part([body("Main_Res", [pad("Base", "S", 5), add], [sketch("S", [circle("c", 0, 0, 9)])], main=True)])
    res = rv.audit_part(raw)
    inner = [i for i in res["issues"] if "Body.7" in i["where"] and i["code"] == "tree.default_name"]
    assert inner and all("fix" not in i and i["manual"] for i in inner)


def test_audit_severity_filter_is_applied_by_the_tool():
    tools = rv.ReverseTools(SimpleNamespace())
    defs = {d["name"]: d for d in tools.get_tool_definitions()}
    assert defs["catia_audit_model"]["inputSchema"]["properties"]["min_severity"]["enum"] == list(rv.SEVERITIES)


def assembly_raw():
    def comp(path, pn, file="X.CATPart", assembly=False):
        return {"path": path, "instance": path.split("/")[-1], "part_number": pn, "file": file,
                "is_assembly": assembly, "children_count": 0, "pose": {"origin": [0, 0, 0], "axes": []}}

    csts = [
        {"name": "Fix_Base", "type": 0, "type_name": "reference", "status": "OK", "inactive": False,
         "owner": "Root", "references": ["Root/Base.1/!Root/Base.1/"], "instances": ["Base.1"]},
        {"name": "Coincidence.1", "type": 2, "type_name": "on", "status": "not satisfied", "inactive": False,
         "owner": "Root", "references": ["Root/Base.1/!Face", "Root/Lid.1/!Face"], "instances": ["Base.1", "Lid.1"]},
        {"name": "Contact_Lid", "type": 20, "type_name": "surface_contact", "status": "OK", "inactive": True,
         "owner": "Root", "references": ["Root/Lid.1/!F", "Root/Base.1/!F"], "instances": ["Lid.1", "Base.1"]},
    ]
    return {"document": {"name": "Root.CATProduct", "type": "Product"}, "product": {"name": "Root"},
            "components": [comp("Base.1", "Base"), comp("Lid.1", "Lid"), comp("Floating.1", "Part1"),
                           comp("Ghost.1", "Ghost", file=None)],
            "constraints": csts, "errors": []}


def test_audit_product_rules():
    res = rv.audit_product(assembly_raw())
    codes = {(i["code"], i["where"]) for i in res["issues"]}
    assert ("constraint.not_ok", "Coincidence.1") in codes
    assert ("constraint.default_name", "Coincidence.1") in codes
    assert ("constraint.inactive", "Contact_Lid") in codes
    assert ("component.unconstrained", "Floating.1") in codes
    assert ("component.default_part_number", "Floating.1") in codes
    assert ("component.unresolved", "Ghost.1") in codes
    assert not any(w == "Base.1" and c == "component.unconstrained" for c, w in codes)   # the fixed part is exempt
    assert not any(c == "component.unconstrained" and w == "Lid.1" for c, w in codes)     # constrained (even if KO)
    assert res["stats"]["fixed"] == ["Base.1"]
    sev = [i["severity"] for i in res["issues"]]
    assert sev == sorted(sev, key=rv.SEVERITIES.index)


def test_audit_product_without_fixed_component_proposes_a_fix(schemas):
    raw = assembly_raw()
    raw["constraints"] = []
    res = rv.audit_product(raw)
    nf = next(i for i in res["issues"] if i["code"] == "assembly.no_fixed_component")
    step = nf["fix"][0]
    steps, problems = batch.normalize_steps([[step["tool"], step["args"]]])
    assert not problems and not batch.validate_batch(steps, schemas)


def test_instance_of_reference_names():
    assert rv._instance_of("Root/Sub.1/Part.2/!Selection_RSur:(Face)", "Root") == "Sub.1/Part.2"
    assert rv._instance_of("Root/Part.1/!Root/Part.1/", "Root") == "Part.1"
    assert rv._instance_of("", "Root") is None


# ---- fake COM: the reader ---------------------------------------------------------------------


class Fake:
    """Minimal COM-like object: attributes, collections (Count/Item), and an interface name."""

    def __init__(self, _fake_type="Object", **attrs: Any) -> None:
        self._fake_type = _fake_type
        self.__dict__.update(attrs)


def coll(items):
    c = Fake("Collection", Count=len(items))
    c.Item = lambda i, _items=list(items): _items[i - 1]
    return c


def value(v):
    return Fake("Length", Value=v)


def fake_document():
    elements = [(3, 0, 0, 0, 20, 0, "Droite.1"), (5, 0, 5, 5, 4, 0, "Cercle.1")]     # a line and a full circle
    sk = Fake("Sketch", Name="Sk_Fake", GeometricElements=None, CenterLine=None,
              Constraints=coll([]))
    sk.Constraints.BrokenConstraintsCount = 0
    sk.Constraints.UnUpdatedConstraintsCount = 0
    limit1 = Fake("Limit", LimitMode=0, Dimension=value(12.5))
    limit2 = Fake("Limit", LimitMode=0, Dimension=value(0.0))
    pad_ = Fake("Pad", Name="Pad_Fake", Sketch=sk, DirectionType=0, DirectionOrientation=0, IsSymmetric=False,
                IsThin=False, FirstLimit=limit1, SecondLimit=limit2)
    body_ = Fake("Body", Name="Fake_Result", Shapes=coll([pad_]), Sketches=coll([sk]))
    part = Fake("Part", Name="Fake", Density=1000.0, Bodies=coll([body_]), MainBody=body_, HybridBodies=coll([]),
                Relations=coll([]),
                Parameters=Fake("Parameters", RootParameterSet=Fake("Set", AllParameters=coll([
                    Fake("Length", Name="Width", Value=42.0, ValueAsString=lambda: "42mm", Comment="",
                         OptionalRelation=None)]))))
    part.IsUpToDate = lambda o: True
    part.IsInactive = lambda o: False
    part.CreateReferenceFromObject = lambda o: Fake("Reference", DisplayName=o.Name)
    doc = Fake("PartDocument", Name="Fake.CATPart", FullName="Fake.CATPart", Part=part)
    return doc, elements


def fake_vbs(elements):
    def run(code, args):
        if code is rv._SKETCH_VBS:
            out = list(AX_XY)
            for t, cons, *vals, name in elements:
                rec = [0.0] * 15
                rec[0], rec[1] = t, cons
                for k, v in enumerate(vals):
                    rec[2 + k] = v
                rec[14] = name
                out += rec
            return tuple(out)
        if code is rv._SHOW_VBS:
            return (0,)
        raise AssertionError("unexpected VBS")
    return run


def test_reader_walks_a_fake_part_into_a_replayable_spec(schemas):
    doc, els = fake_document()
    # circle record: type 5 -> centre (5,5), radius 4, parameter extents 0..2pi
    els[1] = (5, 0, 5.0, 5.0, 4.0, 0.0, 2 * math.pi, 9.0, 5.0, 9.0, 5.0, 0.0, 0.0, "Cercle.1")
    els[0] = (3, 0, 0.0, 0.0, 20.0, 0.0, 0, 0, 0, 0, 0, 0, 0, "Droite.1")
    reader = rv.ModelReader(SimpleNamespace(), doc, run_vbs=fake_vbs(els), measure=False)
    raw = reader.read_part()
    b = raw["bodies"][0]
    assert b["main"] and b["shapes"][0]["type"] == "Pad" and b["shapes"][0]["data"]["l1_value"] == 12.5
    assert b["shapes"][0]["sketch"] == "Sk_Fake" and b["visible"] is True
    kinds = [e["kind"] for e in b["sketches"][0]["elements"]]
    assert kinds == ["line", "circle"]
    assert raw["parameters"][0]["name"] == "Width" and raw["parameters"][0]["value"] == 42.0
    spec = rv.build_spec(raw)
    # a lone line is not a profile: the pad's sketch keeps only the circle as closed content, the line is replayed
    assert spec["bodies"][0]["features"][0]["recognised"]
    assert spec["_ops"][0]["elements"][0] == {"kind": "circle", "cx": 5.0, "cy": 5.0, "r": 4.0, "construction": False}


def test_reader_reports_unreadable_sketch_instead_of_inventing_geometry():
    doc, els = fake_document()

    def boom(code, args):
        if code is rv._SHOW_VBS:
            return (0,)
        raise RuntimeError("Erreur a l'execution")

    reader = rv.ModelReader(SimpleNamespace(), doc, run_vbs=boom, measure=False)
    raw = reader.read_part()
    sk = raw["bodies"][0]["sketches"][0]
    assert sk["elements"] == [] and "could not be read" in sk["unreadable"]
    assert raw["errors"], "the read error must be surfaced"
    spec = rv.build_spec(raw)
    assert spec["replay"]["complete"] is False and spec["opaque"]


def test_type_name_uses_the_interface_name():
    assert rv.type_name(Fake("Pad")) == "Pad"
    assert rv.type_name(object()) == "Unknown"


# ---- registration ---------------------------------------------------------------------------


def test_tools_are_registered_in_the_server_and_schemas_are_truthful(schemas):
    for name in ("catia_describe_model", "catia_audit_model", "catia_measure_model"):
        assert name in schemas
    from catia_mcp import annotations

    for name in ("catia_describe_model", "catia_audit_model", "catia_measure_model"):
        assert schemas[name]["type"] == "object"
        assert annotations.hints(name)["readOnlyHint"] in (True, False)


def test_replay_script_class_is_the_real_partscript():
    spec = rv.build_spec(bracket_raw())
    p = PartScript("Check_Replay", "x", result=spec["replay"]["result_body"], close_all=False)
    rv.apply_ops(p, spec["_ops"])
    p.save()
    assert any(s["tool"] == "catia_hole" for s in p.steps())
    with pytest.raises(ScriptError):
        p2 = PartScript("Check_Replay2", "x", result="Res_Body", close_all=False)
        rv.apply_ops(p2, [{"op": "pad", "name": "P_H1", "height": 1.0, "sketch": "Nope"}])


def test_written_files_when_output_dir_given(tmp_path):
    tools = rv.ReverseTools(SimpleNamespace())
    spec = rv.public_spec(rv.build_spec(bracket_raw()))
    text = tools._finish({**spec, "read_errors": []}, "print('x')\n", {"output_dir": str(tmp_path)}, "Bracket_L")
    data = json.loads(text)
    assert {Path(f).name for f in data["files"]} == {"Bracket_L_spec.json", "Bracket_L_replay.py"}
    assert (tmp_path / "Bracket_L_spec.json").exists()
    assert data["summary"]["replay_complete"] is True and data["summary"]["opaque"] == 0


def test_constructions_the_dsl_refuses_become_opaque_features_not_a_broken_script(schemas):
    # a bow-tie outline crosses itself: PartScript.profile refuses it
    pts = [(0, 0), (10, 10), (10, 0), (0, 10)]
    els = [line(f"L{i}", pts[i], pts[(i + 1) % 4]) for i in range(4)]
    bow = sketch("Sk_Bow", els)
    ok = sketch("Sk_Ok", [circle("c", 0, 0, 20)])
    # a revolution profile that crosses the axis: also refused
    cross = sketch("Sk_Cross", [line("a", (0, -5), (10, -5)), line("b", (10, -5), (10, 5)), line("c", (10, 5), (0, 5)),
                                line("d", (0, 5), (0, -5))], center_line={"kind": "h", "name": "Axe horizontal"})
    shaft = feat("Bad_Turn", "Shaft", "Sk_Cross", angle1=360.0, angle2=0.0, thin=False)
    raw = raw_part([body("R", [pad("Good_Pad", "Sk_Ok", 5), pad("Bow_Pad", "Sk_Bow", 3), shaft],
                         [ok, bow, cross], main=True)])
    spec = rv.build_spec(raw)
    by = {o["name"]: o for o in spec["opaque"]}
    assert "crosses itself" in by["Bow_Pad"]["reason"] and "refuses" in by["Bow_Pad"]["reason"]
    assert "crosses the revolution axis" in by["Bad_Turn"]["reason"]
    assert [o["op"] for o in spec["_ops"]] == ["sketch", "pad"]      # only the good feature is replayed
    assert rv.validate_replay(spec, schemas)["valid"]
    assert spec["replay"]["complete"] is False


def test_audit_reports_an_unreadable_sketch_instead_of_calling_it_empty():
    sk = sketch("Sk_Broken_Read", [])
    sk["unreadable"] = "sketch geometry could not be read: boom"
    res = rv.audit_part(raw_part([body("R", [pad("P", "Sk_Broken_Read", 5)], [sk], main=True, volume=10.0)]))
    codes = [i["code"] for i in res["issues"]]
    assert "sketch.unreadable" in codes and "sketch.empty" not in codes and "sketch.not_closed" not in codes


def test_hole_up_to_a_face_is_replayed_with_a_point_inside_that_face(schemas):
    h = feat("Bore_Thru", "Hole", "Hs", type=0, anchor_mode=0, bottom_type=0, threading_mode=1, diameter=6.0,
             bottom_limit_mode=3, depth=None, origin=[5, 5, 10], direction=[0, 0, -1], up_to_point=[20, 20, 0])
    sk = sketch("S", [circle("c", 0, 0, 30)])
    spec = rv.build_spec(raw_part([body("R", [pad("P", "S", 10), h], [sk], main=True)]))
    assert spec["replay"]["complete"], spec["opaque"]
    assert "up_to_point=(20.0, 20.0, 0.0)" in rv.render_script(spec)
    assert rv.validate_replay(spec, schemas)["valid"]
