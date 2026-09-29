"""Tool sets, resources and prompts: plain Python, no CATIA."""

from __future__ import annotations

import pytest

from catia_mcp import profiles, resources


def test_default_is_full():
    assert profiles.parse(None) == profiles.PRESETS["full"]
    assert profiles.parse("  ") == profiles.PRESETS["full"]


def test_presets_and_groups_mix():
    enabled = profiles.parse("assembly, drawing")
    assert {"assembly", "drawing", "meta", "document"} <= enabled
    assert "sketch" not in enabled and "gsd" not in enabled


def test_meta_is_always_advertised():
    assert "meta" in profiles.parse("sketch")
    assert profiles.visible("meta", frozenset({"sketch"}))


def test_typo_is_a_startup_error_not_a_silent_empty_list():
    with pytest.raises(ValueError, match="Unknown tool set 'asembly'"):
        profiles.parse("asembly")


def test_every_preset_uses_known_groups():
    for name, groups in profiles.PRESETS.items():
        assert groups <= set(profiles.GROUPS), name


def test_server_filters_advertised_tools(monkeypatch):
    monkeypatch.setenv("CATIA_MCP_TOOLSETS", "assembly")
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    advertised = {d["name"] for d in server.advertised_tool_definitions()}
    everything = {d["name"] for d in server.tool_definitions()}
    assert "catia_add_component" in advertised and "catia_batch" in advertised
    assert "catia_pad" not in advertised and "catia_pad" in everything  # hidden, still callable
    assert len(advertised) < len(everything)


def test_resources_are_readable():
    uris = {r["uri"] for r in resources.list_resources()}
    assert {"catia://rules", "catia://lessons", "catia://guides/part-modeling"} <= uris
    assert len(resources.read_resource("catia://rules")) > 500
    assert "L001" in resources.read_resource("catia://lessons")
    with pytest.raises(ValueError):
        resources.read_resource("catia://nope")


def test_prompts_render_and_validate_arguments():
    names = {p["name"] for p in resources.list_prompts()}
    assert {"model_part_from_drawing", "assemble_product", "large_assembly", "audit_model"} <= names
    text = resources.render_prompt("model_part_from_drawing", {"part_name": "Flange_A", "drawing_pdf": "f.pdf"})
    assert "Flange_A_Result" in text and "f.pdf" in text and "catia_add_lesson" in text
    with pytest.raises(ValueError, match="needs"):
        resources.render_prompt("assemble_product", {"assembly_name": "X"})
    with pytest.raises(ValueError, match="Unknown prompt"):
        resources.render_prompt("nope", {})
    assert resources.render_prompt("audit_model", None)
    assert "(not given)" in resources.render_prompt("large_assembly", {"assembly_name": "Car"})


def test_optional_modules_are_skipped_when_absent_or_broken(monkeypatch):
    import importlib

    from catia_mcp.server import CATIAMCPServer

    real = importlib.import_module

    def fake(name, *a, **k):
        if name == "catia_mcp.tools.drafting":
            raise RuntimeError("half-written module")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake)
    server = CATIAMCPServer()  # must not raise
    assert "catia_batch" in {d["name"] for d in server.tool_definitions()}
