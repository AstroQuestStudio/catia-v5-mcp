"""naming.rollback: a failed creation tool must leave the tree as it found it (no CATIA needed)."""

from __future__ import annotations

from catia_mcp import naming


class Obj:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.Name = name
        self.fail = fail


class Selection:
    def __init__(self, tree: list) -> None:
        self.tree, self.current = tree, None

    def Clear(self) -> None:  # noqa: N802 - COM naming
        self.current = None

    def Add(self, obj: Obj) -> None:  # noqa: N802
        self.current = obj

    def Delete(self) -> None:  # noqa: N802
        if self.current.fail:
            raise RuntimeError("locked")
        self.tree[:] = [(p, o) for p, o in self.tree if o is not self.current]


class Conn:
    def __init__(self, tree: list) -> None:
        self.hso = Selection(tree)


def run(monkeypatch, before, after_extra):
    tree = list(before) + list(after_extra)
    monkeypatch.setattr(naming, "snapshot", lambda conn: list(tree))
    conn = Conn(tree)
    note = naming.rollback(conn, list(before))
    return tree, note


def test_removes_the_broken_feature_only(monkeypatch):
    keep = [("B/Pad_Base", Obj("Pad_Base")), ("sk:B/Esq_Base", Obj("Esq_Base"))]
    tree, note = run(monkeypatch, keep, [("B/Extrusion.2", Obj("Extrusion.2"))])
    assert [p for p, _ in tree] == ["B/Pad_Base", "sk:B/Esq_Base"]
    assert "removed" in note and "Extrusion.2" in note


def test_named_new_sketch_is_kept_default_named_one_is_removed(monkeypatch):
    extra = [
        ("sk:B/Esq_Mine", Obj("Esq_Mine")),
        ("sk:B/Esquisse.7", Obj("Esquisse.7")),
        ("B/Trou.3", Obj("Trou.3")),
    ]
    tree, _ = run(monkeypatch, [], extra)
    assert [p for p, _ in tree] == ["sk:B/Esq_Mine"]


def test_nothing_to_clean_returns_empty(monkeypatch):
    same = [("B/Pad_Base", Obj("Pad_Base"))]
    tree, note = run(monkeypatch, same, [])
    assert note == "" and len(tree) == 1


def test_undeletable_object_is_reported_not_raised(monkeypatch):
    tree, note = run(monkeypatch, [], [("B/Extrusion.2", Obj("Extrusion.2", fail=True))])
    assert "could not remove" in note and "catia_delete_feature" in note


def test_unreadable_tree_is_a_noop(monkeypatch):
    monkeypatch.setattr(naming, "snapshot", lambda conn: None)
    assert naming.rollback(object(), []) == ""
    monkeypatch.setattr(naming, "snapshot", lambda conn: [])
    assert naming.rollback(object(), None) == ""
