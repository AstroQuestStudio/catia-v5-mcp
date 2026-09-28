"""MCP resources and prompts: knowledge and ready-made workflows for the model.

Resources (read on demand, no tokens spent until used):
    catia://rules                 the golden rules sent as server instructions
    catia://lessons               every verified pitfall (also searchable with catia_lessons)
    catia://guides/<name>         modelling and assembly guides

Prompts (one-click workflows that already contain the right method, so that even a model that
is weak at 3D follows the proven sequence): model_part_from_drawing, assemble_product,
large_assembly, audit_model, reverse_engineer_part.

Everything here is plain text and testable without CATIA.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_DATA = Path(__file__).parent / "data"
_DOCS = Path(__file__).resolve().parents[1] / "docs"

# uri -> (title, description, filename in data/guides or docs)
GUIDES: dict[str, tuple[str, str, str]] = {
    "part-modeling": (
        "Part modelling guide",
        "Multi-body method, naming, feature order, checks, geometry designation by points.",
        "GUIDE_PART_MODELING.md",
    ),
    "assembly": (
        "Assembly guide",
        "Poses, constraints that hold, pose verification, clash analysis, large assemblies.",
        "GUIDE_ASSEMBLY.md",
    ),
    "drawings": (
        "Reading vector drawings",
        "Exact geometry from PDF drawings, overlays, pitfalls (scale, diameter vs radius).",
        "DRAWINGS.md",
    ),
}


def _read_guide(filename: str) -> str:
    for base in (_DATA / "guides", _DOCS):
        p = base / filename
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return f"(guide {filename} is not installed)"


def list_resources() -> list[dict[str, str]]:
    out = [
        {"uri": "catia://rules", "name": "Golden rules",
         "description": "Condensed rules every session must follow (same text as the server instructions)."},
        {"uri": "catia://lessons", "name": "All verified lessons",
         "description": "Every proven CATIA V5 automation pitfall with cause, rule and proof."},
    ]
    for key, (title, desc, _) in GUIDES.items():
        out.append({"uri": f"catia://guides/{key}", "name": title, "description": desc})
    return out


def read_resource(uri: str) -> str:
    from catia_mcp import lessons

    uri = str(uri)
    if uri == "catia://rules":
        return lessons.render_instructions()
    if uri == "catia://lessons":
        return lessons.format_lessons(lessons.search("", limit=100000))
    prefix = "catia://guides/"
    if uri.startswith(prefix):
        key = uri[len(prefix):]
        if key in GUIDES:
            return _read_guide(GUIDES[key][2])
    raise ValueError(f"Unknown resource: {uri}")


# ── prompts ─────────────────────────────────────────────────────────────────────────────

_COMMON = (
    "Ground rules: name every body, sketch and feature explicitly (no default names like "
    "Pad.1); call catia_lessons before a risky operation; verify each feature with its volume "
    "change instead of assuming; group known sequences in catia_batch (use dry_run first); "
    "record any new pitfall with catia_add_lesson."
)

PROMPTS: dict[str, dict[str, Any]] = {
    "model_part_from_drawing": {
        "description": "Model one part from a PDF drawing: exact geometry, multi-body, verified.",
        "arguments": [
            {"name": "part_name", "description": "Name of the part, e.g. Flange_A", "required": True},
            {"name": "drawing_pdf", "description": "Path to the vector PDF drawing", "required": True},
        ],
        "template": (
            "Model the part '{part_name}' from the drawing '{drawing_pdf}'.\n"
            "1. Read the guide resource catia://guides/part-modeling and catia://guides/drawings.\n"
            "2. drawing_render the views, then drawing_extract_geometry on each: dimensions are "
            "vectorised, so read values visually and CONFIRM them with the measured geometry "
            "(radius vs diameter, title-block scale, rounded non-tangent dimensions).\n"
            "3. Plan the bodies (one per functional zone, main body renamed '{part_name}_Result') "
            "and list every feature with its expected volume change BEFORE touching CATIA.\n"
            "4. Write the scenario with the PartScript template (templates/part_template.py), "
            "run it with dry_run, then for real.\n"
            "5. Check: volume against a hand calculation, bounding box against the drawing, "
            "drawing_overlay of every non-trivial sketch, then catia_get_tree (no default names).\n"
            + _COMMON
        ),
    },
    "assemble_product": {
        "description": "Assemble existing parts with constraints that hold, poses verified.",
        "arguments": [
            {"name": "assembly_name", "description": "Product name", "required": True},
            {"name": "parts", "description": "Comma-separated part files or names", "required": True},
        ],
        "template": (
            "Build the assembly '{assembly_name}' from: {parts}.\n"
            "1. Read catia://guides/assembly.\n"
            "2. For each part call catia_list_faces to find real faces/axes; compute the target "
            "pose of every component from those interfaces.\n"
            "3. Fix one reference part; pre-position every other part with catia_move_component "
            "(track the poses), THEN add constraints. Prefer contact or coincidence on real faces "
            "over unsigned offsets (an offset between planes has no sign and can flip a part "
            "while every constraint reads OK).\n"
            "4. catia_update_assembly, catia_list_constraints (all must be OK), verify the final "
            "poses equal the tracked ones, run catia_clash_analysis and explain every clash.\n"
            + _COMMON
        ),
    },
    "large_assembly": {
        "description": "Strategy for an assembly of hundreds or thousands of parts (vehicle, aircraft).",
        "arguments": [
            {"name": "assembly_name", "description": "Top product name", "required": True},
            {"name": "manifest", "description": "Path to the parts/positions manifest (CSV/JSON)", "required": False},
        ],
        "template": (
            "Plan and build the large assembly '{assembly_name}' (manifest: {manifest}).\n"
            "- Start the server with CATIA_MCP_TOOLSETS=assembly to keep the tool list small.\n"
            "- Structure first: one sub-product per system, created with catia_add_sub_assembly; "
            "never a flat list of thousands of parts.\n"
            "- Work per sub-assembly: build, update, check constraints, clash-analyse it alone, "
            "save_all, and only then insert it into the parent. Save after every sub-assembly so "
            "an interruption loses at most one.\n"
            "- Send steps in catia_batch chunks of 25-100 with stop_on_error; keep the chunk "
            "boundaries in a progress file so the run can resume.\n"
            "- Avoid listing whole trees: query one sub-product at a time.\n"
            "- Clash analysis: per sub-assembly, then between neighbouring sub-assemblies only.\n"
            + _COMMON
        ),
    },
    "audit_model": {
        "description": "Audit the active model: tree quality, naming, volume/bbox sanity.",
        "arguments": [],
        "template": (
            "Audit the active document without modifying it.\n"
            "1. catia_get_tree: list every default-looking name (ending in .N), bodies without a "
            "'_Result', empty bodies, hidden leftovers.\n"
            "2. catia_get_inertia and catia_get_bounding_box: report volume, mass, size.\n"
            "3. For assemblies: catia_list_constraints (any non-OK status), catia_clash_analysis.\n"
            "Report findings as a table with severity and the exact tool call that fixes each one; "
            "do not fix anything until asked.\n" + _COMMON
        ),
    },
    "reverse_engineer_part": {
        "description": "EXPERIMENTAL: describe an existing part and rebuild or document it cleanly.",
        "arguments": [
            {"name": "part_name", "description": "Active part to analyse", "required": True},
        ],
        "template": (
            "Reverse-engineer the part '{part_name}' (experimental workflow).\n"
            "1. catia_get_tree, catia_list_bodies, catia_list_features, catia_get_parameters, "
            "catia_get_inertia, catia_get_bounding_box: record the structure and the numbers.\n"
            "2. For every sketch use catia_sketch_get_geometry; for faces use catia_list_faces "
            "(cylinder axes and radii give the design intent: bores, bosses, patterns).\n"
            "3. Write the recipe as a PartScript (templates/part_template.py) named '{part_name}_Rebuilt' "
            "and run it in a NEW document: never modify the original.\n"
            "4. Compare original and rebuilt: volume within 0.1 %, bounding box, inertia. Report "
            "any feature you could not identify as an explicit black box, never invent one.\n"
            + _COMMON
        ),
    },
}


def list_prompts() -> list[dict[str, Any]]:
    return [
        {"name": n, "description": p["description"], "arguments": p["arguments"]}
        for n, p in PROMPTS.items()
    ]


def render_prompt(name: str, arguments: dict[str, str] | None) -> str:
    if name not in PROMPTS:
        raise ValueError(f"Unknown prompt: {name}")
    spec = PROMPTS[name]
    values = {a["name"]: "(not given)" for a in spec["arguments"]}
    values.update({k: str(v) for k, v in (arguments or {}).items() if k in values})
    missing = [a["name"] for a in spec["arguments"] if a.get("required") and a["name"] not in (arguments or {})]
    if missing:
        raise ValueError(f"Prompt '{name}' needs: {', '.join(missing)}")
    return spec["template"].format(**values)
