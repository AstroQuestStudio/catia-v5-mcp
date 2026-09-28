# catia-v5-mcp

**An MCP server that lets AI agents drive CATIA V5 through COM, and get it right the first time.**

Most CAD automation gives a model a bag of tools and hopes. This one is built around what actually
goes wrong when an agent models real parts for hours: silent no-ops, default feature names, a
constraint that says OK while the part flipped, a dialog that freezes every call, the same
mistake made again in the next session. Each of those has a mechanism here.

> CATIA is a registered trademark of Dassault Systèmes. This project is independent and is not
> affiliated with, endorsed by or sponsored by Dassault Systèmes. You need your own licensed CATIA V5;
> nothing here bypasses or emulates licensing. Validated on CATIA V5 R19 (French UI) on Windows 11.

## What makes it different

| Problem | Mechanism |
|---|---|
| 40 tool calls = 40 model round trips, and a typo in call 31 is found after 30 modifications | **`catia_batch`** validates every step against the tool schemas *before* touching CATIA, then runs them with stop-on-error and a compact timed report. `dry_run` validates only. |
| The same COM pitfall bites every new session | **Lessons ledger**: 124 verified pitfalls ([docs/LESSONS.md](docs/LESSONS.md)). The critical ones ship as server instructions, the matching lesson is appended to the error that triggered it, and agents record new discoveries with `catia_add_lesson`; they are merged at the next start. The server gets smarter with use. |
| "It ran" does not mean "it is right" | Every solid feature reports its **volume change**; geometry is designated by **3D points on it** (never guessed names); `catia_get_tree` audits names; sketches can be overlaid on the source drawing. |
| Default names (`Pad.1`, `Extrusion.3`) make a tree unreadable | Every creation tool takes `name`; the server renames the new object, **reads the name back**, hides consumed sketches, and warns when a default is left. |
| A modal dialog silently freezes CATIA and every queued call | **Popup watchdog** closes single-OK information boxes, logs them, and never answers a question. Optional cross-process lock and hang guard. |
| 100+ tool schemas overwhelm a smaller model | **Tool sets** (`CATIA_MCP_TOOLSETS=part`, `assembly`, ...) advertise only what the task needs; hidden tools still work. **Prompts** carry the proven workflow so a model that is weak at 3D follows the right sequence. |
| Assemblies that read "all constraints OK" while a part flipped | Pose-tracking assembly kit: pre-position, constrain on real faces, **verify final poses**, explain every clash. |
| Turning a drawing into a model by eye | **Drawing tools** (optional) extract exact circles, arcs and lines from vector PDFs (radii to a few hundredths of a mm) and overlay a sketch on the drawing to prove it matches. |

## Install

Windows with a licensed CATIA V5, Python 3.10+.

```bash
git clone https://github.com/AstroQuestStudio/catia-v5-mcp.git
cd catia-v5-mcp
pip install -e .              # add ".[drawing]" for the PDF drawing tools
```

Claude Code:

```bash
claude mcp add catia-v5 -- python -m catia_mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{ "mcpServers": { "catia-v5": { "command": "python", "args": ["-m", "catia_mcp"] } } }
```

CATIA must be registered as a COM server once (`cnext.exe /regserver`, as administrator). The
server attaches to a running CATIA or starts one.

## Use

Ask for what you want; the server's instructions and prompts carry the method. For repeatable
work, write a scenario instead of improvising:

```bash
catia-mcp-run scenario.json --dry-run     # validate every step against the schemas, touch nothing
catia-mcp-run scenario.json               # run, log, non-zero exit code on failure
```

`templates/` holds fill-in-the-blank part and assembly scripts (`# FILL:` markers) built on
`catia_mcp.scripting`, which emits validated steps and tracks component poses.

Prompts (one-click workflows): `model_part_from_drawing`, `assemble_product`, `large_assembly`,
`audit_model`, `reverse_engineer_part` (experimental).

## Tools

Over 100 tools in groups (`catia_*`, plus `drawing_*`):

- **Documents**: new part / product, open, save, close, list.
- **Sketcher**: lines, arcs, circles, rectangles, profiles, splines, constraints, geometry read-back.
- **Part Design**: pad, pocket, shaft, groove, hole, fillet, chamfer, thread, shell, draft, thickness,
  patterns, mirror; **bodies** and **boolean operations** for the multi-body method.
- **Generative Shape Design**: wireframe, surfaces (sweep, loft, fill, blend...), solids from surfaces.
- **Assembly**: components, sub-assemblies, fix / coincidence / contact / offset / angle constraints,
  move, clash analysis, clean display, save all.
- **Measure**: distance, inertia, bounding box, parameters.
- **Export and view**: STEP, IGES, STL, screenshots, standard views.
- **Server**: `catia_batch`, `catia_lessons`, `catia_add_lesson`.

Tools carry read-only / destructive / idempotent annotations so clients can auto-approve harmless
calls.

## Configuration (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `CATIA_MCP_TOOLSETS` | `full` | Advertised groups or presets: `part`, `assembly`, `surface`, `review`, or a list of groups |
| `CATIA_MCP_WATCHDOG` | `1` | Popup watchdog |
| `CATIA_MCP_LOCK` | `0` | Cross-process lock so several agents queue instead of interleaving |
| `CATIA_MCP_HANG_SECONDS` | `0` | Log a call that blocks longer than this; `CATIA_MCP_HANG_KILL=1` also kills CATIA |
| `CATIA_MCP_AUTOTRACE` | `0` | CSV line + screenshot after every feature tool |
| `CATIA_MCP_HOME` | `%APPDATA%\catia-mcp` | State: log, lessons, lock, trace |
| `CATIA_MCP_OFFLINE` | unset | Never attach to or start CATIA (tests, CI, unlicensed machines) |

## Working at scale

Aircraft and vehicles are thousands of parts. The tooling is designed for structure first: one
sub-product per system, build and check each alone, save after each, batch in chunks with
stop-on-error, resume from a progress file, query one sub-product at a time. See the
`large_assembly` prompt and [docs/GUIDE_ASSEMBLY.md](docs/GUIDE_ASSEMBLY.md). Scale validation is
tracked in [docs/LIVE_VALIDATION.md](docs/LIVE_VALIDATION.md); pagination of the large listings is on the roadmap.

## Status and honesty

- Live-validated on CATIA V5 R19. Later releases should work, but the COM surface varies: report
  what you see.
- Parts marked `UNVERIFIED-LIVE` in the source are not yet proven against a real session.
- Known gaps: drafting (2D drawings), formulas and design tables, materials, rib / slot,
  undo. See [docs/ROADMAP.md](docs/ROADMAP.md) (drawings and reverse engineering are the next milestone).
- The optional drawing tools use PyMuPDF, which is AGPL-3.0. It is a separate, lazily imported
  dependency and is never required by the server.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md); the rule is *prove it*. Found a pitfall? Add a lesson.
Security: [SECURITY.md](SECURITY.md).

## Licence

MIT, see [LICENSE](LICENSE).
