# Roadmap

Ordered by value to an agent working unattended. "Live" means it needs a licensed CATIA to be
proven; nothing ships as a registered tool before that.

## Next (0.4)

1. **Drafting: model to 2D drawing.** Create a `CATDrawing`, add views (front, projections, section,
   detail), generate and place dimensions, title block, export PDF. Closed-loop check: read the exported PDF
   back with `drawing_extract_geometry` and compare diameters, hole spacing and overall size with the 3D
   bounding box. Conformance rules (sheet size, preferred scales, title block, line weights, dimensioning, general
   tolerances) will live in a `standards` module and a written design.
2. **Reverse engineering: `catia_describe_model`.** Walk a part (bodies, features, sketches with geometry and
   constraints, parameters, volume, box) and emit a JSON spec plus a replayable `PartScript`. Anything not
   recognised is reported as an explicit black box, never invented.
3. **`catia_audit_model`.** Tree quality: default names, bodies without a result, empty features, over- or
   under-constrained sketches, feature order.
4. **Scale.** `limit` / `offset` on `catia_list_faces` and `catia_list_components`, per-sub-product queries,
   manual update mode with one update per sub-product, checkpointed batches that resume after an interruption.
5. **Undo and transactions.** Roll back the last step of a batch when it fails halfway.
6. **Sketch constraint diagnostics.** Detect over- and under-constrained sketches before extruding.

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
