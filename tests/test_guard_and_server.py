"""Guard rails, annotations, paths and server wiring, all without CATIA."""

from __future__ import annotations

import time

import pytest

from catia_mcp import annotations, guard, paths


def test_only_single_ok_dialogs_are_closable():
    assert guard.is_safe_to_close(["OK"])
    assert guard.is_safe_to_close(["&Fermer"])
    assert guard.is_safe_to_close(["Close"])
    assert not guard.is_safe_to_close(["Oui", "Non"])       # a question: never answered blindly
    assert not guard.is_safe_to_close(["OK", "Annuler"])
    assert not guard.is_safe_to_close(["Enregistrer"])
    assert not guard.is_safe_to_close([])


def test_hang_guard_fires_and_cancels():
    with guard.HangGuard(0.05, "slow", kill=False) as g:
        time.sleep(0.2)
    assert g.fired
    with guard.HangGuard(5, "fast") as g2:
        pass
    assert not g2.fired
    with guard.HangGuard(0, "off") as g3:  # 0 = disabled
        time.sleep(0.05)
    assert not g3.fired


def test_paths_use_state_dir_not_package(tmp_path, monkeypatch):
    monkeypatch.setenv("CATIA_MCP_HOME", str(tmp_path / "state"))
    assert paths.home() == tmp_path / "state" and paths.home().is_dir()
    assert paths.log_file().parent == tmp_path / "state"
    assert paths.lock_file().name == "catia.lock"


def test_env_flag(monkeypatch):
    monkeypatch.delenv("CATIA_MCP_X", raising=False)
    assert paths.env_flag("CATIA_MCP_X", True) and not paths.env_flag("CATIA_MCP_X", False)
    for raw, expected in [("1", True), ("true", True), ("ON", True), ("0", False), ("no", False)]:
        monkeypatch.setenv("CATIA_MCP_X", raw)
        assert paths.env_flag("CATIA_MCP_X", not expected) is expected


def test_annotations():
    assert annotations.hints("catia_list_faces")["readOnlyHint"] is True
    assert annotations.hints("catia_get_inertia")["readOnlyHint"] is True
    assert annotations.hints("catia_lessons")["readOnlyHint"] is True
    pad = annotations.hints("catia_pad")
    assert pad["readOnlyHint"] is False and pad["destructiveHint"] is False
    assert annotations.hints("catia_delete_feature")["destructiveHint"] is True
    assert annotations.hints("catia_close_all")["destructiveHint"] is True
    assert annotations.hints("catia_fit_all")["idempotentHint"] is True


@pytest.fixture(scope="module")
def server():
    from catia_mcp.server import CATIAMCPServer

    return CATIAMCPServer()


def test_meta_tools_registered(server):
    names = {d["name"] for d in server.tool_definitions()}
    assert {"catia_batch", "catia_lessons", "catia_add_lesson"} <= names


def test_tool_names_are_unique_and_prefixed(server):
    names = [d["name"] for d in server.tool_definitions()]
    assert len(names) == len(set(names))
    assert all(n.startswith(("catia_", "drawing_")) for n in names)


def test_every_tool_is_documented_for_an_llm(server):
    for d in server.tool_definitions():
        assert len(d["description"]) >= 20, d["name"]
        schema = d["inputSchema"]
        assert schema["type"] == "object", d["name"]
        for req in schema.get("required", []):
            assert req in schema.get("properties", {}), (d["name"], req)


def test_unknown_tool_suggests_a_close_match(server):
    from catia_mcp.server import CatiaToolError

    with pytest.raises(CatiaToolError, match="catia_pad"):
        server.dispatch("catia_padd", {})


def test_batch_dry_run_through_the_server(server):
    out = server.dispatch(
        "catia_batch",
        {"steps": [{"tool": "catia_fit_all"}, {"tool": "catia_nope"}], "dry_run": True},
    )
    assert "BATCH REJECTED" in out and "catia_nope" in out


def test_instructions_are_sent_to_clients(server):
    assert server.server.instructions and len(server.server.instructions) > 100
