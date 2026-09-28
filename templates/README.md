# Templates: model a part or an assembly with any AI

Requires the package importable: `pip install -e .` from the repo root (or `PYTHONPATH=<repo>`).

Two short scripts an AI (or a person) fills in, an example scenario, and a runner. The scripts only
EMIT tool calls (`catia_mcp.scripting`); nothing touches CATIA until you run the scenario.

| File | Use |
|---|---|
| `part_template.py` | One part: sketches, features, bodies, expected volume and size. Works as is (a flange). |
| `assembly_template.py` | One assembly: components, fix, pre-positioning, named constraints, clash analysis. |
| `scenario_example.json` | A ready scenario (plate with 4 holes) in the JSON format the runner plays. |

Lines marked `# FILL:` are the ones to change; the comments show the other features available.

## Flow

1. **Read the drawing.** Note the scale, every view, and the dimensions. Measure what is not written.
   (`docs/GUIDE_PART_MODELING.md`, `docs/DRAWINGS.md` for vector PDFs.)
2. **Decide the frame and the bodies**; compute the expected volume and overall size by hand.
3. **Fill the template.** Copy it next to the drawing and edit the `# FILL:` lines.
4. **Dry run**, no CATIA needed:
   ```
   python my_part.py --dry-run
   ```
   Every step is validated against the real tool schemas (typos, missing or mistyped arguments,
   default names, crossing outlines are refused with a message that says what to fix). Repeat until
   it says `DRY RUN OK`.
5. **Run** it in CATIA (one runner at a time; `--lock` queues several):
   ```
   python my_part.py                       # script: writes the JSON, then runs it, stops at the first error
   python -m catia_mcp.runner out/Flange/_build/Flange.json --lock
   ```
   Runner options: `--dry-run`, `--keep-going`, `--hang-seconds 600`, `--no-kill`, `--lock`, `--log PATH`.
   The log `<scenario>.log` has one line per step: `[NN] OK 1.2s tool {args} -> output`, then a summary.
6. **Controls** (built in, printed as `CHECKS:`): volume change of every feature, final volume and mass,
   overall size, naming audit; for assemblies the constraint status, the solved poses against the
   intended ones, and the clash analysis. Exit code 0 only when everything passed.
7. **Look at the screenshots** and compare them with the drawing. A plausible volume does not prove the
   shape. Fix the script, dry-run, run again.

Exit codes of the runner: `0` ok, `1` a step failed, `2` scenario rejected (nothing executed), `3` CATIA
hung (killed unless `--no-kill`), `4` built-in checks failed.

## Useful commands

```
python -m catia_mcp.runner schema pad hole coincidence_constraint   # exact argument schema of any tool
python -m catia_mcp.runner schema --list                            # all tool names
```

```python
from catia_mcp.scripting import inspect_steps
# read volume, centre of gravity and bounding box of existing parts (faces=True only for SMALL parts)
steps = inspect_steps(["parts/Housing.CATPart", "parts/Shaft.CATPart"], faces=False)
```

## Environment

- `CATIA_OUT_DIR`: where the scripts write their folder (default `templates/out`, ignored by git).
- `CATIA_PARTS_DIR`: where the assembly template looks for the CATParts.
- `CATIA_MCP_HOME`: server state (lock file, popup log).

## Rules to remember

- Explicit names everywhere: `Sk_<role>`, `<Role>_<dimension>`, `Body_<zone>`, `<Part>_Resultat`.
- Millimetres and degrees. `circle` takes a radius, `hole` a diameter.
- One sketch per feature. Cutters are modelled as solid bodies and merged with `combine(..., "remove", ...)`.
- Assemblies: fix one part, pre-position the others with `move`, constrain with `contact` / `coincidence`;
  avoid distances between planes (and give an `orientation` when unavoidable).
- Anything the script cannot express: `script.raw("catia_<tool>", {...})` (still validated).
