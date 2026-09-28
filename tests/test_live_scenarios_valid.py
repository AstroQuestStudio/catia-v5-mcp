"""The live regression scenarios must stay valid against the current tool schemas.

They only run against a real CATIA (see docs/LIVE_VALIDATION.md), but a schema change that
breaks them is caught here, offline, before anyone needs a licence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from catia_mcp import batch

LIVE = Path(__file__).parent / "live"


@pytest.fixture(scope="module")
def schemas():
    from catia_mcp.server import CATIAMCPServer

    return {d["name"]: d["inputSchema"] for d in CATIAMCPServer().tool_definitions()}


@pytest.mark.parametrize("scenario", sorted(LIVE.glob("*.json")), ids=lambda p: p.name)
def test_scenario_is_valid(scenario, schemas):
    steps, problems = batch.normalize_steps(json.loads(scenario.read_text(encoding="utf-8")))
    assert not problems, problems
    assert not batch.validate_batch(steps, schemas)
