# Reverse engineering a model: describe, audit, measure

Three read-only tools work on the ACTIVE document (they never modify it):

| Tool | What it returns |
|---|---|
| `catia_describe_model` | CATPart: JSON spec (bodies, features in tree order, sketches with exact geometry and constraints, parameters, measurements) + the source of a replayable `PartScript`. CATProduct: components, poses, constraints, statuses. |
| `catia_audit_model` | Findings ranked `error > warning > info`, each with the exact tool calls that fix it (`fix`) or a `manual` instruction when no tool can. |
| `catia_measure_model` | Volume, area, centre of gravity, exact bounding box and inertia of each body: the yardstick to compare an original with its replay. |

The modules `catia_mcp/tools/reverse.py` is loaded by the server automatically when it imports.

## Describe

```
catia_describe_model {"output_dir": "out/spec", "replay_name": "Bracket_Replay"}
```

* `spec` (format `catia-mcp-model-spec/1`): part, parameters, relations, bodies with their features (`recognised`
  true/false), every sketch (frame, geometry in the sketch axes in mm, constraints by element id, `dof`),
  wireframe elements, `checks` (measurements of the original), `opaque`, `replay` (complete or not).
* `script`: a `PartScript` (see `catia_mcp/scripting`). Run it with `python script.py --dry-run` (schema check only) then
  without argument (it builds a NEW document, never closes the others, and compares volume and box with the original).
  Names that are CATIA defaults (`Pad.1`, `PartBody`) cannot be used by the scripting kit: they are replaced by
  `<name>_Rebuilt`; the mapping is in `spec.names`.
* `output_dir` writes `<part>_spec.json` and `<part>_replay.py`.

### Recognised (replayed)

Pad (length, symmetric, second length, direction), pocket (length, up to next/last, direction), shaft and groove
(angle, axis = absolute H/V axis or a sketch line), hole (simple, tapered, counterbored, countersunk, threaded flag,
plain depth), constant-radius fillet and chamfer (edges designated by a point on them), mirror about an origin plane,
Assemble/Add/Remove/Intersect with their operand body (replayed with the multi-body method), sketches made of lines,
arcs, circles and points placed on an origin plane or a plane parallel to one (the frame is read from the sketch, so a
sketch drawn on a face is replayed on the equivalent offset plane).

### Opaque ("black box"), never guessed

Each opaque entry has name, type, where, what is known, and why. Solid-affecting ones set `replay.complete=false`:

* rectangular and circular patterns (the direction and axis that Automation returns do not match the ones used to
  create them; count and spacing are kept in `known`), shell, draft, thickness, thread, union-trim;
* holes with V or trimmed bottom, limited up to a face, anchored at their middle; fillets with a face selection or
  minimal propagation; reversed chamfers; thin pads, second angles;
* deactivated features, features not up to date, unknown feature types;
* sketches containing ellipses or splines (the control points cannot be read back exactly), sketch planes tilted
  against the origin planes;
* any construction the scripting kit refuses (profile crossing itself, profile crossing the revolution axis...): the
  kit's message is the reason.

Wireframe and surface elements are listed (`wireframe`) but not replayed.

### Limits stated up front

* **Constraints are described, not replayed.** The sketch tools address elements by index and the replay draws
  each profile as connected segments, so the constraints of the original are not recreated; dimensions become values.
* **Degrees of freedom are unknown.** The Automation `Sketch` interface has no analysis member (checked against the type
  library and on a live sketch). Contradictory constraints are visible (status `not satisfied`), an iso-constrained
  sketch cannot be proven.
* Edge references are reproduced by a point on the edge; they resolve in the replay only if the geometry is identical.
* Products: no replay script (assembly replay needs stable designation points per part).
* The exact bounding box costs about 0.25 s per face; parts above 400 faces skip it (`bbox_skipped`).
* The approximation of design intent is an aid to audit and regeneration, not a certified round trip: always compare
  the replay with `catia_measure_model`.

## Audit

```
catia_audit_model {"min_severity": "warning"}
```

| Code | Severity | Meaning | Fix |
|---|---|---|---|
| `tree.default_name` | warning | body, feature, sketch or geometrical set with a default name | activate body + `catia_rename_feature` / `catia_rename_body` |
| `tree.empty_body`, `tree.body_without_result` | error | body without any feature, or with no material-making feature, or volume 0 | manual |
| `tree.unmerged_body` | warning | non-main body with features not merged into the result | manual |
| `tree.hidden_body` | warning | body hidden | `catia_hide_show_body` |
| `tree.hidden_element` | info | solid feature or geometrical set hidden | |
| `tree.order` | warning | fillet/chamfer/shell before any material, hole before any pad | manual |
| `feature.not_up_to_date` | error | update failed or pending | `catia_update_part` |
| `feature.inactive` | warning | feature deactivated | manual |
| `sketch.unused` | warning | no feature uses the sketch (internal sketches of holes are exempt) | `catia_delete_feature` (kind sketch) |
| `sketch.not_closed` | error | a profile that feeds a feature has free ends | manual |
| `sketch.constraint_error` | error | constraint not satisfied: over-constrained or contradictory | manual |
| `sketch.unconstrained` | info | curves and no constraint at all | manual |
| `sketch.empty` | warning | no geometry | |

Assemblies: `constraint.not_ok`, `constraint.inactive`, `constraint.default_name`, `component.unconstrained` (not
constrained and not fixed), `component.default_part_number`, `component.unresolved` (broken link),
`assembly.no_fixed_component`. `not_checked` lists what the API cannot tell.

Features of an absorbed operand body cannot be activated, so their fixes are `manual`.

## Checking a replay

1. open the original, run `catia_measure_model`, `catia_describe_model` with `output_dir`;
2. run the generated script (new document);
3. run `catia_measure_model` on the replay and compare with `catia_mcp.tools.reverse.compare_measures`
   (volume 0.1 %, box 0.05 mm, centre of gravity 0.05 mm, mass-normalised principal moments 0.5 %).

The scenario files `tests/live/reverse_*.json` are generated replays (run with `python -m catia_mcp.runner`) and a
describe/audit/measure scenario for whatever part is open. Offline tests: `pytest tests/test_reverse.py`.

## Proven live (CATIA V5 R19, French UI)

Round trip = describe, run the generated script in a new document, compare with `catia_measure_model`.

| Part (built with the kit unless noted) | Features | Volume delta | Box delta | COG / inertia delta |
|---|---|---|---|---|
| L bracket (profile pad, 2 holes, 2 fillets) | 5 | 0 | 0 mm | 0 |
| Flange (pad + 3 body operations, symmetric cutters) | 4 | 0 | 0 mm | 0 |
| Stepped pin (shaft, groove, chamfer) | 3 | 0 | 0 mm | 0 |
| Plate (pocket from an offset plane, reversed, symmetric and two-limit pads) | 5 | 0 | 0 mm | 0 |
| Mirrored plate | 2 | 0 | 0 mm | 0 |
| Complex arm with 5 body operations, pocket, 7 sketches with large arcs (not built with the kit's helpers) | 6 | 2e-9 relative | not measured (over 400 faces) | 0 |

Not exact, and why: a plate with a rectangular and a circular hole pattern replays 0.8 % too heavy because both patterns
are opaque (reported, not replayed); an arm whose two holes go up to a face replays the first hole and the kit
then refuses the second one (the point inside its limiting face is not on a face in the replay), so its replay stops with
2.2 % of the volume missing: recognised does not mean replay-verified, always compare.
`UNVERIFIED-LIVE`: hole up to a face beyond the first hole, hole variants other than the simple one, counterbore,
countersink and tapered holes, chamfer with two lengths, sketches on planes with a flipped normal (unit-tested only).
Assemblies: components, poses, constraints and statuses read on a three-part assembly (fixed base, contact, default-named
coincidence, contradictory offset, one floating part); the audit reported the contradictory offset (error), the
default name and the floating component (warnings).
