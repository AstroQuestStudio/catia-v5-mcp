"""Copy the guides into the package data so they ship inside the wheel."""
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for name in ("GUIDE_PART_MODELING.md", "GUIDE_ASSEMBLY.md", "DRAWINGS.md"):
    shutil.copy(root / "docs" / name, root / "catia_mcp" / "data" / "guides" / name)
    print("synced", name)
