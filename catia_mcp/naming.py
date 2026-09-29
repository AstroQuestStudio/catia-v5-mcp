"""Name every created feature at creation time (optional `name` argument).

The specification tree is the deliverable of a CAD model: a feature left as "Extrusion.3" or
"Trou.2" makes it unreadable and is penalised in any graded or reviewed work. Instead of teaching ~30 tools to rename their own
result, the server snapshots the tree before a creation tool runs, finds the
object(s) that appeared, and renames the newest one to `arguments["name"]`
(then reads the name back to prove it took).
"""

from __future__ import annotations

import re
from typing import Any

# Language-independent: CATIA's default names are "<Type>.<N>" in every UI
# language (Extrusion.3, Pad.1, Esquisse.2, Congé arête.1...), plus the main
# body ("Corps principal" / "PartBody").
_DEFAULT_RE = re.compile(r"(?<!\d)\.\d+$")
_DEFAULT_EXACT = {"Corps principal", "PartBody", "Main Body"}


def is_default_name(name: str) -> bool:
    # CATIA's defaults never contain '_' and never have a digit right before the dot:
    # a user name like 'Esq_Collerette_R57.5' ends with a decimal DIMENSION, not an
    # instance counter (it was silently renamed before this check, seen live).
    if name in _DEFAULT_EXACT:
        return True
    return "_" not in name and bool(_DEFAULT_RE.search(name))

# Tools whose result is a new Part Design feature or wireframe/surface element.
# Tools that already take their own "name" (catia_new_body, catia_create_sketch,
# catia_new_part, GSD geoset creation...) are left alone: see inject_name_property.
CREATION_TOOLS = {
    "catia_pad", "catia_pocket", "catia_shaft", "catia_groove", "catia_fillet",
    "catia_chamfer", "catia_hole", "catia_rect_pattern", "catia_circ_pattern",
    "catia_mirror", "catia_shell", "catia_draft", "catia_thickness",
    "catia_boolean_operation", "catia_thread",
}

_NAME_PROPERTY = {
    "type": "string",
    "description": (
        "Explicit name for the created feature in the specification tree "
        "(e.g. 'Pad_Bras_20mm', 'Trou_Conique_D12'). Strongly recommended: "
        "default names like 'Extrusion.3' make the tree unreadable and are flagged by tree audits."
    ),
}


def is_creation_tool(tool_name: str) -> bool:
    return tool_name in CREATION_TOOLS or (
        tool_name.startswith("catia_gsd_")
        and tool_name not in ("catia_gsd_list_elements", "catia_gsd_set_active_geoset", "catia_gsd_create_geoset")
    )


def inject_name_property(tool_def: dict[str, Any]) -> bool:
    """Add the `name` property to a creation tool's schema. Returns True if
    the server is responsible for renaming (i.e. the tool had no own `name`)."""
    if not is_creation_tool(tool_def["name"]):
        return False
    props = tool_def["inputSchema"].setdefault("properties", {})
    if "name" in props:
        return False
    props["name"] = dict(_NAME_PROPERTY)
    return True


def snapshot(conn: Any) -> list[tuple[str, Any]] | None:
    """Every feature / hybrid shape of the active part, as (path, object)."""
    try:
        part = conn.get_active_part()
    except Exception:
        return None
    items: list[tuple[str, Any]] = []
    try:
        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            body = bodies.Item(i)
            shapes = body.Shapes
            for j in range(1, shapes.Count + 1):
                s = shapes.Item(j)
                items.append((f"{body.Name}/{s.Name}", s))
            try:
                hs = body.HybridShapes
                for j in range(1, hs.Count + 1):
                    s = hs.Item(j)
                    items.append((f"{body.Name}/{s.Name}", s))
            except Exception:
                pass
            # Sketches are tracked too: a Hole silently adds a second, internal
            # sketch at update time (proven live), left as "Esquisse.N".
            sketches = body.Sketches
            for j in range(1, sketches.Count + 1):
                s = sketches.Item(j)
                items.append((f"sk:{body.Name}/{s.Name}", s))
        hbodies = part.HybridBodies
        for i in range(1, hbodies.Count + 1):
            hb = hbodies.Item(i)
            hs = hb.HybridShapes
            for j in range(1, hs.Count + 1):
                s = hs.Item(j)
                items.append((f"{hb.Name}/{s.Name}", s))
    except Exception:
        return None
    return items


def _hide(conn: Any, obj: Any) -> None:
    sel = conn.hso
    sel.Clear()
    sel.Add(obj)
    sel.VisProperties.SetShow(1)  # 1 = hidden
    sel.Clear()


def after_creation(conn: Any, before: list[tuple[str, Any]] | None, new_name: str | None) -> str:
    """Post-process the object a creation tool just made. Returns notes.

    - rename it to `new_name` (verified by reading the name back);
    - hide the sketch it consumed, like the GUI does (otherwise every used
      sketch stays drawn over the solid);
    - give that sketch a meaningful name if it still has a default one
      (e.g. a hole's internal positioning sketch 'Esquisse.7').
    """
    after = snapshot(conn)
    if before is None or after is None:
        return f"[naming] could not inspect the tree; '{new_name}' NOT applied." if new_name else ""
    known = {path for path, _ in before}
    new_items = [(path, obj) for path, obj in after if path not in known]
    created = [(p, o) for p, o in new_items if not p.startswith("sk:")]
    new_sketches = [o for p, o in new_items if p.startswith("sk:")]
    if not created:
        return f"[naming] no new feature detected; '{new_name}' NOT applied." if new_name else ""
    _, obj = created[-1]
    notes = []

    if new_name:
        existing = {p.split("/", 1)[1] for p, _ in after if not p.startswith("sk:")}
        if new_name in existing:
            notes.append(f"[naming] '{new_name}' already exists in the part; kept '{obj.Name}'. Choose a unique name.")
        else:
            old = obj.Name
            obj.Name = new_name
            if obj.Name != new_name:
                notes.append(f"[naming] rename of '{old}' to '{new_name}' did not take effect.")
            else:
                notes.append(f"[naming] '{old}' renamed to '{new_name}'.")

    try:
        sketches = [obj.Sketch]
    except Exception:
        sketches = []
    seen = {s.Name for s in sketches}
    sketches += [s for s in new_sketches if s.Name not in seen]
    # A Hole carries TWO sketches (proven R19): obj.Sketch = the positioning point,
    # plus CATIA's own profile sketch (axis + profile lines). Name them by role
    # instead of "Esq_Trou_2", which reads like a stray duplicate in the tree.
    try:
        _ = obj.ThreadingMode  # only Hole features expose it
        is_hole = len(sketches) == 2
    except Exception:
        is_hole = False
    for k, sketch in enumerate(sketches):
        try:
            if is_default_name(sketch.Name):
                old = sketch.Name
                if is_hole:
                    sketch.Name = f"Esq_{'Position' if k == 0 else 'Profil'}_{obj.Name}"
                else:
                    sketch.Name = f"Esq_{obj.Name}" + ("" if k == 0 else f"_{k + 1}")
                notes.append(f"[naming] sketch '{old}' renamed to '{sketch.Name}'.")
            _hide(conn, sketch)
        except Exception as e:
            notes.append(f"[tidy] could not tidy sketch: {e}")
    return "\n".join(notes)


def rollback(conn: Any, before: list[tuple[str, Any]] | None) -> str:
    """Remove what a FAILED creation tool left behind, so a failed call has no side effect.

    CATIA keeps a rejected feature in the tree (seen live: a pad on an open profile left
    'Extrusion.2' behind). Everything that appeared since ``before`` is deleted, newest first:
    features and shapes always, sketches only when they still carry a default name (an
    internal sketch such as a hole's; the user's own sketch existed before the call).
    Returns a note, or '' when there was nothing to clean or the tree could not be read.
    """
    after = snapshot(conn)
    if before is None or after is None:
        return ""
    known = {path for path, _ in before}
    leftovers = [(p, o) for p, o in after if p not in known]
    removed, failed = [], []
    for path, obj in reversed(leftovers):
        try:
            name = obj.Name
            if path.startswith("sk:") and not is_default_name(name):
                continue
            sel = conn.hso
            sel.Clear()
            sel.Add(obj)
            sel.Delete()
            sel.Clear()
            removed.append(name)
        except Exception as e:
            failed.append(f"{path.split('/', 1)[-1]} ({str(e)[:60]})")
    notes = []
    if removed:
        notes.append(f"[cleanup] removed what the failed call left in the tree: {', '.join(removed)}.")
    if failed:
        notes.append(f"[cleanup] could not remove: {'; '.join(failed)}. Delete it with catia_delete_feature.")
    return "\n".join(notes)
