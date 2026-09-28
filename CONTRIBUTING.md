# Contributing

Bug reports, fixes, new tools, lessons and documentation are welcome.

## The one rule: prove it

CATIA's COM layer is full of surprises (a method that exists but returns zeros, an argument order
that differs from the docs, an enum whose value is not what the name suggests). Almost every bug
this project has had came from a guessed signature. So:

- **Never guess a signature or enum value.** Dump the type library (the COM type
  libraries can be enumerated from Python with pywin32) and read the exact parameter names.
- **Say how you tested it**: your CATIA release, the scenario, the result. Mark code you could not run
  against CATIA with `# UNVERIFIED-LIVE` and keep it out of the registered tools until someone has.
- **Every pitfall you find becomes a lesson.** Add it to `catia_mcp/data/lessons.json` with the
  observed error text as `error_patterns` and how you proved the cause, then regenerate the doc with
  `python scripts/gen_lessons_doc.py`. Lessons are how the next agent avoids your mistake.

## Development

```bash
pip install -e ".[dev]"
ruff check catia_mcp tests
pytest                      # offline: never starts CATIA (CATIA_MCP_OFFLINE=1 is set by the tests)
```

Live checks that need a licensed CATIA are listed in `docs/LIVE_VALIDATION.md`.

## Adding a tool

- A tool module is a class with `get_tool_definitions()` (name, description, `inputSchema`) and
  `execute(tool_name, arguments)`; register it in `server.py` and in `module_groups` (tool sets).
- Descriptions are read by a model that may be weak at 3D: say what the tool does, what it returns,
  the units (mm, degrees), and the pitfall that matters.
- Creation tools get the optional `name` argument automatically (`naming.py`); check the tool name is
  recognised by `naming.is_creation_tool`.
- Prefer designating geometry by a 3D point on it over names.
- Report what happened with a number (volume change, count, distance), so the agent can verify without a second call.
- Add offline tests for schemas, validation and any pure logic.

## Pull requests

Small and focused, one topic each. Describe what and how you tested. No project-specific files
(personal scripts, part generators, drawings): keep them in your fork.

## Licensing

By contributing you agree your work is released under the MIT licence. Do not contribute anything
that bypasses, emulates or circumvents CATIA licensing, or third-party drawings and CAD models you
have no right to publish. The optional drawing tools use PyMuPDF (AGPL-3.0) as a separate, lazily
imported dependency; keep it optional.
