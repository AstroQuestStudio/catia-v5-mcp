"""Compact, LLM-readable text rendering of an ``extract()`` result (no PyMuPDF needed)."""

from __future__ import annotations

from typing import Any


def format_report(res: dict[str, Any], max_items: int = 60) -> str:
    """Numbered list of circles / arcs / lines with radii and diameters (model mm)."""
    out = [
        f"# model mm (X right, Y up), origin at paper {res['origin_paper'][0]:.2f},"
        f"{res['origin_paper'][1]:.2f} mm ; scale {res['scale_text']} ({res['scale_source']}) ; "
        f"{res['counts']['circles']} circles, {res['counts']['arcs']} arcs, "
        f"{res['counts']['lines']} lines"
    ]
    if res["bbox"]:
        b = res["bbox"]
        out.append(f"# bbox X[{b[0]:.3f}, {b[2]:.3f}] Y[{b[1]:.3f}, {b[3]:.3f}]")
    for w in res["warnings"]:
        out.append(f"! {w}")

    def section(title: str, items: list[str], total: int) -> None:
        if not items:
            return
        out.append(f"## {title}")
        out.extend(items)
        if total > len(items):
            out.append(f"... {total - len(items)} more (narrow `region` or raise min sizes)")

    section("Circles", [
        f"C{i}: centre=({c['cx']:.3f}, {c['cy']:.3f}) R={c['r']:.3f} D={c['d']:.3f}"
        f"  [paper {c['paper'][0]:.2f},{c['paper'][1]:.2f}]"
        for i, c in enumerate(res["circles"][:max_items], 1)], len(res["circles"]))
    section("Arcs", [
        f"A{i}: centre=({a['cx']:.3f}, {a['cy']:.3f}) R={a['r']:.3f} D={2 * a['r']:.3f} "
        f"span={a['span_deg']:.1f}deg {a['direction']} from ({a['start'][0]:.3f}, {a['start'][1]:.3f})"
        f" to ({a['end'][0]:.3f}, {a['end'][1]:.3f})"
        for i, a in enumerate(res["arcs"][:max_items], 1)], len(res["arcs"]))
    section("Lines", [
        f"L{i}: ({s['x0']:.3f}, {s['y0']:.3f}) -> ({s['x1']:.3f}, {s['y1']:.3f}) "
        f"len={s['length']:.3f} angle={s['angle_deg']:.1f}deg"
        for i, s in enumerate(res["lines"][:max_items], 1)], len(res["lines"]))
    return "\n".join(out)
