# Design: drawings from 3D models, model description, audit

Status: **part 1 (drawings) is implemented and proven live**: `catia_mcp/tools/drafting.py` registers the
`catia_drawing_*` tools, `catia_mcp/drawing/verify.py` holds the closed-loop check, and the guide
`docs/DRAFTING.md` lists the experiments and the pitfalls. Sections 1.2 and 5 below were corrected with the
exact names and values read from the type libraries of CATIA V5 R19 (`scripts/dump_typelibs.py`).
**Parts 2 and 3 (model description, audit) are still design only, UNVERIFIED-LIVE**, nothing is registered.
The pure-Python parts (`catia_mcp/standards.py`, `catia_mcp/drawing`) are implemented and tested.

Goal: starting from a 3D model (CATPart or CATProduct), produce

1. a clean, complete CATDrawing that follows the technical drawing standards,
2. a replayable description of the model (tree, sketches, dimensions) to regenerate or audit it,
3. a closed-loop check: read the exported drawing back with `catia_mcp.drawing.extract()` and verify
   that it matches the 3D model (diameters, hole spacing, overall size).

## 0. Ground rules

* One tool = one intent, structured JSON in and out, same error conventions as the existing tools
  (truthful schema, deterministic failure, no silent fallback that changes meaning).
* Everything pure (choosing sheet and scale, layout, checking a spec, comparing measurements) lives
  in Python modules without COM so it can be tested offline. COM code only reads/writes CATIA and
  returns plain data.
* No invention: when the model contains something the code does not understand, it is reported as
  an opaque feature (part 2), never guessed.
* Units: millimetres and degrees at the tool boundary. CATIA works in SI internally for some
  properties (metres, radians); each COM helper converts explicitly and says so.
* Scale convention: `standards.Scale.ratio` is paper/model. `catia_mcp.drawing.geometry.parse_scale`
  returns model/paper. Convert at the call boundary and test the conversion.
* Provenance labels used in the tables: **[repo]** = already used by a tool of this repository that
  was proven live; **[recalled: doc]** = recalled from the CATIA Automation documentation;
  **[recalled: pycatia]** = recalled from the pycatia wrapper; **[recalled: forum]** = recalled from
  community examples; **[unknown]** = no reliable memory. All non-[repo] items: *to be confirmed in
  the DRAFTINGITF type library* (or the library named in the row).

## 1. Drawing generation (`catia_drawing_*`)

### 1.1 Data flow

```
model (CATPart/CATProduct)
   |  catia_get_bounding_box, catia_list_faces/edges, catia_get_parameters   [existing tools]
   v
plan   = drawing_plan(bbox, features, standards)      pure Python (standards.choose_sheet, layout)
   v
spec   = {sheet, scale, projection, views, dimensions, title_block, ...}    JSON, checked by
         standards.validate_drawing_spec(spec)  before touching CATIA
   v
catia_drawing_create -> catia_drawing_add_view (xN) -> catia_drawing_generate_dimensions
   -> catia_drawing_add_dimension (gaps) -> catia_drawing_title_block -> catia_drawing_export_pdf
   v
PDF --> catia_mcp.drawing.extract() --> compare with 3D --> report      (closed loop, 1.8)
```

### 1.2 Automation objects (exact names read from DRAFTINGITF; **live** = proven on R19)

| Object / member | Use | Status |
|---|---|---|
| `Application.Documents.Add("Drawing")` -> `DrawingDocument` | new CATDrawing | **live**; a fresh sheet has no readable format until `PaperSize` is set |
| `DrawingDocument.Sheets` (`DrawingSheets`: `Add`, `AddDetail`, `Item`, `Count`, `ActiveSheet`), `.Standard` (`CatDrawingStandard`: ANSI 0, ISO 1, JIS 2) | sheets; a new drawing is already ISO | **live** (read) |
| `DrawingSheet.PaperSize` (`CatPaperSize`: A0=2, A1=3, A2=4, A3=5, A4=6, ...), `.Orientation` (`CatPaperOrientation`: portrait 0, landscape 1), `.Scale` (paper/model), `.ProjectionMethod` (`CatSheetProjectionMethod`: first 0, third 1), `.GetPaperWidth()/.GetPaperHeight()`, `.PaperName`, `.GenerateDimensions()`, `.Views` | sheet format, scale, projection | **live**; A0..A4 sizes equal ISO 5457 and the exported PDF page |
| `DrawingViews.Add(name)`, `.Remove(name)`, `.Item(i or name)`, `.Count` | create / delete a view; every drawing has `Main View` (type 13) and `Background View` (type 0) | **live** |
| `DrawingView.GenerativeBehavior` -> `DrawingViewGenerativeBehavior`; `.Document = <part/product>.Product` **on every generative view**; `.HiddenLineMode` (`CatHiddenLineMode`: 1 = show hidden edges); `.Update()` | link a view to 3D | **live** |
| `.DefineFrontView(x1,y1,z1, x2,y2,z2)` = horizontal and vertical axes of the view; `.DefineIsometricView` same arguments; `.DefineProjectionView(parentGenerativeBehavior, CatProjViewType)` (right 0, left 1, top 2, bottom 3, rear 4); `.DefineSectionView(profile, "SectionCut", "Offset", side, parentGenerativeBehavior)`; `.DefineCircularDetailView(xCentre, yCentre, radius, parentGenerativeBehavior)` | view kinds. The auxiliary (`DefineAuxiliaryView`), broken, unfolded and clipping views exist but were not exercised | front, projection, section, detail: **live**; isometric: see `docs/DRAFTING.md` |
| `DrawingView.x`, `.y` (centre of the projected bounding box), `.xAxisData`, `.yAxisData` (sheet position of the projected 3D origin), `.Scale`, `.Angle`, `.Name`, `.ViewType` (`CatDrawingViewType`), `.Activate()`, `.SetViewName(prefix, id, suffix)`, `.Size(array)` (byref: VBScript only) | placement | **live** |
| `DrawingView.Factory2D` (`CreateLine`, `CreatePoint`, `CreateClosedCircle(cx, cy, r)`; `CreateCircle` needs 5 arguments) | 2D geometry: frame, centre lines, dimension helpers. **The view must be `Activate()`d first**, else `CreateLine` fails with E_FAIL | **live** |
| `DrawingView.Dimensions` (`DrawingDimensions`: `Add(CatDimType, geomArray, pointArray, CatDimLineRep)`, `Count`, `Item`, `Remove`) -> `DrawingDimension` (`GetValue().Value`, `DimType`, `MoveValue(x, y, 0, 0)`, `GetDimLine().GetGeomInfo` via VBScript, `GetValue().SetPSText(1, prefix, suffix)`) | manual dimensions: only on 2D geometry created with Factory2D (points, lines, circles), never on the generated edges | **live** |
| `Selection.VisProperties.SetRealWidth(index, 1)`, `.SetRealLineType(index, 1)`, `.SetShow(1)` | line width / type / hide | **live**: width index 1 = 0.13 mm, 2 = 0.35, 3 = 0.70; line type 1 continuous, 3 dashed, 4 chain |
| `DrawingView.Tables` (`DrawingTables.Add(x, y, rows, cols, rowHeight, colWidth)` -> `DrawingTable`: `SetCellString`, `SetColumnSize`, `SetRowSize`, `MergeCells(row, col, nRows, nCols)`, `AnchorPoint` 2 = bottom-left, `.x/.y`) | title block | **live** |
| `DrawingView.Texts` (`DrawingTexts.Add(text, x, y)`), `DrawingText.SetFontSize(0, 0, size)`, `.AnchorPosition` | notes | **live** (Add, SetFontSize, AnchorPosition) |
| `DrawingDocument.ExportData(path, "pdf")` | PDF export | **live**: vector PDF, page = sheet |
| `DrawingDocument.SaveAs(path)` | save the CATDrawing | not exercised (same quirks as the other documents) |
| `Selection.Search("CATDrwSearch.*,all")` | only user 2D geometry, views, texts: the generated edges cannot be enumerated | **live** (negative result) |

Answers to the "known unknowns": the 3D source is fed as `<document>.Product` (works for a CATPart); the six
arguments of `DefineFrontView` are two axis vectors; automatic dimension generation is scriptable
(`DrawingSheet.GenerateDimensions`) but only creates the dimensions driven by 3D constraints; the frame and the
title block are drawn (Factory2D + Tables), the resource route (standard frame) was not tried.

### 1.3 Tools

All tools take `drawing` (document name, default: active document, like the other tools) and return
`{ok, ..., warnings[]}`. All raise before touching CATIA when `standards.validate_drawing_spec`
reports an `error` on the requested spec (the report is returned in the error).

#### `catia_drawing_create`

Input: `source` (name of the open CATPart/CATProduct), `sheet` (`"A3"` or `"auto"`), `scale`
(`"1:2"` or `"auto"`), `projection` (`"first_angle"` default in Europe, `"third_angle"`),
`standard` (`"ISO"`), `n_views` (for `auto`), `margins`.

Algorithm:
1. Read the bounding box of the source (existing `catia_get_bounding_box`).
2. `auto`: `standards.choose_sheet(...)` on the largest face of the bounding box for the chosen
   views (front, top, right: the front view fixes two dimensions, the others share them). Result:
   sheet name, preferred scale, layout cells.
3. Create the document, set paper size and orientation (A0..A3 landscape, A4 portrait, ISO 5457:1999 4.1),
   sheet scale, projection method, drawing standard.
4. Return the plan (`sheet`, `scale`, `frame` from `standards.frame_rect`, `drawing_space`,
   `cells` where each view centre goes), which the following tools use.

Failure modes: source not open; source empty (no bounding box); nothing fits (raise with the best
attempt); property names differ in R19 (caught in experiment set 2).

#### `catia_drawing_add_view`

Input: `kind` in `front | top | bottom | left | right | rear | section | detail | isometric | auxiliary`,
`face` (for `front`: the principal direction chosen by the caller, e.g. `"-Y"`, or `"auto"` = the
face with the largest projected area), `reference_view` (for projections), `position` (paper mm, or
`"auto"` = the plan cell), `scale` (default sheet scale; if different it is annotated, ISO 5455:1979 4.2),
`hidden_lines` (bool), `centre_lines` (bool), and per kind:
* `section`: `cutting_plane` = `{through: [x,y,z] or view point, direction}` or two points in the parent view;
  produces a section arrow pair, a label, and hatching from the drawing standard (ISO 128-3:2020).
* `detail`: `centre`, `radius` (paper mm in the parent view), `scale` (enlargement, e.g. `"5:1"`),
  `label` (capital letter).

Placement rules: first angle: top below the front view, left view to its right, right view to its
left, bottom above (ISO 5456-2:1996 5.1); third angle mirrored (`standards.expected_view_side`).
Projected views are aligned on the front view (`view_alignment` check). The tool never places a view
outside the frame or over the title block (`standards.frame_rect`, `title_block_rect`) and refuses
overlaps.

Returns: view id, name, paper bounding box, scale, the 3D-to-paper transform (origin and axes in the
view, needed by the loop closure 1.8).

#### `catia_drawing_generate_dimensions`

Goal: dimension what the 3D model defines, once (ISO 129-1:2018 4.1.1).

Strategy (in order of preference, chosen after experiment 9):
1. If automatic generation is scriptable: call it per view, then read the result back
   (`DrawingView.Dimensions`), then run the redundancy filter below.
2. Otherwise **derive the dimensions from the model** and create them one by one with
   `catia_drawing_add_dimension`: overall size (three extents, each in the view that shows it best),
   every hole/boss diameter (once, in the view where the circle is round; `n x D` for identical
   features), positions of hole centres from a datum (two orthogonal ordinates, or one chain), radii of
   fillets, thread designation from `standards.thread_designation`, chamfers.
   The model provides the values (feature parameters when a hole/pad/pocket feature is recognised
   as in part 2, edge/face measures otherwise).

Redundancy filter (pure Python, tested): keep a dimension only if no other dimension already fixes
the same quantity in another view; a repeat becomes an auxiliary dimension `(value)` or is dropped.
Chains that close a loop (overall size + all partial sizes) drop one link (the most informative
overall stays).

Returns: list of `{id, view, type, feature, value, unit, position}` plus the spec fragment, so the
result can be run through `validate_drawing_spec`.

#### `catia_drawing_add_dimension`

Input: `view`, `type` (`linear | diameter | radius | angular`), `from`/`to` (points in view paper mm, or
element references chosen from the view's geometry), `orientation` (`horizontal | vertical | parallel`),
`value_override` (rare; must be underlined if it disagrees with the scale, ISO 129-1:2018 4.1.3),
`tolerance` (`None` -> general tolerance applies), `prefix` (`"2x"`), `thread` (validated by
`thread_designation`), `auxiliary` (bool, printed in parentheses), `position` (dimension line offset).

Checks before the call: view exists; the dimension would be inside the frame; not on a hidden line
(warn); not already present for the same feature (error, ISO 129-1:2018 4.1.1). After the call: read the
value back with `GetValue` and compare with the model value (tolerance 0.01 mm); mismatch = failure.

#### `catia_drawing_title_block`

Input: the ISO 7200:2004 fields by key (`standards.TITLE_BLOCK_FIELDS`), `general_tolerance` ("m"),
`scale`, `projection`, `sheet` (auto), `unit`. Validation: every mandatory field present, lengths
within the recommendation, `sheet_number <= number_of_sheets`.

Two implementation routes, chosen by experiment 10:
* **Resource route**: insert the drawing standard's frame and title block from CATIA resources, then
  fill its text fields (fields vary by installation: fragile).
* **Drawn route (preferred, deterministic)**: in the sheet background view draw the frame (0.7 mm
  continuous, 20 mm left / 10 mm elsewhere), a 180 mm wide table (`standards.title_block_rect`), and
  the field texts with `Texts.Add`. The block is fully known to the code, so it is checkable.
The scale, projection symbol and general tolerance are written near the block as data fields (ISO 7200:2004 clause 4).

#### `catia_drawing_export_pdf`

Input: `path`, `sheet` (default active), `check` (bool, default true).
Runs `ExportData(path, "pdf")`, verifies the file exists and is non-empty, that the PDF has vector
content and the page size equals the sheet (mm, tolerance 0.5). If `check`, runs the closed loop (1.8).
Returns path, page size, entity counts from `extract()`.

### 1.4 Line conventions to apply

Line group 0.5 or 0.7 (preferred): wide lines (visible outline 01.2, cutting-plane ends) at the group
width, narrow lines (hidden 02.1, centre 04.1, dimension, extension, hatching, leader) at half
(`standards.line_pair_for_group`). `standards.LINE_USAGE` gives the type per usage. Line widths and
types of the drawing standard installed with CATIA may not follow ISO 128-2:2020: read them back
(`DrawingView` element graphic properties, [unknown] member names) and report deviations rather than
silently changing the standard file.

### 1.5 Completeness checklist of the produced drawing

Sheet in ISO-A series with frame and title block; scale from the recommended series; projection symbol;
principal view chosen for information content (ISO 5456-2:1996 4.2); views limited to the necessary
ones; sections/details where hidden or small features make dimensioning unclear (ISO 5455:1979 5.3);
centre lines on holes and axes; every feature dimensioned once; thread callouts; general tolerance
note (`ISO 2768-m` default); mass or material in the title block only if provided (not invented).

### 1.6 What is not attempted (v1)

Surface texture symbols, geometrical tolerances, weld symbols, bill of materials and balloons for
products, exploded views, multi-sheet drawings beyond sheet numbering, non-metric units.

### 1.7 Products

For a CATProduct the same tools apply with `source` = the product. The bill of materials and item
balloons are out of scope for v1; each part still gets its own drawing (`catia_drawing_create` per part).

### 1.8 Closed-loop verification (`catia_drawing_check`, pure Python + 3D reads)

Purpose: prove that the drawing says what the 3D model says.

1. **Expected data from the 3D side** (per view): projected circles (diameter, centre in the view
   plane), overall extents of the projected outline, distances between hole centres. Sources: feature
   parameters (part 2) when recognised, otherwise edges of the B-Rep (`catia_list_edges` /
   `catia_list_faces`) and `catia_get_bounding_box`. Everything expressed in the model frame of the view.
2. **Measured data from the drawing**: `catia_mcp.drawing.extract(pdf, page, region, scale, origin)`
   per view. `region` = the view's paper box (from `catia_drawing_add_view`, converted to the PDF frame:
   PDF paper mm is top-left origin, y down; CATIA sheet coordinates are bottom-left, y up:
   `y_pdf = sheet_height - y_catia`). `origin` = the view's model origin in paper mm, so
   extract's `cx, cy, r` come out directly in model mm. `scale` = the view's scale passed **explicitly**
   from the spec as model/paper (never read from the title block: text in the PDF may be strokes, and
   `extract` warns it defaults to 1:1).
3. **Comparison** (`compare_drawing(expected, measured, tol)`):
   * every expected circle has a measured circle with `|d_meas - d_exp| <= max(0.02 mm, 0.5 %)` and
     centre distance <= `max(0.05 mm, 0.5 % of the view size)`; unmatched on either side is reported;
   * distances between matched centres (entraxes) agree within the same tolerance;
   * the extents of the visible outline agree with the projected bounding box;
   * the scale check: the extracted overall size divided by the 3D size equals the declared ratio.
   Matching is nearest-neighbour on centre distance, then a one-to-one assignment; no fuzzy diameter matching.
4. **Report**: pass/fail per check with expected vs measured values and the drawing's `Issue` list from
   `validate_drawing_spec`; it can also render the overlay (`catia_mcp.drawing.overlay`) of expected circles
   on the PDF for a visual check.

Limits, stated up front: `extract` returns circles, arcs and line segments, not dash styles or
dimension values (dimension text may be strokes). Hidden lines are therefore mixed with visible ones, and
dimension values are checked through the CATIA side (read back with `GetValue`) rather than from the PDF.
Overlapping views or views with a section hatch need a `region` that isolates them. Threads are drawn as
partial circles; compare the major/minor diameters with a looser tolerance.

## 2. Reverse engineering: `catia_describe_model`

### 2.1 Purpose and output

Walk a CATPart and return:
* `spec` (JSON, format `catia-mcp-model-spec/1`): everything recognised, in the order of the tree,
  with parameter values and references between features by name;
* `script`: a replayable PartScript (the idea of `catia_mcp.scripting`: a list of `{tool, args}` steps that
  a batch runner executes with the existing tools and checks). The interface is described here without
  importing that package; it will be aligned when it settles. Only recognised features become steps;
* `opaque`: features that could not be translated, each with its name, type string and the reason;
* `checks`: what was measured on the original (volume, bounding box, inertia) so the replay can be compared.

### 2.2 Spec format (sketch)

```json
{
  "format": "catia-mcp-model-spec/1",
  "part": {"name": "...", "units": "mm"},
  "parameters": [{"name": "L", "value": 40.0, "unit": "mm", "formula": null}],
  "bodies": [{
    "name": "PartBody", "order": 0, "visible": true,
    "features": [
      {"name": "Pad.1", "kind": "pad", "sketch": "Sketch.1", "limit": {"mode": "dimension", "length": 12.0},
       "symmetric": false, "recognised": true},
      {"name": "EdgeFillet.3", "kind": "fillet", "radius": 2.0, "edges": "opaque-selection",
       "recognised": false, "reason": "edge selection cannot be reproduced by name"}
    ]}],
  "sketches": [{
    "name": "Sketch.1", "support": {"plane": "XY", "offset": 0.0}, "origin": [0, 0], "axes": "default",
    "geometry": [
      {"id": 1, "type": "line", "start": [0, 0], "end": [40, 0], "construction": false},
      {"id": 2, "type": "circle", "center": [20, 10], "radius": 4.0, "construction": false},
      {"id": 3, "type": "arc", "center": [0, 0], "radius": 5, "start": [5, 0], "end": [0, 5], "direction": "ccw"}],
    "constraints": [
      {"type": "distance", "elements": [1], "value": 40.0, "status": "driving"},
      {"type": "radius", "elements": [2], "value": 4.0}],
    "dof": {"state": "iso-constrained"}
  }],
  "checks": {"volume_mm3": 0.0, "bbox": [0, 0, 0, 40, 20, 12]},
  "opaque": [{"name": "...", "type": "...", "reason": "..."}]
}
```

### 2.3 What is walked and how (Automation, all [recalled] unless noted)

| Step | COM route | Provenance |
|---|---|---|
| bodies | `Part.Bodies`, `Part.MainBody`, `Body.Name` | [repo] (`catia_list_bodies`) |
| features in order | `Body.Shapes` (`Shapes.Item(i)`), `Part.HybridBodies` for GSD sets | [repo] for listing (`catia_list_features`, `catia_get_tree`) |
| feature type | the object's COM class name via `TypeName`-like property or `Name` prefix | the tree tools already classify; the exact type discriminator [repo] to reuse |
| pad/pocket parameters | `Pad.FirstLimit.Dimension.Value`, `.SecondLimit`, `.IsSymmetric`, `Pocket.FirstLimit.LimitMode` | [repo] (`part_design.py`) |
| shaft/groove, hole, fillet, chamfer, shell, thickness, draft, patterns, mirror | corresponding properties (radius, length, angle, counts, spacing) | [recalled: doc]; some are used by the creating tools [repo], reading back is [unknown] |
| feature -> sketch link | `Pad.Sketch` (or the profile element) | [recalled: doc] |
| sketch support and axes | `Sketch.Support`, `Sketch.SetAbsoluteAxisData` / `GetAbsoluteAxisData` | [recalled: doc] |
| sketch geometry | `Sketch.GeometricElements` items `Line2D` (`StartPoint`, `EndPoint`), `Circle2D` (`CenterPoint`, `Radius`, start/end angles for arcs), `Point2D.GetCoordinates` | [repo] lists names and `GeometricType` only (`sketch_get_geometry`); coordinates [recalled: doc] |
| sketch constraints | `Sketch.Constraints` items: `Type`, `Mode` (driving/reference), `Dimension.Value`, `GetElements`/first and second references | [repo] for creation (`AddBiEltCst`); reading `Type` enum values [unknown] until dumped (`catCstType*` in the MecMod library) |
| degrees of freedom | `Sketch` analysis members ("iso-constrained", "over-constrained") | [unknown] |
| parameters | `Part.Parameters`, `Parameter.Name/Value`, relations/formulas `Part.Relations` | [repo] `catia_get_parameters`; formulas [recalled: doc] (KnowledgewareTypeLib) |
| volume, bbox, inertia | `SPAWorkbench` (`Inertias`, `Measurable`) | [repo] (`catia_get_inertia`, `catia_get_bounding_box`) |

### 2.4 Rules

* A feature is `recognised` only if its kind, all needed parameters and its sketch could be read, and
  its replay uses an existing tool. Otherwise it goes to `opaque` with the reason; the walk continues.
* Selections by geometry (fillet edges, shell faces, pattern seeds) are reproduced only when a stable
  reference can be described (named face/edge, or a described position). Otherwise opaque. The replay
  never uses face/edge indices from the original as if they were stable across sessions.
* Sketch geometry is emitted in the sketch's own axes, with units and the `construction` flag. Constraints
  that reference geometry use the spec's geometry ids, not CATIA names.
* Numerical formulas are kept as text plus the evaluated value; the replay uses values by default.
* `--replay-check`: build the script, run it in a new part (dry-run first), then compare volume,
  bounding box and inertia with `checks` (tolerance 1e-3 relative). A mismatch marks the spec as
  `replay_verified: false` with the deltas. A spec with opaque features is never presented as complete.
* No data leaves the machine; the spec contains only geometry and parameter names.

### 2.5 Honest limits

Features of other workbenches (GSD surfaces beyond the basics, Wireframe, sheet metal, kinematics),
knowledge-driven features, power copies, user features, and imported (non-parametric) bodies are opaque.
Sketch constraint analysis may differ from CATIA's own solver state. Order of features that were
reordered or defined "in" another body may be lost. Tessellated geometry cannot be regenerated.
The output is an approximation of design intent: an audit and regeneration aid, not a certified
round trip.

## 3. Model audit: `catia_audit_model`

Read-only. Returns `{issues[], stats}`; each issue has `severity` (`error|warning`), `code`, `where`,
`message`, and a suggested fix. Checks:

| Code | Check | Severity | Data |
|---|---|---|---|
| `tree.default_name` | features, sketches, bodies with default names (`Pad.1`, `Sketch.3`, `Body.2`, ...) | warning | tree walk |
| `tree.body_without_result` | a body with no feature producing a solid (empty or only sketches) | error | body shapes |
| `tree.empty_feature` | a feature with no volume change or a failed update | error | update status, volume delta [unknown] |
| `sketch.unconstrained` | sketch with degrees of freedom left | warning | analysis [unknown]; fallback: count constraints vs geometry |
| `sketch.over_constrained` | over- or inconsistently constrained sketch | error | analysis [unknown] |
| `sketch.not_closed` | profile used by a pad/pocket is open | error | geometry read (2.3) |
| `sketch.unnamed_dimension` | driving dimensions with no name/parameter when the part has parameters | warning | constraints |
| `tree.order` | dress-up features (fillet, chamfer, shell, draft) before the features they depend on, or sketches on faces produced late (fragile references) | warning | order + references |
| `tree.hidden_body` / `tree.unused_geometry` | hidden bodies, unused sketches, orphan construction geometry | warning | visibility, dependencies |
| `param.unused` | parameters not referenced | warning | parameters and relations |
| `model.units` | non-mm document units | warning | settings |

The audit reuses the same tree walk as `catia_describe_model`; both call one shared reader (pure
data out) so their results agree. Each check is a pure function on that data, tested with hand-written
fixtures, without CATIA. Anything the reader cannot obtain is reported as `unknown`, never as passed.

## 4. Code layout (future)

* `catia_mcp/standards.py` (done) and `catia_mcp/drawing` (done): pure.
* `catia_mcp/drawing_plan.py` (new, pure): layout, dimension derivation from the expected data,
  redundancy filter, comparison `compare_drawing`.
* `catia_mcp/model_reader.py` (new, COM, thin): tree, sketches, parameters -> plain dicts.
* `catia_mcp/model_spec.py` (new, pure): spec building, opaque handling, script emission, audit rules.
* `catia_mcp/tools/drawing_cad.py` (new, COM): the `catia_drawing_*` tools. Name distinct from the existing
  `tools/drawing.py` (PDF reader) to avoid confusion.
* Register in `server.py` only after part 5 passes. Add lessons to `data/lessons.json` for every pitfall found.

## 5. Live validation plan (needs CATIA; not before)

Rules for every experiment: one CATIA call at a time, explicit document context, record the exact error
text, save the outputs under the state folder (not in the repository). Create test data from scratch
(a simple plate with two holes and a pocket, then a shaft with a thread), never from confidential files.
Success criteria are numeric wherever possible.

1. **Dump the type libraries.** `python scripts/dump_typelibs.py` (registry mode), then `--mode live`.
   Success: files exist for `DRAFTINGITF`, `KnowledgewareTypeLib`, `CATMat`, plus the Part/Product/MecMod
   libraries; `INDEX.txt` lists them; `rg "interface DrawingViewGenerativeBehavior"` returns the method list
   with parameter names. The script must not have modified any document (the `live` mode closes its temporary
   documents). Fix the script from what fails. Then **re-read sections 1.2 and 2.3 and correct every
   [recalled] row with the dump**; add the verified signatures to `docs/LESSONS.md`.
2. **Create a drawing.** `Documents.Add("Drawing")`; read `Sheets.Count`, `ActiveSheet.PaperSize`, `.Orientation`,
   `.Scale`. Success: values readable; set A3 landscape and scale 1:2, read them back equal.
3. **Sheet properties.** Set A4 (portrait), A0; find how the projection method is set (sheet or document) and read
   it back. Success: first and third angle switch is readable back.
4. **Front view from a model.** Open the test plate, create a front view with the correct `DefineFrontView`
   arguments (from the dump). Success: the view shows the plate; read `X`, `Y`, `Scale`; the view's projected extents
   equal the 3D bounding box extents times the scale within 0.05 mm (measured after export in experiment 8).
5. **Projected views.** Add top and right views in first angle; then switch to third angle. Success: views land
   on the side `standards.expected_view_side` predicts (compare view centres) and are aligned within 1 mm.
6. **Section and detail.** Section through a hole axis with the cutting-plane definition; detail of a small
   feature at 5:1. Success: hatch present, labels A-A / A present, detail scale annotated; the section view's
   circle diameters are unchanged.
7. **Manual dimension.** `Dimensions.Add` a diameter and a linear dimension; read back `GetValue`. Success: value
   equals the 3D value to 0.01 mm; the dimension appears in the PDF at the expected place.
8. **PDF export and closed loop.** `ExportData(path, "pdf")` on the drawing; if it fails, record the error and test
   `SaveAs` with `.pdf` and printing to a PDF printer as alternatives. Then `catia_mcp.drawing.extract()`
   on each view region with the explicit scale and origin. Success: page size equals the sheet within 0.5 mm; each hole
   circle found with `|d - d_3D| <= 0.02 mm`; centre distances within 0.05 mm; overall width/height within 0.05 mm.
   Check the PDF coordinate frame (y flip, offsets) by locating the sheet frame rectangle.
9. **Automatic dimension generation.** Look in the dump for a generation method; if none, try the
   `Generate Dimensions` command through `StartCommand` on a view. Success: dimensions appear and can be listed;
   otherwise record "not scriptable" and use the derived strategy (1.3). Measure redundancy: count of duplicate features.
10. **Title block.** Try both routes: resource frame and title block (fill fields), and the drawn route (frame with
   `Factory2D`, table, texts). Success: every ISO 7200:2004 mandatory field visible in the export;
   `validate_drawing_spec` reports no error on the spec that produced it; the frame is 20/10 mm from the sheet edge
   (measure in the PDF).
11. **Line properties.** Read the graphic properties of visible, hidden and centre lines in the produced views (thickness,
   type). Success: widths follow the chosen group (0.5/0.25 or 0.7/0.35) or the deviation is quantified.
12. **Sketch reading.** In the test plate sketch: read line, circle and arc coordinates and the constraints (type, value, mode)
   via `GeometricElements` and `Constraints`. Success: values equal the ones used to create it; arcs' orientation is right.
13. **Feature reading.** For pad, pocket, hole, fillet, chamfer, shaft: read the parameters that `catia_describe_model` needs.
   Success: each equals the creation value; unknown features return a type string that lands in `opaque`.
14. **Replay round trip.** `describe_model` -> script -> replay in a new part -> compare volume (1e-3 rel.), bounding box,
   inertia. Success: identical within tolerance for the test parts; a part with a deliberately unsupported feature yields
   an `opaque` entry and `replay_verified: false`, never a silent gap.
15. **Audit.** Build a part with default names, an unconstrained sketch, an over-constrained one, an empty body and a fillet
   before its pad; run `catia_audit_model`. Success: each defect is reported once with the right code; a clean part gives none;
   unreadable properties are reported `unknown`.
16. **Failure handling.** Run the drafting tools with: no open source, closed document, read-only file, a view that
   does not fit, a dialog popping up. Success: deterministic error message, no half-created drawing left open, session still usable.
17. **Timing and stability.** Full pipeline on the plate: time each step; run five times in a row. Success: no COM
   leak (document count stable), identical outputs.

Exit criteria for registering the tools: experiments 1-8, 12-14 pass; 9-11 pass or are documented as fallbacks; every discovered
pitfall is in `lessons.json`; `tests/test_standards.py` and a new offline test-suite for the pure modules pass.
