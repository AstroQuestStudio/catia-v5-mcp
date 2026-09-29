"""Safety tiers: what each tier allows, the ratchet, and the gate inside dispatch / batch."""

from __future__ import annotations

import pytest

from catia_mcp import safety
from catia_mcp.server import CatiaToolError


def test_parse_default_is_unrestricted_and_typos_are_errors():
    assert safety.parse(None) == safety.DANGEROUS == safety.parse("  ")
    assert safety.parse("READ") == "read"
    with pytest.raises(ValueError, match="Unknown safety tier"):
        safety.parse("reed")


def test_every_registered_tool_has_a_sensible_tier():
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    tiers = {d["name"]: safety.required_tier(d["name"]) for d in server.tool_definitions()}
    assert {n for n, t in tiers.items() if t == "dangerous"} == {
        "catia_delete_feature", "catia_close_document", "catia_close_all"}
    for name in ("catia_pad", "catia_pocket", "catia_new_part", "catia_save_all", "catia_add_component",
                 "catia_contact_constraint", "catia_export", "catia_boolean_operation"):
        assert tiers[name] == "write", name
    for name in ("catia_list_faces", "catia_get_inertia", "catia_get_tree", "catia_screenshot",
                 "catia_clash_analysis", "catia_lessons", "catia_batch", "catia_prepare_geometry",
                 "catia_set_view", "catia_open_document", "catia_get_safety_state"):
        assert tiers[name] == "read", name


def test_gate_allows_by_tier():
    read, write, danger = safety.Gate("read"), safety.Gate("write"), safety.Gate("dangerous")
    assert read.allows("catia_get_tree") and not read.allows("catia_pad") and not read.allows("catia_close_all")
    assert write.allows("catia_pad") and not write.allows("catia_delete_feature")
    assert danger.allows("catia_delete_feature")
    assert "CATIA_MCP_SAFETY=write" in read.blocked_message("catia_pad")


def test_the_tier_can_only_be_lowered():
    gate = safety.Gate("write")
    assert gate.lower("read") == "read"
    with pytest.raises(PermissionError, match="cannot be raised"):
        gate.lower("write")
    with pytest.raises(PermissionError):
        gate.lower("dangerous")
    assert gate.tier == "read"


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("CATIA_MCP_SAFETY", "read")
    from catia_mcp.server import CATIAMCPServer

    return CATIAMCPServer()


def test_dispatch_refuses_a_forbidden_tool_before_touching_catia(server):
    with pytest.raises(CatiaToolError, match="Blocked by safety tier 'read'"):
        server.dispatch("catia_pad", {"height": 1})


def test_batch_is_refused_as_a_whole_when_one_step_is_forbidden(server):
    out = server.dispatch("catia_batch", {"steps": [
        {"tool": "catia_get_tree"}, {"tool": "catia_pad", "args": {"height": 1}}]})
    assert "BATCH REJECTED" in out and "catia_pad" in out and "Nothing was executed" in out


def test_ratchet_through_the_tool(server):
    assert "read" in server.dispatch("catia_get_safety_state", {})
    assert "'read'" in server.dispatch("catia_set_safety", {"tier": "read"})
    with pytest.raises(CatiaToolError, match="cannot be raised"):
        server.dispatch("catia_set_safety", {"tier": "write"})


def test_inspection_tools_of_the_optional_modules_are_read_tier():
    from catia_mcp import safety
    for tool in ("catia_describe_model", "catia_audit_model", "catia_measure_model",
                 "catia_drawing_info", "catia_drawing_check"):
        assert safety.required_tier(tool) == safety.READ, tool
    for tool in ("catia_drawing_create", "catia_drawing_add_view", "catia_drawing_export_pdf"):
        assert safety.required_tier(tool) == safety.WRITE, tool
