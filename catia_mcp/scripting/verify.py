"""Post-run verification of a scenario, driven by the ``checks`` block written by the DSL.

Pure functions over ``(tool, ok, output)`` per step, so the same verification runs after
``script.run()``, after ``python -m catia_mcp.runner`` and in offline tests with fake outputs.
Step numbers in the spec are 1-based, like the ``[NN]`` prefixes of the runner log.

Spec layout (all keys optional)::

    {"features": [{"step": 7, "name": "Base_L120", "sign": "add"|"remove"|"any", "dvol": 1234.5,
                   "tol": 0.02}],
     "volume": {"step": 40, "expected": 5000.0, "tol": 0.02},
     "mass":   {"step": 40, "expected": 0.04, "tol": 0.02},
     "bbox":   {"step": 41, "expected": [100, 50, 20], "tol": 0.5},
     "audit":  {"step": 42},
     "solve": {"step": 60}, "components": {"step": 62, "instances": [...], "poses": {...}},
     "clash": {"step": 63, "allowed": [["A.1", "B.1"]]}}
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from catia_mcp.scripting.poses import Pose, check_poses, parse_components

_DVOL = re.compile(r"volume change\s*([+-]?\d+(?:[.,]\d+)?)")


def _out(results: Sequence[Any], n: int) -> tuple[bool, str] | None:
    """(ok, output) of step n (1-based) or None when it was not executed."""
    if not 1 <= n <= len(results):
        return None
    r = results[n - 1]
    return (bool(getattr(r, "ok", True)), str(getattr(r, "output", "")))


def _json_of(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        return None


def volume_change(output: str) -> float | None:
    m = _DVOL.search(output)
    return float(m.group(1).replace(",", ".")) if m else None


def _rel(actual: float, expected: float, tol: float) -> bool:
    return abs(actual - expected) <= tol * max(abs(expected), 1e-9)


def verify(spec: dict[str, Any] | None, results: Sequence[Any]) -> list[str]:
    """Return the list of problems found (empty = every built-in check passed)."""
    if not spec:
        return []
    problems: list[str] = []

    def get(kind: str, block: dict[str, Any]) -> str | None:
        got = _out(results, block["step"])
        if got is None:
            problems.append(f"{kind}: step {block['step']} was not executed, cannot check")
            return None
        if not got[0]:
            problems.append(f"{kind}: step {block['step']} failed, cannot check")
            return None
        return got[1]

    # UNVERIFIED-LIVE: sign rules (pad/shaft add; pocket/groove/hole/chamfer remove once the body has
    # material; boolean remove < 0) come from the tools' "volume change" lines. Confirm on real runs
    # that no legitimate feature trips them.
    for f in spec.get("features", []):
        got = _out(results, f["step"])
        if got is None or not got[0]:
            continue  # the failure itself is reported by the runner
        dv = volume_change(got[1])
        if dv is None:
            continue  # tool did not report (first feature in an empty body)
        label = f"feature '{f['name']}' (step {f['step']})"
        sign = f.get("sign", "any")
        if abs(dv) < 1e-3:
            problems.append(f"{label}: volume change is 0 - the feature did nothing "
                            "(wrong side, sketch outside the material, or pattern of a zero-volume cut)")
        elif sign == "add" and dv < 0:
            problems.append(f"{label}: expected material to be ADDED, volume change {dv:+.2f} mm3")
        elif sign == "remove" and dv > 0:
            problems.append(f"{label}: expected material to be REMOVED, volume change {dv:+.2f} mm3")
        if f.get("dvol") is not None and not _rel(dv, f["dvol"], f.get("tol", 0.02)):
            problems.append(f"{label}: volume change {dv:+.2f} mm3, expected {f['dvol']:+.2f} "
                            f"(tolerance {f.get('tol', 0.02):.1%})")

    for key, field, label in (("volume", "volume_mm3", "volume"), ("mass", "mass_kg", "mass")):
        blk = spec.get(key)
        if blk:
            text = get(label, blk)
            data = _json_of(text) if text else None
            if text is not None:
                if not data or field not in data:
                    problems.append(f"{label}: no '{field}' in the inertia output")
                elif not _rel(float(data[field]), blk["expected"], blk.get("tol", 0.02)):
                    problems.append(f"{label}: measured {data[field]}, expected {blk['expected']} "
                                    f"(tolerance {blk.get('tol', 0.02):.1%})")

    blk = spec.get("bbox")
    if blk:
        text = get("bbox", blk)
        data = _json_of(text) if text else None
        if text is not None:
            if not data or "size" not in data:
                problems.append("bbox: no 'size' in the bounding-box output")
            else:
                tol = blk.get("tol", 0.5)
                for axis, a, e in zip("XYZ", data["size"], blk["expected"]):
                    if e is not None and abs(a - e) > tol:
                        problems.append(f"bbox: size along {axis} is {a} mm, expected {e} mm (tolerance {tol} mm)")

    blk = spec.get("audit")
    if blk:
        text = get("naming audit", blk)
        if text is not None and "AUDIT: OK" not in text:
            problems.append("naming audit: default names remain in the tree -> "
                            + text[text.find("AUDIT"):][:400].replace("\n", " "))

    blk = spec.get("solve")
    if blk:
        text = get("constraint solve", blk)
        if text is not None and "NOT OK" in text:
            problems.append("constraint solve: " + text.strip()[-400:])

    # UNVERIFIED-LIVE: FIXED components are expected to stay at their pre-fix pose.
    blk = spec.get("components")
    if blk:
        text = get("components", blk)
        if text is not None:
            flat = parse_components(text)
            for inst in blk.get("instances", []):
                if inst not in flat and inst.split("/")[0] not in flat:
                    problems.append(f"components: instance '{inst}' does not exist "
                                    f"(CATIA has: {', '.join(sorted(flat)) or 'nothing'})")
            poses = {k: Pose.from_json(v) for k, v in blk.get("poses", {}).items()}
            problems += ["pose: " + p for p in check_poses(poses, text, blk.get("tol", 0.05))]

    # UNVERIFIED-LIVE: parsing of the catia_clash_analysis output ("NO CLASH..." + JSON with
    # "list"/"between") follows assembly.py but was not run against a live analysis.
    blk = spec.get("clash")
    if blk:
        text = get("clash analysis", blk)
        if text is not None:
            allowed = [tuple(p) for p in blk.get("allowed", [])]
            data = _json_of(text) or {}
            bad = []
            for c in data.get("list", []):
                pair = c.get("between", [])
                if not any(all(any(a in n for n in pair) for a in ap) for ap in allowed):
                    bad.append(f"{' x '.join(pair)} ({c.get('catia_value')} mm)")
            if not data and text.strip().upper().startswith("NO CLASH"):
                pass
            elif not data:
                problems.append("clash analysis: output not understood: " + text[:200])
            elif bad:
                problems.append(f"clash analysis: {len(bad)} unexpected clash(es): " + "; ".join(bad[:10]))

    return problems
