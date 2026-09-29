# Guide: assembling parts

Method proven on two assemblies of 30 to 60 components in a live CATIA V5 R19 (Assembly Design). It is
written for an AI agent who fills `templates/assembly_template.py`; the rules are enforced by the
`AssemblyScript` DSL or checked after the run.

```python
from catia_mcp.scripting import AssemblyScript, axis, face
a = AssemblyScript("Gearbox", "out/Gearbox")
housing = a.add("parts/Housing.CATPart")          # -> "Housing.1"
shaft = a.add("parts/Shaft.CATPart")              # -> "Shaft.1"
a.fix(housing, "Fix_Housing")
a.move(shaft, tz=5)                               # pre-position, THEN constrain
a.coincidence(shaft, axis(10, 0, 20), housing, axis(10, 0, 30), "Coax_Shaft_Housing")
a.contact(shaft, face(0, 0, 5), housing, face(20, 0, 5), "Contact_Shaft_Shoulder")
a.finish()                                        # solve, poses, clash analysis, save all, screenshots
```

## 1. Principle

1. `add` every component. `add` returns the INSTANCE name CATIA will use, `<PartNumber>.<k>` (k counts the
   copies of that part: `Bolt.1`, `Bolt.2`). Purchased parts often have a PartNumber different from their
   file name: pass `part_number=` (or the exact `instance_name=`) and confirm with `catia_list_components`.
   A sub-assembly is inserted as a `.CATProduct`; its children are addressed `Sub.1/Part.1`
   (`add(..., parent="Sub.1")` inserts into it).
2. `fix` the reference part: the biggest one, the one that carries the others.
3. **Pre-position** every other component with `move` so it sits at its final place. The solver then has almost
   nothing to do and cannot jump to a wrong solution.
4. **Constrain** each component with named constraints on real faces and axes.
5. `finish()`: update, constraint status, component list, clash analysis, save all, clean display, screenshots.
6. The run compares the solved poses with the intended ones and fails if the solver moved a component.

## 2. Designating geometry

Points are in each part's OWN coordinates (part frame, millimetres), not in the assembly frame.

| Helper | Meaning | Common mistake |
|---|---|---|
| `axis(x, y, z)` | a point ON a cylindrical/conical face: designates its AXIS | a point in a slot or hole instead of on the surface |
| `face(x, y, z)` | a point strictly INSIDE a planar face | on its border, over a hole, or 0.01 mm off |
| `edge(x, y, z)` | a point on an edge | |
| `origin_plane("xy")` | one of the part's origin planes | |

- An `axis` point must be EXACTLY at the cylinder radius. A `face` point measured to 0.01 mm (53.99 instead of
  54) fails with "closest is 0.010 mm away". Read the numbers from the part script or from `catia_list_faces`
  on a SMALL part (never on a dense one: minutes at 100 % CPU, every other user blocked).
- Purchased parts are usually centered on the origin with their axis along Z.
- A cylinder face alone is the wrong geometry for a coincidence: an `axis(...)` point makes the server build the
  AXIS reference. Mixing an axis with a face is refused before CATIA sees it.

## 3. Choosing constraints

| Need | Use | Notes |
|---|---|---|
| Two shafts/bores share an axis | `coincidence(axis, axis)` | Removes 4 degrees of freedom; the turn about the axis stays free |
| Parts touch face to face | `contact(face, face)` | normals OPPOSITE: unambiguous |
| Two planar faces in one plane | `coincidence(face, face)` (coplanar) | unambiguous |
| Reference position of a part | `fix` | one part per assembly, before the rest |
| A gap that cannot be a contact | `offset(..., value, orientation="same"|"opposite")` | last resort |
| Functional angle | `angle(...)` | only when the angle matters |

- **Prefer contact and coplanarity on real faces** over an offset between planes. A distance between planes
  has no reliable sign: 40 constraints reported OK and yet two wheels were stacked on the same side, nuts were
  out of their seats and a bar was flipped. `offset` therefore REQUIRES `orientation`; the run then checks the poses.
- Rotation about an axis may stay free (the pre-positioning fixes it). Constrain it only when it matters (bolt
  holes aligned: a second coaxiality on a hole). Never add a redundant constraint: it goes into conflict.
- Repeated hardware (10 bolts): one instance each, each pre-positioned and constrained (coaxial with its hole +
  contact under the head). Same for nuts.
- Names: `Fix_<part>`, `Coax_<A>_<B>`, `Contact_<A>_<B>`, `Coplan_<A>_<B>`, `Dist_<A>_<B>_<value>`. The DSL
  refuses empty and default names, and duplicates.

## 4. Poses: pre-positioning and verification

`move(instance, tx, ty, tz, rx, ry, rz)` does exactly what `catia_move_component` does:

- rotation (degrees, applied as Rz * Ry * Rx in WORLD axes) turns the component's axes about its OWN origin;
  it does not move the origin;
- then the translation (world coordinates, mm) is added to the origin;
- calls compose: `move(x, rx=180)` then `move(x, tx=30)` = turned over, then carried.

The DSL replays the same arithmetic (`a.pose(instance)`), so it knows the intended pose of every component.
Rules enforced:

- `move` BEFORE `fix` and BEFORE the component's constraints (otherwise the position the solver saw is not the
  position you meant);
- nested components (`Sub.1/Part.1`) cannot be moved individually: move the sub-assembly;
- after solving, `verify_poses(output)` (and the `components` check of the run) compares each pre-positioned or
  fixed component's origin (0.05 mm) and axes (0.001) with the intended pose. A mismatch means a constraint has
  the wrong orientation or picked the wrong face: fix the constraint, not the tolerance.

To orient a part: choose rotations that bring its interface axis onto the wanted direction (e.g. `ry=-90` turns
a Z axis onto -X ... check with `a.pose(inst).axes`), then translate its interface point to the target.

## 5. Interference (clash) analysis

`catia_clash_analysis` tests every pair of components and lists those that interpenetrate (with a penetration
depth in mm) and those that merely touch. `finish()` runs it (limit 900 s: it can be long) and the run fails on
any clash that is not declared: `finish(allow_clashes=[("Bearing.1", "Housing.1")])` for INTENDED
interference such as a press fit (a bearing of diameter 151 in a bore of 150).

Typical causes of an unwanted clash:
- a washer or nut with a flat face resting tangent to a curved bore: it enters by its corners (0.9 mm seen with a
  flange nut placed at R44 in a bore where R43 was needed): move it inward;
- a solver solution on the wrong side (see poses): always fix the poses first;
- overlapping threads: cosmetic threads do not clash, modelled ones do.

Keep the clash list short: 0 clashes except justified interference, each one written down.

## 6. Order of steps and saving

- `finish()` runs: `update_assembly` ("all OK" or the broken constraints), `list_constraints`,
  `list_components` (poses), `clash_analysis`, `save_all`, then display cleanup.
- `save_all` writes `<Assembly>.CATProduct` and a COPY of every part/sub-product into the folder: the folder is
  self-contained and the original parts are never modified.
- `clean_display` only hides planes, sketches, axes and constraint symbols (sub-assemblies included), and the
  tree with `hide_tree=True`. It comes AFTER `save_all`. The first screenshot shows the whole window (with the
  tree), the next ones use the viewer without the tree; the setting sticks to CATIA's window, so the DSL restores
  the tree at the end.
- Screenshots of the viewer are written as `.jpg` whatever extension is requested.

## 7. Troubleshooting

| Log says | Meaning | Do |
|---|---|---|
| `unknown instance 'X.2'` (script) | typo, or a copy that was never added | `add` returns the names: use them |
| `Component ... not found ... Available: ...` | CATIA named the instance differently (PartNumber differs from file) | `part_number=` / `list_components` |
| constraint status "wrong geometry type" | axis vs face mix, or a cylinder face instead of its axis | use `axis(...)` for cylinders |
| `closest is 0.010 mm away` | designation point not on the face | use the exact number |
| `NOT OK: [...]` after update | conflicting or broken constraints | remove redundant constraints; check orientations |
| `pose: X.1: expected origin ..., got ...` | solver placed the part elsewhere | fix constraint orientation; prefer contact |
| `unexpected clash` | interference | see section 5 |
| step killed after `--hang-seconds` | CATIA blocked (dense part, modal dialog) | shorten the scenario; work on the file again |

## 8. Before saying "done"

- `update_assembly`: `all OK`. `list_constraints`: no default names.
- `CHECKS: all passed` (poses, constraints, clashes) in the log.
- Screenshots compared with the assembly drawing (exploded view, section, isometric).
- Bill of materials: reference, part, quantity as on the drawing; the fixed part and the mounting logic written down.

## 9. Speed and scale (measured live)

Measured on CATIA V5 R19 (French UI), a laptop with 8 cores and 32 GB. Times move by up to 3x between
identical runs on this class of machine (background load, thermal state): compare medians of several
runs, interleaved, never a single number. The speed-ups below are 10x or more, far above that noise.

### Where the time goes

`CATIA_MCP_PROFILE=1` (environment of the server or runner) appends, to every call, the split of its time:
`[perf] catia_fillet: total=21.8s overhead=0.19s (snapshot_before=... execute=21.65s ...)`.

- The server's own bookkeeping (tree snapshot, volume check, renaming) costs 0.1 to 0.3 s per feature: negligible.
- **Designating geometry** (`face_point`, `axis_point`, `edge_point`) is the expensive part. Every designation
  opens the part in CATIA, searches its topology, closes it and reactivates the assembly window. That is the
  window that flickers, ~1 s per element and 20-30 s for two EDGES of a dense part (about 50 ms per edge,
  intrinsic to CATIA). Prefer faces and axes; give several edges in ONE call (one search serves all points).
- Each constraint solve re-computes every constraint, so its cost grows with the assembly.

### What removes it

| Measure | Effect |
|---|---|
| `catia_prepare_geometry` (the kit and the runner add it for you) | Every part is opened ONCE and all its points are searched together. A 58-constraint wheel: 92 designations became 53 in 7 openings. |
| Designation cache (file + modification time + point) | 100 identical bolts cost one search, not 200. Bounded LRU (200 000 entries). `CATIA_MCP_DESIGNATION_CACHE=0` disables it. |
| `defer_update` on constraints (`AssemblyScript(..., defer_updates=True)`) | No solve after each constraint; `finish()` solves and checks all once. Cost per constraint stays flat. |
| Direct lookup of components by name | Adding and moving a component no longer slows down as the assembly grows (0.2 s to 1.5 s per insertion at 150 parts before, 0.01 s flat after). |

Results: contact constraint 2.48 s to 0.16 s on average (x15); 30-component, 58-constraint wheel 293 s to 47 s;
150 constraints 249 s to 36 s; 300 instances with 301 constraints (906 steps) in 81 s, zero errors.

### Very large assemblies (thousands of parts and more)

The MCP layer is linear: validating 100 000 steps takes about 4 s, listings are paginated, the cache is bounded.
What limits a vehicle or an aircraft is CATIA itself (memory, licence, load time), so structure the work:

1. One sub-product per system; build, update, check constraints and run the clash analysis on it ALONE, save,
   then insert it into its parent. Save after every sub-assembly so an interruption loses at most one.
2. Send steps in chunks of 25-500 in `catia_batch` (or scenario files of any size with `catia-mcp-run`), with
   `stop_on_error`, keeping the chunk boundaries in a progress file so a run can resume.
3. Never list the whole tree: `catia_list_components` takes `path`, `depth`, `limit`, `offset`
   (`depth=1, limit=50` = one page of direct children). A listing above 5000 components is cut and flagged.
4. Clash analysis per sub-assembly, then between neighbouring sub-assemblies only.
5. Start the server with `CATIA_MCP_TOOLSETS=assembly` to keep the tool list small.
6. Use `defer_updates=True` for the final run of a stable assembly, `False` while debugging a new one
   (a wrong constraint is then reported at the step that made it).
