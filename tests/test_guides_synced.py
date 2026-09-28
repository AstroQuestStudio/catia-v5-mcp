"""Packaged guides must match the docs (run scripts/sync_guides.py after editing a guide)."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["GUIDE_PART_MODELING.md", "GUIDE_ASSEMBLY.md", "DRAWINGS.md"])
def test_packaged_guide_matches_docs(name):
    assert (ROOT / "docs" / name).read_text(encoding="utf-8") == (
        ROOT / "catia_mcp" / "data" / "guides" / name
    ).read_text(encoding="utf-8")
