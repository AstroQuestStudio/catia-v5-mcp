# Live validation checklist

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
- [ ] `python -m catia_mcp.runner tests/live/features.json` : every step OK.
- [ ] `python -m catia_mcp.runner tests/live/gsd.json` : every step OK.
- [ ] Record the timings; they are the baseline for section 6.

## 3. New behaviour that touches COM
- [ ] `catia_batch` with 30 mixed steps, including `catia_screenshot` in the middle: the screenshot is
      not stale (display freezing is per step, not per batch).
- [ ] Popup watchdog: provoke an information box (open a corrupt file); it is closed and logged in
      `catia_popups.log`. Provoke a Yes/No question; it is left open and reported.
- [ ] `CATIA_MCP_LOCK=1`: start two runners; the second waits, then proceeds.
- [ ] `CATIA_MCP_HANG_SECONDS=20`: a deliberately slow call is logged as a hang. With
      `CATIA_MCP_HANG_KILL=1` CATIA is killed and the next scenario restarts it.
- [ ] `CATIA_MCP_AUTOTRACE=1`: a CSV line and a screenshot per feature in the trace directory.
- [ ] `catia_delete_feature` without `name` is rejected; with a name it deletes exactly that.

## 4. Suspects from the code review (fix or document)
- [ ] `catia_sketch_constraint`: constraint type codes disagree with the assembly ones read from the
      type library. Test every constraint kind on a scratch sketch.
- [ ] `catia_list_faces` / `catia_list_components` on a large model: add `limit` / `offset`, cap the output.
- [ ] `SaveAs` over an existing file: overwrite silently today; add an explicit `overwrite` flag.
- [ ] `orientation` is hidden for coincidence / contact / angle constraints: expose it or explain why not.
- [ ] Swallowed exceptions (`except Exception: pass`): triage the ~37, log or surface the ones that hide failures.

## 5. Scale (vehicle / aircraft class)
- [ ] Build a synthetic assembly: 500 instances of 5 distinct parts in 10 sub-products. Record time per
      100 components; check it stays linear.
- [ ] `catia_list_components` on a sub-product versus the root: keep responses small.
- [ ] Constraint solving cost: compare update after every constraint versus one update per sub-product
      (manual update mode). This was the largest single cost in real runs (~6 s per constraint).
- [ ] `catia_clash_analysis` on one sub-product versus the whole tree: time and memory.
- [ ] Interrupt a long scenario and resume it from its progress file.

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
