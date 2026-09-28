#!/usr/bin/env python
"""Generate docs/LESSONS.md from catia_mcp/data/lessons.json.

Usage: python scripts/gen_lessons_doc.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "catia_mcp" / "data" / "lessons.json"
DST = ROOT / "docs" / "LESSONS.md"

AREA_TITLES = {
    "com": "COM automation",
    "sketch": "Sketcher",
    "part": "Part Design",
    "boolean": "Boolean operations and bodies",
    "topology": "Topology and references",
    "measure": "Measurement",
    "assembly": "Assembly Design",
    "display": "Display and screenshots",
    "process": "Process and verification",
    "drawing": "Reading drawings",
    "performance": "Performance",
}
SEVERITY_ORDER = ["critical", "high", "medium", "low"]


def render(lessons: list[dict]) -> str:
    out = [
        "# Lessons learned",
        "",
        "Generated from `catia_mcp/data/lessons.json` by `scripts/gen_lessons_doc.py` - do not edit by hand.",
        "",
        "Every lesson comes from a mistake actually made and proven on a live CATIA V5 R19 session.",
        "AI agents receive the critical and high ones as server instructions at startup, can query the full",
        "base with the `catia_lessons` tool, and add their own with `catia_add_lesson`.",
        "",
        f"{len(lessons)} built-in lessons.",
        "",
    ]
    areas = [a for a in AREA_TITLES if any(l["area"] == a for l in lessons)]
    out.append("## Contents")
    out.append("")
    for a in areas:
        n = sum(1 for l in lessons if l["area"] == a)
        out.append(f"- [{AREA_TITLES[a]}](#{AREA_TITLES[a].lower().replace(' ', '-')}) ({n})")
    out.append("")
    for a in areas:
        out.append(f"## {AREA_TITLES[a]}")
        out.append("")
        items = sorted(
            (l for l in lessons if l["area"] == a),
            key=lambda l: (SEVERITY_ORDER.index(l["severity"]), l["id"]),
        )
        for l in items:
            out.append(f"### {l['id']} - {l['title']}")
            out.append("")
            out.append(f"**Severity:** {l['severity']}" + (f" | **Tools:** {', '.join('`'+t+'`' for t in l['tools'])}" if l.get("tools") else ""))
            out.append("")
            if l.get("symptom"):
                out.append(f"- **Symptom:** {l['symptom']}")
            if l.get("cause"):
                out.append(f"- **Cause:** {l['cause']}")
            out.append(f"- **Rule:** {l['rule']}")
            if l.get("example"):
                out.append(f"- **Example:** `{l['example']}`")
            if l.get("error_patterns"):
                out.append("- **Matches errors:** " + ", ".join(f"`{p}`" for p in l["error_patterns"]))
            if l.get("proof"):
                out.append(f"- **Proof:** {l['proof']}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    lessons = json.loads(SRC.read_text(encoding="utf-8"))
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(render(lessons), encoding="utf-8", newline="\n")
    print(f"wrote {DST} ({len(lessons)} lessons)")


if __name__ == "__main__":
    main()
