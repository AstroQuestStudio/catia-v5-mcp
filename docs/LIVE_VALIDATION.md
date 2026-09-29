# Live validation checklist

Last full pass: 2026-09-29, CATIA V5 R19 (French UI), Windows 11. Items marked **DONE** were proven on that pass
with the figure given; the others are still open.

Everything below needs a licensed CATIA V5 session. The offline suite (`pytest`, which sets
`CATIA_MCP_OFFLINE=1`) covers schemas, batching, lessons, guards, profiles, drawings and the
scripting kit; it cannot prove COM behaviour. Run this list before every release and record the
CATIA build you used. Anything marked **UNVERIFIED-LIVE** in the source is on it.

## 0. Offline first (2 min)
- [ ] `pip install -e ".[dev]"` then `pytest` : all green, CATIA not started.
- [ ] `ruff check catia_mcp tests` : clean.

## 1. Connection and protocol (5 min)
- [ ] Register the server, open a client: the instructions text is shown, `tools/list` succeeds.
- [ ] `CATIA_MCP_TOOLSETS=assembly` : the list shrinks, a hidden tool still works when called.
- [ ] Resources `catia://rules`, `catia://lessons`, `catia://guides/*` open; prompts appear.
- [ ] A failing call is reported as an error (`isError`) with a lesson hint appended when one matches.

## 2. Regression scenarios (10-20 min)
- [x] **DONE** `python -m catia_mcp.runner tests/live/features.json` : 33 steps OK.
- [x] **DONE** `python -m catia_mcp.runner tests/live/gsd.json` : 28 steps OK.
- [ ] Record the timings; they are the baseline for section 6.

## 3. New behaviour that touches COM
- [ ] `catia_batch` with 30 mixed steps, including `catia_screenshot` in the middle: the screenshot is
      not stale (display freezing is per step, not per batch).
- [x] **DONE** Popup watchdog (single-OK box closed and logged in 2 s; question boxes are unit-tested only): provoke an information box (open a corrupt file); it is closed and logged in
      `catia_popups.log`. Provoke a Yes/No question; it is left open and reported.
- [x] **DONE** `--lock`: the second runner waited 6 s, then proceeded without interleaving.
- [x] **DONE** hang guard (`--hang-seconds 2` killed CATIA, exit code 3, CATIA relaunched by the next run): a deliberately slow call is logged as a hang. With
      `CATIA_MCP_HANG_KILL=1` CATIA is killed and the next scenario restarts it.
- [ ] `CATIA_MCP_AUTOTRACE=1`: a CSV line and a screenshot per feature in the trace directory.
- [x] **DONE** `catia_delete_feature` without `name` is rejected by the schema.

## 4. Suspects from the code review (fix or document)
- [ ] `catia_sketch_constraint`: constraint type codes disagree with the assembly ones read from the
      type library. Test every constraint kind on a scratch sketch.
- [ ] `catia_list_faces` / `catia_list_components` on a large model: add `limit` / `offset`, cap the output.
- [ ] `SaveAs` over an existing file: overwrite silently today; add an explicit `overwrite` flag.
- [ ] `orientation` is hidden for coincidence / contact / angle constraints: expose it or explain why not.
- [ ] Swallowed exceptions (`except Exception: pass`): triage the ~37, log or surface the ones that hide failures.

## 5. Scale (vehicle / aircraft class)
- [x] **DONE (300 instances)** Build a synthetic assembly: 500 instances of 5 distinct parts in 10 sub-products. Record time per
      100 components; check it stays linear.
- [x] **DONE (offline, fake COM)** `catia_list_components` on a sub-product versus the root: keep responses small.
- [ ] Constraint solving cost: compare update after every constraint versus one update per sub-product
      (manual update mode). This was the largest single cost in real runs (~6 s per constraint).
- [ ] `catia_clash_analysis` on one sub-product versus the whole tree: time and memory.
- [ ] Interrupt a long scenario and resume it from its progress file (runner `--start-at` not written yet).

## 6. CATIA performance experiments (change one setting at a time, keep > 5 % wins)
Measure with the section 2 scenarios (median of 3) and a 200-step view rotation.
- [ ] Which GPU renders CATIA (`nvidia-smi pmon` while rotating a large model). Force the dedicated GPU
      for `CNEXT.exe` in Windows graphics settings if needed.
- [ ] Manual update mode plus one explicit update at the end of a batch.
- [ ] Display settings (3D accuracy, anti-aliasing), undo stack, cache. Back up `CATSettings` first.
- [ ] Expect little from more cores: modelling and tree updates are essentially single-threaded.

## 7. Drafting and reverse engineering (next milestone)
- [ ] Dump the Drafting, Knowledgeware and material type libraries and read the exact signatures.
- [ ] Prototype drawing creation with one view and one dimension; export to PDF; read the PDF back with
      `drawing_extract_geometry` and compare with the 3D bounding box.

## 8. Results of 2026-09-29 (numbers)
- Scripting kit, live: three parts (housing, shaft, cover) and their 3-constraint assembly built from the templates,
  all built-in checks passed (volume, bounding box, names, constraint status, poses, clash analysis: 0 clashes).
- 300 instances / 301 constraints / 906 steps: 81 s, 0 errors; contact constraint 0.10 s to 0.23 s from the first to the last quarter.
- 58-constraint wheel: 293 s (before) to 102 s (cache) to 47-53 s (prepare + defer).
- The same scenario can vary 3x between identical runs (a gear scenario measured 13 to 52 s with the same code).
  Compare medians of interleaved runs. Do not chase a "regression" from a single run.
- Fillet on a 36-tooth gear: 20-30 s, of which 99 % is the designation of two edges (intrinsic to CATIA).
- Open: the suspects of section 4 (sketch constraint codes, `SaveAs` overwrite, swallowed exceptions).

## 9. Results of the second live session (2026-09-29, afternoon)
- Full rebuild of a 63-element project (58 parts, 5 assemblies up to a 13-component final assembly):
  48 minutes, 62 of 63 first pass; the last one failed because of a save regression fixed the same day
  (see lessons on Save As of multi-instance parts), then passed.
- Designation cache on disk: a 70-component assembly rebuilt in 164 s instead of 780 s, no error.
- Drafting replay (`tests/live/drafting.json`): 51 steps OK in 26 s, including PDF export and the closed-loop check.
- Reverse engineering: describe, replay and compare on brackets, flanges, stepped pins, plates, a mirrored plate
  and a 6-feature link: volume, box, centre of gravity and inertia identical.
- Audit of 59 parts and 5 assemblies: no real defect; the "feature not up to date" findings were caused by the
  audit reading the features.
- Popup watchdog: the "files not found or wrong content" dialog is now dismissed automatically.
- One CATIA client at a time: two scenarios in parallel froze CATIA; sequenced, both ran normally.
