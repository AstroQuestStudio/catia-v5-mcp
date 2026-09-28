# Working from 2D drawings (optional `drawing` extra)

Many parts come with a **vector PDF** drawing (lines, circles and arcs are real paths, not a
scan). The `drawing_*` tools and `python -m catia_mcp.drawing` read the exact geometry from
it, so sketches are built from measured values instead of eyeballed ones. They do **not** need
CATIA and are optional:

```
pip install "catia-v5-mcp-server[drawing]"
```

> PyMuPDF, which powers this extra, is licensed AGPL-3.0. It is never imported unless you call
> a drawing tool, and nothing else in the server depends on it. Check that the license fits
> your use before installing it.

## Tools

| Tool | Purpose |
|---|---|
| `drawing_render` | PNG of a page or region (optional grid in part mm). Use it to *read* dimensions. |
| `drawing_extract_geometry` | Numbered circles / arcs / lines with centre, R and D in part mm. |
| `drawing_overlay` | Draws a sketch over the drawing to check it visually. |

The same three operations exist on the command line:

```
python -m catia_mcp.drawing render  part.pdf --dpi 110 --out page.png
python -m catia_mcp.drawing geom    part.pdf --region 20,30,180,150
python -m catia_mcp.drawing geom    part.pdf --region 20,30,180,150 --scale 1:2 --origin 100,90
python -m catia_mcp.drawing overlay part.pdf --sketch sketch.json --region 20,30,180,150 \
        --scale 1:2 --origin 100,90 --hv="-h,v" --out check.png
```

## Coordinate frames

* **Paper**: millimetres from the top-left corner of the sheet, y pointing down. `region` and
  `origin` are given in paper mm.
* **Model** (all output values): `X = (px - ox) * k`, `Y = (oy - py) * k`, with `(ox, oy)` the
  `origin` and `k` the scale factor (`1:2` means 1 mm on paper is 2 mm on the part, k = 2).
  X is right, Y is up. Without `origin`, the sheet corner is used; every entity also prints its
  paper position, so you can pick a centre and pass it back as `origin`.

## Workflow: read the drawing, sketch, check by overlay

1. **Render the whole page** at about 110 dpi. Identify the views and the title block scale.
2. **Zoom** on each view (`region` + 300-400 dpi) and **read the dimension values by eye**:
   dimension text is drawn as vector strokes, so it cannot be extracted as text.
3. **Measure**: `drawing_extract_geometry` on the view's region *without* `origin` to find the
   axis / centre that serves as the datum, then again with `origin` and `scale` to get part
   coordinates. Values read on the image and values measured must agree; when they do not,
   trust the measurement and note the difference.
4. **Sketch** in CATIA with the measured values (`catia_sketch_profile`, ...).
5. **Overlay** the same entities on the drawing (`drawing_overlay`; `sketch` accepts the
   arguments of the sketch tools, e.g. a `catia_sketch_profile` object). Look at the PNG: the
   coloured line must follow the black line everywhere. Use `hv` (`h,v`, `-h,v`, `v,h`, ...)
   when the sketch plane axes are rotated or mirrored relative to the view.
6. Only then create the pad / shaft / pocket.

## Pitfalls

* **Title block scale is sometimes wrong** (or unreadable: it is often vectorised too, then
  the tool assumes 1:1 and warns). Confirm with one known dimension, for example an overall
  length written on the drawing, before trusting `scale`.
* **R vs D**: outputs list both, so match them against the symbol on the drawing (`R25` vs
  `Ø25`).
* **Rounded dimensions**: written values are rounded; the drawn geometry may be tangent where
  the rounded values are not. Prefer measured values, and derive tangent constructions from
  the measured geometry rather than from two rounded numbers.
* **Dimensions are vectorised**: text search finds nothing. Read on `drawing_render`, confirm
  with the geometry.
* **Curves that are not circles** (splines, ellipses) come back as many short line segments.
* Scans and bitmap PDFs contain no geometry: the tool reports that nothing was found.
* Thresholds `min_len` / `min_radius` are in **paper** mm; lower them to see small details.

## Sketch JSON accepted by `overlay`

Coordinates in part mm, in the sketch's own (h, v) axes:

```json
[
  {"type": "circle", "cx": 0, "cy": 0, "radius": 25},
  {"type": "line", "x1": 0, "y1": 0, "x2": 40, "y2": 0},
  {"type": "arc", "center": [0, 0], "start": [25, 0], "end": [0, 25], "direction": "ccw"},
  {"start": [0, 0], "segments": [{"type": "line", "to": [50, 0]}, {"type": "line"}]}
]
```

A harness-style list of `["catia_sketch_profile", {...}]` pairs and `{"profiles": [...]}` work
too.
