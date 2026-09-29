# Roadmap

Ordered by value to an agent working unattended. "Live" means it needs a licensed CATIA to be
proven; nothing ships as a registered tool before that.

## Done in 0.4

Drafting (model to 2D drawing with a closed-loop check against the model), reverse engineering
(`catia_describe_model`, replay, `catia_measure_model`), `catia_audit_model`, paging and persistent caching for
scale, safety tiers. See the changelog.

## Next (0.5)

1. **Sketch constraints as data.** Address sketch elements by stable ids instead of indexes so that
   constraints can be replayed by the reverse-engineering script and diagnosed (over- and under-constrained).
2. **Assembly editing.** Delete or disable a constraint, absolute component placement, hide or show a
   component, keep the camera between calls. Missing today; an animation had to be built around them.
3. **Undo and transactions.** Roll back the last step of a batch when it fails halfway.
4. **Checkpointed batches.** Resume a long scenario after an interruption (runner `--start-at`).
5. **Materials and mass properties**: apply a material, report mass and inertia with real density.
6. **Audit without side effects**: reading features must not mark them for update.

## Then

- **Parameters and knowledge**: create parameters and formulas, design tables, user-defined relations.
- **Materials and mass properties**: apply a material, report mass and inertia with real density.
- **Bill of materials** and publications for assemblies.
- **More features**: rib, slot, stiffener, variable-radius fillet, solid loft, user pattern.
- **Wider generative shape design**: about 24 of the 128 `HybridShapeFactory.AddNew*` methods are covered.
- **Selection and visibility helpers**: search by attribute, show / hide sets.

## Protocol and quality

- **Structured output** (`outputSchema` / `structuredContent`) for the measurement and listing tools, so agents
  read numbers without parsing text.
- **Confirmation before destructive calls** through MCP elicitation, for clients that support it.
- **Progress notifications** for long batches.
- **Call journal export**: every session as a replayable script, so an interactive exploration becomes a scenario.
- **Specification assertions** in a scenario ("volume within 0.1 % of X", "bounding box equals Y") that fail the
  batch when the model drifts from the drawing.
- **Designation cache**: remember which face or edge a 3D point resolved to. Designation is the slowest thing
  the server does on dense parts (about 50 ms per distance measurement).
- **Mock COM adapter** for contract tests of the tool logic without CATIA.
- Publication: PyPI trusted publishing, MCP registry `server.json`.

## Not planned

- Anything that bypasses, emulates or works around CATIA licensing.
- Multi-user or network operation: the server is a local stdio bridge.
