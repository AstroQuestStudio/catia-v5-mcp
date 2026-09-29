# Drafting: from a 3D model to an ISO drawing, checked against the model

Proven on CATIA V5 R19 (French UI), Windows 11. Everything registered as a tool was run live; what could not
be proven is listed at the end as **UNVERIFIED-LIVE** and is not exposed. The design notes and the object
table are in `docs/DESIGN_DRAFTING_AND_REVERSE.md`, the standards in `docs/ISO_STANDARDS.md`
(`catia_mcp/standards.py`).

## Workflow (what a client should do)

```
catia_open_document / catia_new_part ... the 3D model, saved
catia_drawing_create        sheet auto|A0..A4, scale auto|1:2..., projection, frame        -> plan
catia_drawing_add_view      front first, then top / right / ... (position 'auto' follows the plan)
                            optional: section, detail, isometric
catia_drawing_add_centerlines, catia_drawing_add_dimension, catia_drawing_generate_dimensions
catia_drawing_title_block   ISO 7200 fields (8 mandatory), general tolerance, projection symbol
catia_drawing_export_pdf    vector PDF, page = sheet
catia_drawing_check         CLOSED LOOP: PDF read back and compared with the 3D model
catia_drawing_close         closes the drawing only
```

`tests/live/drafting.json` replays it on a flange (6 tools of the kit, then the drawing and the check;
`python -m catia_mcp.runner tests/live/drafting.json --lock`; the last step fails the run when the drawing
does not match the model). The same check can be run from Python:

```python
from catia_mcp.drawing import verify as V
frame = V.ViewFrame(x, y, scale, (xAxisData, yAxisData), sheet_height)       # what CATIA reports for the view
V.check_view_pdf("drawing.pdf", frame, expected_circles=[{"cx": 0, "cy": 0, "d": 30}], expected_box=(-40, -40, 40, 40))
```

## Conventions

* Millimetres. `position` = centre of the view on the sheet, origin bottom-left, y up (CATIA sheet frame).
  The PDF frame is top-left, y down: `y_pdf = sheet_height - y_sheet` (done by `ViewFrame`).
* Scale: `Sheet.Scale` and `View.Scale` are **paper/model** (`0.5` = 1:2). `catia_mcp.drawing.extract` takes
  model/paper. `ViewFrame.k()` converts. Always pass the scale to `extract` explicitly.
* View coordinates `(u, w)`: model millimetres, origin on the projection of the model origin, u right, w up.
  With the viewer on `-Y` for the front view (`view_from='-Y'`, CATIA's usual front): front u=X w=Z;
  top u=X w=Y; bottom u=X w=-Y; right u=Y w=Z; left u=-Y w=Z; rear u=-X w=Z. Measured live on a bracket
  whose holes lie on three axes (see the tests `test_view_axes_match_live_measurements`).
* Projection: first angle puts the top view **below** the front view and the right view on its **left**
  (`standards.expected_view_side`); third angle mirrors it. `right` means "seen from the right".

## Pitfalls proven live

1. **A view without `GenerativeBehavior.Document` is silently empty** (no error, nothing in the PDF).
   It must be set on every generative view: front, projected, section, detail.
2. **The sheet scale is not inherited.** New views are at (0, 0) with scale 1; set `Scale`, `x`, `y` yourself.
   Projected views are not placed by CATIA either.
3. **`Factory2D` needs the view activated** (`view.Activate()`), otherwise `CreateLine` fails with the
   generic E_FAIL. Re-activate `Main View` afterwards.
4. **Generated edges cannot be dimensioned from Automation**: `Selection.Search` only finds user 2D geometry.
   `DrawingDimensions.Add(type, geometry, points, rep)` works on geometry created with `Factory2D`.
   The tool creates hidden helper geometry (`VisProperties.SetShow(1)`) exactly on the model geometry and
   dimensions that. The value shown is the one you give: take it from the model, and let
   `catia_drawing_check` compare it with the PDF.
5. **Dimension line position = `MoveValue(x, y, 0, 0)`** in view coordinates. The pick points passed to `Add`
   do not place the line (default: 7 mm *inside* the object). For a diameter, `MoveValue` sets the direction
   of the dimension line through the circle centre.
6. **`Sheet.GenerateDimensions()` only generates what the 3D constraints drive** (pad lengths, hole
   diameters). On the test bracket: 2 of 5 needed dimensions plus 3 hole diameters; overall sizes and hole
   positions are never generated. They are drawn green.
7. **A fresh drawing has no readable sheet format**: `PaperSize` raises until you set it. Set `PaperSize`
   then `Orientation`; `GetPaperWidth/Height` then return the ISO sizes (A0..A4 checked, both orientations).
8. **`View.Size` fills a byref array**: from Python it only returns zeros. Read it through a VBScript
   (`SystemService.Evaluate`), same trick as the measurements. It returns (xmin, xmax, ymin, ymax) on the sheet.
9. **`View.x/.y` is the centre of the projected bounding box; `xAxisData/yAxisData` is where the model origin
   lands.** Use the latter to convert view coordinates to the sheet.
10. **PDF export**: `DrawingDocument.ExportData(path, "pdf")` works (vector, page = sheet). Text is exported
    as strokes, so glyphs appear as small circles (about 1.3 mm): the check ignores circles under 2 mm.
11. **Large circles are exported as chains of hundreds of chords.** `extract()` rejected them because the PDF
    coordinate jitter flips the sign of the tiny turning angles (radius 5 mm and above were lost, 3 mm was
    found). `verify.polyline_circles` judges the total turning and the fit residual instead
    (regression test `test_polyline_circles_survive_pdf_jitter`).
12. **Line widths**: `SetRealWidth(index, 1)`: 1 = 0.13 mm, 2 = 0.35, 3 = 0.70 (measured in the PDF; the
    ISO 128-2 group 0.7/0.35 is therefore available, group 0.5/0.25 is not). Generated visible edges are
    exported at 0.35 mm, hidden edges thin dashed. Line types: `SetRealLineType`: 1 continuous, 3 dashed,
    4 chain (dash-dot, used for centre lines).
13. **Closing a drawing may close its linked source** in the same session, and another client of the same
    CATIA (an agent using `catia_close_all`, no lock) can close documents between two calls: every tool
    re-finds its drawing by name and says so when it vanished.
14. **Default view names**: CATIA appends `A-A` to a section view name and `A` to a detail view name
    (`SecA-A`, `DetA`); `SetViewName("", "A", "")` gives the plain label.
15. **The dimension diameter symbol is part of the default prefix**: setting a prefix (`SetPSText(1, "4x ", "")`)
    replaces it. Include the symbol in the prefix when you need both.

## Closed-loop check (`catia_drawing_check`, `verify.py`)

For each orthographic view: the 3D side comes from `catia_list_faces` (cylinders: radius, axis) and
`catia_get_bounding_box`, projected with the axes of the view; the PDF side from `extract` plus
`polyline_circles`. Circles are matched one-to-one by centre (a wrong diameter is reported as an error, not as
a missing circle). Tolerances: diameter 0.02 mm, centre 0.05 mm, pitch 0.1 mm, outline sides 0.05 mm.
Also checked: the view size on paper against bounding box x scale, and every diameter/radius dimension against
a circle really drawn in the PDF. Limits: blind holes are only visible from their open side; a cylinder that
is only part of a circle (fillet) is matched by an arc; sections, details and isometric views are not checked
(their `(u, w)` frame differs); text-sized circles (< 2 mm) are ignored.

## UNVERIFIED-LIVE (not exposed)

* Tolerances on dimensions (`SetTolerances`), thread callouts (`DrawingThreads`), welding symbols, tables
  other than the title block, leaders and notes with `DrawingTexts`/`Leaders`.
* Auxiliary, broken, unfolded and clipping views (`DefineAuxiliaryView`, `DefineBrokenView`, ...).
* Inserting the installed standard's own frame and title block (resource route); saving with `SaveAs`.
* Drawing of an assembly (CATProduct): the calls are the same but no assembly was drawn.
* Model description and audit (part 2 and 3 of the design note).
