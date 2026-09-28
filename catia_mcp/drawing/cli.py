"""Command line: ``python -m catia_mcp.drawing geom|render|overlay <pdf> ...``.

Requires the optional PyMuPDF extra: pip install "catia-v5-mcp-server[drawing]".

  geom    <pdf> [--page 0] [--region x0,y0,x1,y1] [--scale 1:2] [--origin ox,oy]
                [--min-len 1.0] [--min-r 0.6] [--json] [--max-items 60]
  render  <pdf> [--page 0] [--region ...] [--dpi 150] [--out f.png]
                [--scale 1:2 --origin ox,oy --grid 10]     (grid in PART mm)
  overlay <pdf> --sketch sketch.json [--region ...] --scale 1:2 --origin ox,oy
                [--hv="-h,v"] [--dpi 150] [--out f.png]

Region and origin are PAPER mm from the top-left corner of the sheet (y down). Workflow:
render the whole page, geom on a view's region WITHOUT origin to find the axis/centre (each
entity prints its paper position), then geom again with --origin to get exact part mm.
Write values starting with a dash with "=" (--hv="-h,v").
"""

from __future__ import annotations

import argparse
import json
import sys

from catia_mcp.drawing import geometry, render
from catia_mcp.drawing.report import format_report


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m catia_mcp.drawing",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("pdf")
        p.add_argument("--page", type=int, default=0)
        p.add_argument("--region", "--box", dest="region", help="x0,y0,x1,y1 paper mm")
        p.add_argument("--scale", help="drawing scale, e.g. 1:2 (default: title block text, else 1:1)")
        p.add_argument("--origin", help="ox,oy paper mm point = model (0,0)")

    g = sub.add_parser("geom", help="print exact circles/arcs/lines")
    common(g)
    g.add_argument("--min-len", type=float, default=1.0, help="min segment length, paper mm")
    g.add_argument("--min-r", type=float, default=0.6, help="min arc radius, paper mm")
    g.add_argument("--max-items", type=int, default=60)
    g.add_argument("--json", action="store_true", help="raw JSON instead of text")

    r = sub.add_parser("render", help="render a page/region to PNG")
    common(r)
    r.add_argument("--dpi", type=int, default=150)
    r.add_argument("--grid", type=float, help="graduated grid step in part mm (needs --origin)")
    r.add_argument("--out")

    o = sub.add_parser("overlay", help="draw a sketch over the drawing")
    common(o)
    o.add_argument("--sketch", required=True, help="JSON file (see catia_mcp.drawing.render.load_sketch)")
    o.add_argument("--dpi", type=int, default=150)
    o.add_argument("--hv", default="h,v", help="sketch (h,v) -> view (X,Y): h,v | -h,v | v,h ...")
    o.add_argument("--out")
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "geom":
            res = geometry.extract(a.pdf, a.page, a.region, a.scale, a.origin, a.min_len, a.min_r)
            print(json.dumps(res, indent=1) if a.json else format_report(res, a.max_items))
        elif a.cmd == "render":
            print(render.render_view(a.pdf, a.page, a.region, a.out, a.dpi, a.scale, a.origin, a.grid))
        else:
            print(render.overlay(a.pdf, a.page, a.sketch, a.out, a.region, a.scale, a.origin, a.dpi, a.hv))
    except geometry.DrawingDependencyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
