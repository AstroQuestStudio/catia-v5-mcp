"""Validation helpers shared by the scripting DSL (pure Python: never imports COM)."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from catia_mcp.naming import is_default_name

MAX_MM = 100_000.0  # anything larger is almost certainly a unit mistake (metres, micrometres)
EPS = 1e-9


class ScriptError(ValueError):
    """A script call was refused before anything could reach CATIA."""


# CATIA's bare type names, with or without a counter, in English and French UIs.
_BARE_TYPES = {
    "pad", "pocket", "sketch", "hole", "fillet", "chamfer", "shaft", "groove", "body", "extrusion",
    "esquisse", "corps", "trou", "conge", "chanfrein", "poche", "rainure", "arbre", "mirror",
    "pattern", "boolean", "assemble", "remove", "add", "intersect", "thread", "shell", "draft",
    "constraint", "contrainte", "coincidence", "contact", "offset", "angle", "fix", "fixed",
    "partbody", "part", "product", "name", "test", "tmp", "temp", "new", "feature",
}
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


def check_name(name: Any, what: str, used: Iterable[str] | None = None) -> str:
    """An explicit, non-default, unique name (CATIA trees are read by humans and audited)."""
    if not isinstance(name, str) or not name.strip():
        raise ScriptError(f"{what}: a non-empty explicit name is required (got {name!r}).")
    if not _NAME_RE.match(name):
        raise ScriptError(
            f"{what}: name {name!r} must use letters, digits, '_', '-' or '.' only (no spaces or accents)."
        )
    if not re.search(r"[A-Za-z]", name):
        raise ScriptError(f"{what}: name {name!r} must contain at least one letter.")
    if is_default_name(name):
        raise ScriptError(
            f"{what}: {name!r} looks like a CATIA default name (Pad.1, Sketch.2...). "
            "Give it a meaningful name such as 'Base_L120' or 'Bore_D25'."
        )
    stem = re.sub(r"[\s_\-.]*\d*$", "", name).lower()
    if stem in _BARE_TYPES:
        raise ScriptError(
            f"{what}: {name!r} says nothing about the role of the object. "
            "Use '<Role>_<key dimension>' (e.g. 'Slot_W8', 'Boss_D40')."
        )
    if used is not None and name in used:
        raise ScriptError(f"{what}: name {name!r} is already used in this script (names must be unique).")
    return name


def num(value: Any, what: str, *, positive: bool = False, nonzero: bool = False,
        nonneg: bool = False, lo: float | None = None, hi: float | None = None,
        limit: float | None = MAX_MM) -> float:
    """A finite real number in millimetres (or degrees); bool and str are refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScriptError(f"{what}: expected a number (mm), got {value!r} ({type(value).__name__}).")
    v = float(value)
    if not math.isfinite(v):
        raise ScriptError(f"{what}: {value!r} is not finite.")
    if limit is not None and abs(v) > limit:
        raise ScriptError(f"{what}: {v} is beyond {limit:g}. Values are in MILLIMETRES.")
    if positive and v <= 0:
        raise ScriptError(f"{what}: must be > 0 (got {v}).")
    if nonneg and v < 0:
        raise ScriptError(f"{what}: must be >= 0 (got {v}).")
    if nonzero and abs(v) < EPS:
        raise ScriptError(f"{what}: must not be 0.")
    if lo is not None and v < lo:
        raise ScriptError(f"{what}: must be >= {lo} (got {v}).")
    if hi is not None and v > hi:
        raise ScriptError(f"{what}: must be <= {hi} (got {v}).")
    return v


def integer(value: Any, what: str, *, lo: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScriptError(f"{what}: expected an integer, got {value!r}.")
    if value < lo:
        raise ScriptError(f"{what}: must be >= {lo} (got {value}).")
    return value


def point(value: Any, what: str, dim: int) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != dim:
        raise ScriptError(f"{what}: expected {dim} numbers (mm), got {value!r}.")
    return [num(v, f"{what}[{i}]") for i, v in enumerate(value)]


def point3(value: Any, what: str) -> list[float]:
    return point(value, what, 3)


def point2(value: Any, what: str) -> list[float]:
    return point(value, what, 2)


def one_of(value: Any, what: str, allowed: Iterable[str]) -> str:
    allowed = tuple(allowed)
    if value not in allowed:
        raise ScriptError(f"{what}: {value!r} is not one of {', '.join(allowed)}.")
    return value


def make_step(tool: str, args: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"tool": tool, "args": args}
    if timeout_s:
        out["timeout_s"] = timeout_s
    return out
