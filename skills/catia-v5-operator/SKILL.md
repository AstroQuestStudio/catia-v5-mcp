---
name: catia-v5-operator
description: Drive CATIA V5 (R19+) through the catia-v5-mcp server without repeating known mistakes. Use for any CATPart / CATProduct modelling, assembly, measurement, drawing-reading or audit task that calls catia_* tools.
---

# Operating CATIA V5 through the MCP server

The server already sends its golden rules as instructions and appends the matching lesson to
errors. This skill is the working method around them. Install it by copying this folder into
`~/.claude/skills/` (or your agent's equivalent).

## Before you start
1. `catia_lessons` (no query) : the critical rules. Query it again before a risky operation:
   topology selection, booleans, assembly constraints, measurements, anything that failed once.
2. Read the guide resources for your task: `catia://guides/part-modeling`, `catia://guides/assembly`,
   `catia://guides/drawings`.
3. Large job? Start the server with `CATIA_MCP_TOOLSETS=part` (or `assembly`) to keep the tool list small.

## Method for a part
1. Get exact numbers first: `drawing_render` + `drawing_extract_geometry` for a PDF drawing. Dimension
   text is vector art, so read values visually and confirm them with the measured geometry
   (radius vs diameter, wrong title-block scale, rounded non-tangent values).
2. Plan on paper: one body per functional zone, the main body named `<Part>_Result`, every feature
   with its expected volume change. Then write ONE scenario, not 60 improvised calls.
3. `catia_batch` with `dry_run=true`, then for real. It stops at the first failure.
4. Name everything (`name` argument on every creation tool). No `Pad.1` may survive.
5. Designate geometry by 3D points (`catia_list_faces` / `catia_list_edges` give safe ones), never by guessed names.
6. Verify, do not assume: the volume change reported by each feature, `catia_get_inertia`,
   `catia_get_bounding_box`, `catia_get_tree`, and `drawing_overlay` of every non-trivial sketch on the drawing.

## Method for an assembly
1. Compute every component's target pose from real interfaces (faces, axes) before adding it.
2. Fix one part, pre-position the others with `catia_move_component`, then add constraints.
3. Prefer contact or coincidence on real faces. An offset between planes is unsigned: a part can flip
   while every constraint reads OK. Verify the final poses against the intended ones.
4. `catia_update_assembly`, `catia_list_constraints` (all OK), `catia_clash_analysis`; explain each clash.
5. Save per sub-assembly; work sub-product by sub-product on big models.

## When something fails
- Read the appended `[lesson ...]` hint first; then `catia_lessons` with the error text.
- A generic `UpdateObject failed` says nothing about the cause: isolate by variants, do not guess.
- A hung call: the popup watchdog reports dialogs in the log; a Yes/No question needs a human.
- Never guess a COM signature or enum value; dump the type library and read the exact signature.

## Leave the campsite better
Found something that failed, surprised you or needed a workaround? Call `catia_add_lesson` with the
exact error text as an `error_patterns` regex and how you proved the fix. Every future session,
yours and other agents', is warned automatically.

## Never
- Circumvent or emulate a CATIA licence.
- Delete or overwrite a user's files without an explicit request.
- Answer a CATIA question dialog blindly.
