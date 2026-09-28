"""Scripting kit: Python DSL that EMITS validated CATIA tool calls (never imports COM).

    from catia_mcp.scripting import PartScript

    p = PartScript("Flange", "out/Flange")
    with p.sketch("xy", "Sk_Disc") as sk:
        sk.circle(0, 0, 40)
    p.pad("Disc_D80_T10", 10)
    p.save()
    p.checks(volume=3.14159 * 40**2 * 10)

    p.write_json("flange.json")     # then: python -m catia_mcp.runner flange.json --dry-run
    p.run(dry_run=True)             # or validate against the real tool schemas right here
"""

from catia_mcp.scripting._base import SCENARIO_FORMAT, ScriptResult, cli, load_schemas
from catia_mcp.scripting._common import ScriptError, check_name
from catia_mcp.scripting.assembly import AssemblyScript, axis, edge, face, origin_plane
from catia_mcp.scripting.inspection import inspect_steps
from catia_mcp.scripting.part import PartScript, Plane, Sketch, arc_seg, line_seg
from catia_mcp.scripting.poses import Pose, check_poses, rot_matrix
from catia_mcp.scripting.verify import verify as verify_results

__all__ = [
    "AssemblyScript", "PartScript", "Plane", "Pose", "SCENARIO_FORMAT", "ScriptError", "ScriptResult",
    "Sketch", "arc_seg", "axis", "check_name", "check_poses", "cli", "edge", "face", "inspect_steps", "line_seg",
    "load_schemas", "origin_plane", "rot_matrix", "verify_results",
]
