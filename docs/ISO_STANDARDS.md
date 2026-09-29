# ISO standards used for technical drawings

`catia_mcp/standards.py` holds the data and the checks that the drafting tools rely on
(see [DESIGN_DRAFTING_AND_REVERSE.md](DESIGN_DRAFTING_AND_REVERSE.md)). It is pure standard
library, never touches CATIA, and is covered by `tests/test_standards.py`.

Values were checked against the text of the standard when a preview was reachable, and against
several independent secondary sources otherwise. The last column says which. **Anything that could
not be confirmed is marked `unverified` in the code (`# unverified`) and in the last section of
this page.** Standards are copyrighted: this page and the module reproduce numeric values and rule
summaries only, not the standards' text. Buy the standards for anything contractual.

## Retained standards

| Topic | Standard (number:year) | What the module implements | Checked against |
|---|---|---|---|
| Sheet sizes and layout | ISO 5457:1999 (+ Amd 1:2010, not read) | A0..A4 trimmed and untrimmed sizes (Table 1), drawing space, frame (20 mm left, 10 mm elsewhere, 0.7 mm line), title block position (bottom right; A0..A3 horizontal, A4 vertical), grid field counts (Table 2), `drawing_space()` reproduces Table 1 | Standard text (preview) |
| Scales | ISO 5455:1979 | Recommended series 50:1 ... 1:10000, power-of-ten extension, designation `SCALE X:Y`, rule for a second scale next to its view, `preferred_scale()` | Standard text (preview) |
| Title block | ISO 7200:2004 | The 19 data fields with mandatory/optional flag, recommended lengths and clause; 180 mm width | Standard text (preview) |
| Line types and widths | ISO 128-2:2020 (replaces ISO 128-20:1996 and ISO 128-24:1999) | Width series 0.13 ... 2 mm (ratio 1:sqrt(2)), wide:narrow = 2:1, line groups (0.5 and 0.7 preferred), types 01.1 01.2 02.1 02.2 04.1 04.2 05.1 and the usage of each (visible outline, hidden, centre, cutting plane, dimension, hatching ...) | ISO 128-20:1996 and ISO 128-24:1999 previews (both withdrawn). ISO 128-2:2020 itself not read: carry-over is `unverified` |
| Dimensioning | ISO 129-1:2018 (+ Amd 1:2020, not read) | Rules checked by `validate_drawing_spec`: each dimension shown once, auxiliary dimension in parentheses, dimensions in the view that shows the feature best, hidden features and inside-contour placement discouraged, unit rules (angular unit always shown, other unit flagged), comma decimal marker, out-of-scale values underlined | Standard text (preview) |
| General tolerances | ISO 2768-1:1989 | Tables 1-3 for classes f, m, c, v: linear (0.5-4000 mm), broken edges (external radii, chamfer heights), angular; designation `ISO 2768-m` | Four secondary sources agree, except one cell (see below) |
| Projection methods | ISO 5456-2:1996 (arrangement also in ISO 128-3:2020 4.5-4.10) | First and third angle: where the top, bottom, left, right views go relative to the front view; alignment check | Standard text (previews) |
| Metric threads | ISO 261:1998 (general plan), ISO 262 (selected sizes), ISO 724:1993 (basic dimensions), ISO 68-1 (profile) | Designation `M8`, `M8x1` (coarse pitch implied), coarse pitches M1..M64, fine pitch validation, H, d2, d1 from the basic profile | Secondary sources, partly `unverified` |

## What `standards.py` provides

| Need | API |
|---|---|
| Smallest sheet + preferred scale for the views | `choose_sheet(width_mm, height_mm, n_views=1, margins=None, ...)` -> `SheetChoice` |
| Sheet data and geometry | `SHEETS`, `get_sheet`, `frame_rect`, `drawing_space`, `title_block_rect` |
| Scales | `parse_scale`, `preferred_scale(ratio, mode="fit"/"up"/"nearest")`, `is_preferred_scale`, `PREFERRED_SCALES` |
| General tolerance | `general_tolerance(size_mm, cls="m", kind="linear"/"broken_edge"/"angular")`, `general_tolerance_designation` |
| Lines | `LINE_WIDTHS`, `LINE_GROUPS`, `LINE_TYPES`, `LINE_USAGE`, `line_type_for_usage`, `line_pair_for_group` |
| Title block | `TITLE_BLOCK_FIELDS`, `TITLE_BLOCK_WIDTH_MM` |
| Projection | `PROJECTION_METHODS`, `expected_view_side(projection, view)` |
| Threads | `thread_designation(nominal, pitch=None)`, `thread_info`, `parse_thread_designation`, `COARSE_PITCH` |
| Audit of a whole drawing spec | `validate_drawing_spec(spec) -> list[Issue]`, `Issue(severity, code, message, reference, path)` |

`choose_sheet` conventions (design choices, not ISO): `width_mm x height_mm` is the model size of one
view; every view gets `margins` (default 15 mm) of clear paper on each side for dimensions; a 40 mm
strip is reserved for the title block over the full width (conservative); the largest recommended
scale not above `max_scale` (default 1:1) is taken on the smallest sheet whose scale is not below
`min_scale` (default 1:2). Otherwise the largest sheet is returned with a note.

**Scale convention.** `standards.Scale.ratio` is paper length / model length (2:1 -> 2.0,
1:2 -> 0.5). `catia_mcp.drawing.geometry.parse_scale` returns the opposite (model / paper: 1:2 ->
2.0) because it converts paper millimetres to part millimetres. Convert explicitly at the boundary.

## Severity of `validate_drawing_spec` findings

* `error`: contradicts a "shall" of the standard (unknown sheet size, missing mandatory title block
  field, non-standard line width, wrong line type for a usage, wrong view arrangement for the stated
  projection, redundant dimension, angular dimension without unit, out-of-scale dimension not underlined,
  view outside the frame or over the title block, invalid thread designation).
* `warning`: departs from a "should", a recommendation or a common practice the standard states
  (non-preferred scale, A4 landscape, field longer than the recommended length, dimension on a hidden
  feature, comma decimal marker, non-preferred line group, undimensioned feature, no general tolerance).

## Out of scope (not implemented)

Lettering (ISO 3098), geometrical tolerancing (ISO 1101, datums ISO 5459), size tolerancing and fits
(ISO 14405, ISO 286), surface texture (ISO 1302 / ISO 21920), thread representation (ISO 6410) and
tolerance classes (ISO 965), sections and hatching rules in detail (ISO 128-3:2020 clauses beyond
projection), edge indication (ISO 13715), welding (ISO 2553), gears, springs, piping, electrical and
building drawings, drawing frame graphics (grid letters, trimming marks) beyond their dimensions,
the exact geometry of the projection symbol (ISO 5456-2 Annex A was not read), arrowhead geometry
(ISO 129-1 clause 5, not implemented), ISO 2768-2 geometrical general tolerances, and any national
variant (ASME Y14.5, DIN, JIS, GB).

## Unverified values and known gaps

1. **ISO 2768-1 Table 3, class c, "over 120 up to 400" mm.** Two sources give 0 deg 15', two give
   0 deg 20'. The module uses 0 deg 15' (same as DIN 7168 coarse). The other cells agree in every
   source consulted.
2. **Coarse pitches** of M1.4 (0.3), M1.8 (0.35), M18 (2.5), M33 (3.5), M39 (4), M45 (4.5),
   M52 (5), M60 (5.5) were not corroborated by two sources (the others were). `thread_info().notes`
   says so. Confirm in ISO 261:1998 Table 1 / ISO 262.
3. **ISO 261 pitch series** (0.2 ... 8) is used to accept fine pitches; it was recalled, not cross-checked.
   `SELECTED_FINE_PITCH` is only the subset confirmed by a reference table; other fine pitches are
   accepted and flagged as "not confirmed as an ISO 262 selected size".
4. **ISO 128-2:2020** replaced ISO 128-20 and ISO 128-24. The type numbers (01.1, 01.2, ...), the width
   series and the line groups come from the withdrawn editions. Compare with ISO 128-2:2020 before
   relying on them.
5. **ISO 2768:2025** (a single-part revision of ISO 2768-1) was announced as in pre-publication. If it is
   published, tolerance values may change. The module implements ISO 2768-1:1989.
6. **ISO 7200:2004.** The title block height and the exact field arrangement (Figures 1 and 2) are not
   fixed here; only the fields and the 180 mm width are. Current status of the standard not checked
   (the ISO catalogue was not reachable).
7. **ISO 5457:1999 Amd 1:2010** and **ISO 129-1:2018 Amd 1:2020** were not read.
8. Basic thread dimensions use `d2 = d - 0.649519 P`, `d1 = d - 1.082532 P`, `H = sqrt(3)/2 P` from
   the ISO 68-1 profile as reproduced by a secondary source; tolerances and classes (ISO 965) are not modelled.
9. Design constants that are choices, not norms: view margin 15 mm, title block strip 40 mm,
   projected-view alignment tolerance 1 mm, dimension scale tolerance 1 %.
