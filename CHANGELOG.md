# Changelog

## [0.3.0] - unreleased

First release of this line. A CATIA V5 R19 automation server built for agents that must get it
right the first time, and keep working unattended.

### Added
- **`catia_batch`**: many tool calls in one round trip. Every step is validated against the tool
  schemas first (unknown tool, misspelt or missing argument, wrong type, bad enum), so a typo in
  step 31 is reported before CATIA is touched. Stops at the first failure; `dry_run` validates only.
- **Lessons ledger** (`catia_lessons`, `catia_add_lesson`, `docs/LESSONS.md`): 124 verified pitfalls
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

### Changed
- State (log, lock, lessons) lives in `%APPDATA%\catia-mcp` (or `CATIA_MCP_HOME`), never in the package.
- `catia_delete_feature` now requires `name` (it used to delete the last feature when omitted).
- The auto-trace journal is opt-in (`CATIA_MCP_AUTOTRACE=1`).
- pywin32 is only required on Windows; the offline test suite runs on any OS.
