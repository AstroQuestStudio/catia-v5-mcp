"""Tests for the lessons knowledge base (no CATIA needed)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from catia_mcp import lessons

DATA = Path(lessons.__file__).resolve().parent / "data" / "lessons.json"
REQUIRED = ("id", "title", "area", "severity", "symptom", "error_patterns", "cause", "rule", "tools", "proof", "source")


@pytest.fixture(scope="module")
def raw() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real user file."""
    monkeypatch.setenv("CATIA_MCP_HOME", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------ data
def test_json_valid_and_size(raw):
    assert isinstance(raw, list)
    assert 80 <= len(raw) <= 150


def test_schema(raw):
    for e in raw:
        for k in REQUIRED:
            assert k in e, f"{e.get('id')} misses {k}"
        assert re.fullmatch(r"L\d{3}", e["id"])
        assert e["area"] in lessons.AREAS
        assert e["severity"] in lessons.SEVERITIES
        assert e["source"] == "builtin"
        for k in ("title", "rule", "symptom", "cause", "proof"):
            assert isinstance(e[k], str) and e[k].strip(), f"{e['id']}.{k}"
        assert isinstance(e["error_patterns"], list) and isinstance(e["tools"], list)
        assert all(isinstance(t, str) and t.startswith("catia_") for t in e["tools"])
        assert 1 <= e["rule"].count(".") + e["rule"].count(";") + 1


def test_ids_and_titles_unique(raw):
    ids = [e["id"] for e in raw]
    assert len(ids) == len(set(ids))
    titles = [lessons._title_key(e["title"]) for e in raw]
    assert len(titles) == len(set(titles))


def test_regex_compile(raw):
    for e in raw:
        for p in e["error_patterns"]:
            re.compile(p, re.IGNORECASE)


def test_every_area_and_critical_present(raw):
    areas = {e["area"] for e in raw}
    assert {"com", "sketch", "part", "boolean", "topology", "measure", "assembly", "display", "process"} <= areas
    assert sum(e["severity"] == "critical" for e in raw) >= 8


def test_no_private_content(raw):
    blob = json.dumps(raw, ensure_ascii=False).lower()
    import getpass
    from pathlib import Path

    # never ship the author machine identity or school-project wording inside lessons
    for bad in ("moodle", "professeur", getpass.getuser().lower(), Path.home().name.lower()):
        assert len(bad) < 3 or bad not in blob, bad
    assert not re.search(r"[a-z]:\\users\\", blob)
    assert not re.search(r"[a-z]:/users/", blob)
    assert not re.search(r"\b\d_\d{3}_", blob)  # part numbers of a school project


def test_builtin_loaded_without_user_file():
    loaded = lessons.load()
    assert len(loaded) == len(json.loads(DATA.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ real error texts -> lesson
ERROR_CASES = [
    ("(-2147352567, \"Une exception s'est produite.\", (0, 'CATIAPart', 'La méthode UpdateObject a échoué', None, 0, -2147467259), None)",
     "UpdateObject"),
    ("La méthode UpdateObject a échoué", "UpdateObject"),
    ("La méthode Search a échoué", "CGMFace"),
    ("Property 'AddNewShaft.FirstAngle' can not be set.", ".Value"),
    ("Property 'AddNewShaft.FirstAngle' can not be set.", "CenterLine"),
    ("La méthode SaveAs a échoué", "SaveAs raises"),
    ("La méthode Name a échoué", "PartNumber"),
    ("Interdiction de supprimer une géométrie agrégée par une autre géométrie", "Selection.Delete"),
    ("les données importées ont été modifiées dans la session", "SaveAs of the root"),
    ("Constraint 'Coax_A_B' created, status wrong geometry type", "axis"),
    ("closest is 0.010 mm away", "0.01 mm"),
    ("Commande inconnue", "StartCommand"),
    ("profile crosses itself (segment 2 crosses segment 5 near (1.0; 2.0)). Nothing was drawn.", "self-intersecting"),
    ("closed profile does not return to its start: ends at (1, 2), starts at (0, 0).", "return to its start"),
    ("[check] arc 3 sweeps 250° (> 180°) around (0; 0) and passes through (1.0; 2.0).", "180 degrees"),
    ("AttributeError: <unknown>.CreateArc", "CreateArc"),
    ("CATIA refused the Shaft (axis 'h'): La méthode UpdateObject a échoué. The failed feature was removed from the tree.", "failed feature"),
    ("[naming] 'Pad_base' already exists in the part; kept 'Extrusion.1'. Choose a unique name.", "Feature.Name"),
]


@pytest.mark.parametrize("text,expected", ERROR_CASES)
def test_error_patterns_match_real_errors(text, expected):
    matched = [l for l in lessons.load() if lessons._match_length(l, text)]
    assert matched, f"no lesson matches: {text}"
    assert any(expected.lower() in (l["title"] + " " + l["rule"] + " " + l["cause"]).lower() for l in matched), \
        (expected, [l["title"] for l in matched])


def test_hint_for_error_format_and_limit():
    text = "(-2147352567, 'x', (0, 'CATIAPart', 'La méthode UpdateObject a échoué', None, 0, -2147467259), None)"
    hint = lessons.hint_for_error(text, tool="catia_shaft")
    lines = hint.splitlines()
    assert 1 <= len(lines) <= 2
    assert all(re.match(r"\[lesson L\d{3}\] .+: .+", ln) for ln in lines)
    assert len(lessons.hint_for_error(text, limit=1).splitlines()) == 1
    assert lessons.hint_for_error("everything went fine") == ""
    assert lessons.hint_for_error("") == ""


def test_hint_prefers_tool_specific_lesson():
    hint = lessons.hint_for_error("La méthode Search a échoué", tool="catia_list_edges", limit=1)
    assert "CGM" in hint
    hint = lessons.hint_for_error("La méthode UpdateObject a échoué", tool="catia_chamfer", limit=1)
    assert hint.startswith("[lesson ")


# ------------------------------------------------------------------ instructions
def test_render_instructions_budget_and_content():
    text = lessons.render_instructions()
    assert len(text) <= 7000
    assert "catia_lessons" in text and "catia_add_lesson" in text
    assert text.index("Critical") < text.index("Important")
    assert "(L004)" in text  # a critical lesson is always kept
    for n in (500, 1500, 3000):
        assert len(lessons.render_instructions(max_chars=n)) <= n


# ------------------------------------------------------------------ search / format
def test_search_basic_and_accents():
    hits = lessons.search("chanfrein")  # not in English text: no crash, maybe empty
    assert isinstance(hits, list)
    ids = [h["id"] for h in lessons.search("pocket direction")]
    assert ids
    assert lessons.search("EXTRACTION échoué SaveAs")  # accent- and case-insensitive
    assert lessons.search("Révolution") == lessons.search("revolution")


def test_search_filters_and_limit():
    assert all(h["area"] == "assembly" for h in lessons.search(area="assembly"))
    assert all("catia_pocket" in h["tools"] for h in lessons.search(tool="catia_pocket"))
    assert len(lessons.search("constraint", limit=3)) <= 3
    critical_first = lessons.search(area="part")
    assert critical_first[0]["severity"] in ("critical", "high")
    assert lessons.search("zzzzqqqq") == []


def test_format_lessons():
    text = lessons.format_lessons(lessons.search("pocket", limit=2))
    assert "Rule:" in text and "[L" in text
    assert lessons.format_lessons([]) == "No matching lesson."


# ------------------------------------------------------------------ user lessons
def test_add_user_lesson_roundtrip(tmp_path):
    e = lessons.add_user_lesson(
        "Rib needs a closed profile", "Close the profile before catia_rib.", area="part",
        severity="high", symptom="Rib fails", cause="open contour",
        error_patterns=[r"rib .* failed"], tools=["catia_pad"], proof="seen once")
    assert e["id"] == "U001" and e["source"] == "user"
    f = tmp_path / "lessons.jsonl"
    assert f.is_file()
    assert json.loads(f.read_text(encoding="utf-8").splitlines()[0])["title"] == e["title"]
    e2 = lessons.add_user_lesson("Second lesson", "Do the other thing.")
    assert e2["id"] == "U002" and e2["area"] == "process" and e2["severity"] == "medium"
    loaded = lessons.load()
    assert {"U001", "U002"} <= {l["id"] for l in loaded}
    assert "Rib fails" in lessons.format_lessons(lessons.search("rib"))
    assert lessons.hint_for_error("RIB 12 FAILED").startswith("[lesson U001]")


def test_add_user_lesson_rejects_duplicates_and_invalid():
    lessons.add_user_lesson("Unique title", "Rule one.")
    with pytest.raises(ValueError):
        lessons.add_user_lesson("  unique   TITLE!! ", "Another rule.")
    with pytest.raises(ValueError):
        lessons.add_user_lesson("Set a Parameter object with .Value, never by direct assignment", "dup of builtin")
    with pytest.raises(ValueError):
        lessons.add_user_lesson("", "rule")
    with pytest.raises(ValueError):
        lessons.add_user_lesson("T", "")
    with pytest.raises(ValueError):
        lessons.add_user_lesson("Bad area", "r", area="nope")
    with pytest.raises(ValueError):
        lessons.add_user_lesson("Bad severity", "r", severity="urgent")
    with pytest.raises(ValueError):
        lessons.add_user_lesson("Bad regex", "r", error_patterns=["(unclosed"])


def test_load_ignores_invalid_user_lines(tmp_path):
    f = tmp_path / "lessons.jsonl"
    good = {"id": "U007", "title": "Good one", "rule": "Do X.", "area": "com", "severity": "low"}
    f.write_text("not json\n\n[1, 2]\n" + json.dumps({"title": "no rule"}) + "\n" + json.dumps(good) + "\n{broken",
                 encoding="utf-8")
    user = [l for l in lessons.load() if l["source"] == "user"]
    assert [l["id"] for l in user] == ["U007"]
    e = lessons.add_user_lesson("Next", "Rule.")
    assert e["id"] == "U008"


def test_user_path_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("CATIA_MCP_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "ad"))
    assert lessons.user_lessons_path() == tmp_path / "ad" / "catia-mcp" / "lessons.jsonl"
    monkeypatch.delenv("APPDATA")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert lessons.user_lessons_path() == tmp_path / "home" / ".catia-mcp" / "lessons.jsonl"
    monkeypatch.setenv("CATIA_MCP_HOME", str(tmp_path / "x"))
    assert lessons.user_lessons_path() == tmp_path / "x" / "lessons.jsonl"


def test_load_with_explicit_path(tmp_path):
    p = tmp_path / "custom.jsonl"
    p.write_text(json.dumps({"title": "Custom", "rule": "R."}) + "\n", encoding="utf-8")
    loaded = lessons.load(p)
    assert loaded[-1]["title"] == "Custom" and loaded[-1]["id"].startswith("U")
