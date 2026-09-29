# Changelog

## [0.4.0] - 2026-09-29

### Added
- **Safety tiers** (`CATIA_MCP_SAFETY`, `catia_get_safety_state`, `catia_set_safety`): read / write / dangerous.
  A ratchet: a tool call can only lower the tier, a human restarts the server to raise it. `catia_batch`
  refuses a batch up front if one step is forbidden.
- **Drafting** (`catia_drawing_create`, `_add_view`, `_add_dimension`, `_generate_dimensions`,
  `_add_centerlines`, `_title_block`, `_export_pdf`, `_check`, `_info`, `_close`): norm-aware 2D drawings
  from a 3D part, checked against the model. Live replay: `tests/live/drafting.json`.
- **Reverse engineering** (`catia_describe_model`, `catia_audit_model`, `catia_measure_model`): describe an
  existing part or assembly, replay it as a script, audit it (default names, empty or hidden bodies,
  unused or open sketches, unsatisfied or contradictory constraints, floating components), and compare
  original and replay (volume, box, centre of gravity, inertia). Opaque features are listed, never guessed.
- **Persistent designation cache** (`CATIA_MCP_DESIGNATION_CACHE_DISK`, default on).
- **Popup watchdog**: also dismisses the "files not found or wrong content" dialog (Close, never Desktop).
- 152 lessons (28 new), among them: one CATIA client at a time, plane coincidence orientation, offset sign,
  Save As of multi-instance parts, mirrored faces, audit side effects.

### Changed
- `catia_save_all` no longer renames a part inserted several times, and gives two different documents that
  share a file name distinct files.
- Inspection tools of the optional modules are read tier.

## [0.3.0] - 2026-09-29

First release of this line. A CATIA V5 R19 automation server built for agents that must get it
right the first time, and keep working unattended.

### Added
- **`catia_batch`**: many tool calls in one round trip. Every step is validated against the tool
  schemas first (unknown tool, misspelt or missing argument, wrong type, bad enum), so a typo in
  step 31 is reported before CATIA is touched. Stops at the first failure; `dry_run` validates only.
- **Lessons ledger** (`catia_lessons`, `catia_add_lesson`, `docs/LESSONS.md`): 152 verified pitfalls
  with cause, rule and proof. The critical ones are sent to every client as server instructions;
  the matching lesson is appended automatically to an error message; agents record new discoveries
  in a user file that is merged at the next start.
- **Resources and prompts**: rules, lessons and guides as MCP resources; ready-made workflows
  (`model_part_from_drawing`, `assemble_product`, `large_assembly`, `audit_model`,
  `reverse_engineer_part`).
- **Tool sets** (`CATIA_MCP_TOOLSETS=part|assembly|surface|review|full`): advertise only the tools a
  task needs. Hidden tools still work when called.
- **Tool annotations** (read-only / destructive / idempotent hints) so clients can auto-approve
  harmless calls and ask before risky ones. Failures are now reported with `isError`.
- **Guard rails** for unattended sessions: popup watchdog (closes single-OK dialogs, never answers
  a question), cross-process lock, hang guard (`CATIA_MCP_HANG_SECONDS`, optional kill).
- **Multi-body method**: body tools (`catia_new_body`, `catia_rename_body`, `catia_activate_body`, ...)
  and boolean operations between bodies.
- **Assembly**: contact / coincidence / offset / angle constraints with axis and face references,
  `catia_move_component`, `catia_clash_analysis`, `catia_clean_display`, `catia_save_all`.
- **Automatic naming**: every creation tool takes `name`; the server renames the new object, reads
  the name back, hides consumed sketches, and warns when a default name is left.
- **Self-checking features**: every solid feature reports its volume change.
- **Geometry designation by 3D point** instead of fragile names (faces, edges, holes, constraints).
- **Optional drawing tools** (`pip install "catia-v5-mcp[drawing]"`): exact geometry from vector PDF
  drawings, renders, and sketch-over-drawing overlays.
- `catia-mcp-run`: replay a JSON scenario with a per-step timeout, a log and a non-zero exit code.
- `CATIA_MCP_OFFLINE=1` safety switch: never attach to or launch CATIA (tests, CI, unlicensed hosts).

### Performance (measured live on CATIA V5 R19)
- **`catia_prepare_geometry`**: resolves every face / axis / edge designation of an assembly in one pass, opening each
  part once instead of once per constraint (no window flicker). Added automatically by `AssemblyScript` and by the runner
  (`--no-prepare` to disable).
- **Designation cache** keyed on (file, modification time, point), bounded LRU.
- **`defer_update`** on constraints (`AssemblyScript(..., defer_updates=True)`): solve once at the end.
- O(1) component lookup and insertion (they used to slow down with the size of the assembly).
- Result: contact constraint 2.48 s to 0.16 s; 58-constraint wheel 293 s to 47 s; 150 constraints 249 s to 36 s;
  300 instances with 301 constraints in 81 s.
- `catia_list_components` takes `path`, `depth`, `limit`, `offset`; the runner accepts scenario files of any size
  (100 000 steps validate in about 4 s).
- `CATIA_MCP_PROFILE=1` shows where the time of each call goes.

### Fixed
- A failed creation tool no longer leaves a broken feature in the tree (`[cleanup]`); a lesson that names tools is
  only shown for those tools; whole-part names such as `Shaft` are accepted by the scripting kit.

### Changed
- State (log, lock, lessons) lives in `%APPDATA%\catia-mcp` (or `CATIA_MCP_HOME`), never in the package.
- `catia_delete_feature` now requires `name` (it used to delete the last feature when omitted).
- The auto-trace journal is opt-in (`CATIA_MCP_AUTOTRACE=1`).
- pywin32 is only required on Windows; the offline test suite runs on any OS.
