"""Scale features of the assembly tools that need no CATIA: batching of designations, the bounded
cache, paginated listings (with fake COM objects), and the kit's automatic prepare step."""

from __future__ import annotations

import json

import pytest

from catia_mcp.scripting import AssemblyScript, axis, face
from catia_mcp.tools.assembly import AssemblyTools


class FakeConn:
    app = None


# ── designation cache ─────────────────────────────────────────────────────────────────────────
def test_cache_is_a_bounded_lru(monkeypatch):
    monkeypatch.setenv("CATIA_MCP_DESIGNATION_CACHE_DISK", "0")
    t = AssemblyTools(FakeConn())
    t._GEOM_CACHE_MAX = 3
    for k in range(3):
        t._cache_put(("f", k), f"name{k}")
    assert t._cache_get(("f", 0)) == "name0"  # touching 0 makes it the most recent
    t._cache_put(("f", 3), "name3")            # evicts the least recent, which is now 1
    assert t._cache_get(("f", 1)) is None
    assert t._cache_get(("f", 0)) == "name0" and t._cache_get(("f", 3)) == "name3"
    assert len(t._geom_cache) == 3


def test_cache_survives_a_new_session(tmp_path, monkeypatch):
    """A second process reuses the designations of the first (the slow part of a rebuild)."""
    monkeypatch.setenv("CATIA_MCP_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("CATIA_MCP_DESIGNATION_CACHE_DISK", raising=False)
    part = tmp_path / "P.CATPart"
    part.write_text("a")
    key = AssemblyTools._cache_key(str(part), "face", [1, 2, 3])
    AssemblyTools(FakeConn())._cache_put(key, "Selection_RSur:(Face:(Brp:(Pad;0)))")
    second = AssemblyTools(FakeConn())  # fresh process: empty memory
    assert second._cache_get(key) == "Selection_RSur:(Face:(Brp:(Pad;0)))"
    part.write_text("edited")  # the part changed: the old name must not be served
    assert second._cache_get(AssemblyTools._cache_key(str(part), "face", [1, 2, 3])) is None


def test_disk_cache_ignores_torn_lines_and_can_be_disabled(tmp_path, monkeypatch):
    from catia_mcp import paths

    monkeypatch.setenv("CATIA_MCP_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("CATIA_MCP_DESIGNATION_CACHE_DISK", raising=False)
    key = ("p", 1, 2, "face", (1.0, 2.0, 3.0))
    AssemblyTools(FakeConn())._cache_put(key, "ok")
    with paths.designation_cache_file().open("a", encoding="utf-8") as f:
        f.write('["p", 1, 2, "fa')  # crash in the middle of a write
    assert AssemblyTools(FakeConn())._cache_get(key) == "ok"
    monkeypatch.setenv("CATIA_MCP_DESIGNATION_CACHE_DISK", "0")
    assert AssemblyTools(FakeConn())._cache_get(key) is None


def test_cache_key_changes_when_the_file_changes(tmp_path):
    f = tmp_path / "P.CATPart"
    f.write_text("a")
    k1 = AssemblyTools._cache_key(str(f), "face", [1, 2, 3.00001])
    assert k1 == AssemblyTools._cache_key(str(f), "face", [1, 2, 3.00002])  # rounded to 1e-4
    f.write_text("bb")  # different size (and mtime): the designation must be resolved again
    assert AssemblyTools._cache_key(str(f), "face", [1, 2, 3]) != k1


# ── paginated listing with fake COM objects ───────────────────────────────────────────────────
class Products:
    def __init__(self, children):
        self.children = children

    @property
    def Count(self):  # noqa: N802
        return len(self.children)

    def Item(self, i):  # noqa: N802
        return self.children[i - 1]


class Comp:
    def __init__(self, name, children=()):
        self.Name, self.PartNumber = name, name.split(".")[0]
        self.Products = Products(list(children))
        self.ReferenceProduct = type("R", (), {"Parent": type("D", (), {"FullName": f"C:/{name}.CATPart"})()})()


def listing_tools(root):
    t = AssemblyTools(FakeConn())
    t._root = lambda: root
    t._position = lambda comp: [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0]
    return t


def test_default_listing_is_unchanged_json_list():
    root = Comp("Root", [Comp("A.1"), Comp("Sub.1", [Comp("B.1")])])
    out = json.loads(listing_tools(root)._list_components({}))
    assert isinstance(out, list) and [c["path"] for c in out] == ["A.1", "Sub.1"]
    assert out[1]["children"][0]["path"] == "Sub.1/B.1"


def test_paging_and_depth():
    root = Comp("Root", [Comp(f"P{k}.1") for k in range(10)])
    t = listing_tools(root)
    page = json.loads(t._list_components({"limit": 4, "offset": 4}))
    assert page["total"] == 10 and page["returned"] == 4
    assert [c["path"] for c in page["components"]] == ["P4.1", "P5.1", "P6.1", "P7.1"]
    last = json.loads(t._list_components({"limit": 4, "offset": 8}))
    assert last["returned"] == 2


def test_scoped_listing_and_depth_one_counts_children():
    deep = Comp("Sub.1", [Comp("Leaf.1"), Comp("Leaf.2")])
    root = Comp("Root", [Comp("Top.1", [deep]), Comp("Other.1")])
    t = listing_tools(root)
    scoped = json.loads(t._list_components({"path": "Top.1"}))
    assert scoped["total"] == 1 and scoped["components"][0]["path"] == "Top.1/Sub.1"
    shallow = json.loads(t._list_components({"depth": 1}))
    top = shallow["components"][0]
    assert "children" not in top and top["children_count"] == 1


def test_huge_listing_is_cut_and_flagged():
    root = Comp("Root", [Comp(f"P{k}.1") for k in range(50)])
    t = listing_tools(root)
    t._LIST_CAP = 20
    out = json.loads(t._list_components({}))
    assert out["truncated"] is True and out["returned"] == 20 and "path/depth/limit/offset" in out["note"]


# ── kit: one prepare step, before the first constraint ───────────────────────────────────────
def build(tmp_path, **kw):
    for n in ("A", "B", "C"):
        (tmp_path / f"{n}.CATPart").write_text("x")
    a = AssemblyScript("Pair_Test", tmp_path / "out", **kw)
    x, y, z = (a.add(tmp_path / f"{n}.CATPart") for n in ("A", "B", "C"))
    a.fix(x, "Fix_A")
    a.contact(y, face(0, 0, 5), x, face(20, 0, 5), "Contact_B_A")
    a.coincidence(y, axis(5, 0, 0), x, axis(5, 0, 1), "Coax_B_A")
    a.contact(z, face(0, 0, 5), x, face(20, 0, 5), "Contact_C_A")  # same A point: must be listed once
    return a


def test_one_prepare_step_before_the_first_constraint(tmp_path, schemas):
    from catia_mcp import batch

    a = build(tmp_path)
    steps = a.steps()
    tools = [s["tool"] for s in steps]
    assert tools.count("catia_prepare_geometry") == 1
    idx = tools.index("catia_prepare_geometry")
    first_designating = next(i for i, t in enumerate(tools) if t in ("catia_contact_constraint", "catia_coincidence_constraint"))
    assert idx == first_designating - 1 and idx > max(i for i, t in enumerate(tools) if t == "catia_add_component")
    items = steps[idx]["args"]["items"]
    assert len(items) == len({json.dumps(i, sort_keys=True) for i in items})  # no duplicates
    assert {i["component"] for i in items} == {"A.1", "B.1", "C.1"}
    norm, problems = batch.normalize_steps(steps)
    assert not problems and not batch.validate_batch(norm, schemas)


def test_prepare_can_be_disabled(tmp_path):
    assert "catia_prepare_geometry" not in [s["tool"] for s in build(tmp_path, prepare_geometry=False).steps()]


@pytest.fixture(scope="module")
def schemas():
    from catia_mcp.server import CATIAMCPServer

    return {d["name"]: d["inputSchema"] for d in CATIAMCPServer().tool_definitions()}


def test_save_all_gives_distinct_documents_distinct_files():
    used: dict[str, str] = {}
    u = AssemblyTools._unique_target
    a = u("out/2011N144.CATPart", used, "docA", "Bearing A")
    assert a == "out/2011N144.CATPart"
    assert u("out/2011N144.CATPart", used, "docA", "Bearing A") == a      # same document: same file
    b = u("out/2011N144.CATPart", used, "docB", "Bearing B")
    assert b != a and b.endswith("_Bearing_B.CATPart")
    c = u("out/2011N144.CATPart", used, "docC", "Bearing B")               # same tag, still unique
    assert c not in (a, b)


class _Doc:
    def __init__(self, path, name):
        self.FullName, self.Name = str(path), name

    def Save(self):
        pass

    def SaveAs(self, target):
        open(target, "w").close()
        self.FullName = target


class _Ref:
    def __init__(self, doc, part_number):
        self.Parent, self.PartNumber = doc, part_number
        self.Products = type("P", (), {"Count": 0})()


class _Inst:
    def __init__(self, ref):
        self.ReferenceProduct = ref


class _Products:
    def __init__(self, insts):
        self._i = insts
        self.Count = len(insts)

    def Item(self, i):
        return self._i[i - 1]


def test_save_all_saves_a_multi_instance_part_once_and_keeps_two_same_named_parts(tmp_path):
    src_a, src_b, out = tmp_path / "a", tmp_path / "b", tmp_path / "out"
    src_a.mkdir(), src_b.mkdir()
    for d in (src_a, src_b):
        (d / "Washer.CATPart").write_text("x")
    washer = _Doc(src_a / "Washer.CATPart", "Washer.CATPart")
    other = _Doc(src_b / "Washer.CATPart", "Washer.CATPart")         # different document, same file name
    wref, oref = _Ref(washer, "91860A035"), _Ref(other, "OTHER")
    root_doc = _Doc(tmp_path / "Root.CATProduct", "Root.CATProduct")
    (tmp_path / "Root.CATProduct").write_text("x")
    root = type("R", (), {})()
    root.Parent, root.PartNumber = root_doc, "Root"
    root.Products = _Products([_Inst(wref)] * 4 + [_Inst(oref)])     # 4 instances of one washer
    app = type("A", (), {"DisplayFileAlerts": True, "Documents": type("D", (), {"Count": 0})()})()
    t = AssemblyTools(type("C", (), {"app": app})())
    t._root = lambda: root
    t._save_all({"folder": str(out)})
    names = sorted(p.name for p in out.iterdir())
    assert names == ["Root.CATProduct", "Washer.CATPart", "Washer_OTHER.CATPart"]   # no chain of suffixes
