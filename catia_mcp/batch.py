"""Batch execution: many tool calls in ONE MCP round trip, validated before CATIA is touched.

Why: an agent that models a part with ~40 calls pays 40 LLM round trips, and one typo in
step 31 is discovered only after 30 modifications. ``catia_batch`` first validates EVERY step
against the tool schemas (unknown tool, misspelt or missing argument, wrong type, value not in
the enum), and only then runs them, stopping at the first failure by default. The module is
pure Python: it is unit-tested without CATIA.
"""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_STEPS = 500            # one catia_batch tool call (keeps the model's answer small)
MAX_SCENARIO_STEPS = 5_000_000  # a scenario FILE run by catia-mcp-run: chunking is the runner's job
NOT_BATCHABLE = {"catia_batch"}


@dataclass
class StepResult:
    index: int
    tool: str
    args: dict[str, Any]
    ok: bool
    output: str
    seconds: float = 0.0


@dataclass
class BatchReport:
    steps: list[StepResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    total: int = 0
    dry_run: bool = False
    stopped_early: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems and all(s.ok for s in self.steps)

    def text(self, max_ok_chars: int = 300, max_err_chars: int = 2000) -> str:
        lines: list[str] = []
        if self.problems:
            lines.append(
                f"BATCH REJECTED before running anything: {len(self.problems)} problem(s). "
                "Nothing was executed in CATIA."
            )
            lines += [f"  - {p}" for p in self.problems]
            return "\n".join(lines)
        if self.dry_run:
            return (
                f"DRY RUN OK: {self.total} step(s) validated against the tool schemas; "
                "nothing executed."
            )
        for s in self.steps:
            head = f"[{s.index:02d}] {'OK ' if s.ok else 'ERR'} {s.seconds:5.1f}s {s.tool}"
            body = s.output.strip()
            limit = max_ok_chars if s.ok else max_err_chars
            if len(body) > limit:
                body = body[:limit] + f" ... [+{len(body) - limit} chars]"
            lines.append(f"{head}\n      -> {body}")
        n_ok = sum(s.ok for s in self.steps)
        n_err = len(self.steps) - n_ok
        summary = f"SUMMARY: {n_ok} OK, {n_err} ERR, {sum(s.seconds for s in self.steps):.1f}s"
        if self.stopped_early:
            summary += (
                f"; STOPPED at step {self.steps[-1].index}/{self.total} (stop_on_error): "
                "the remaining steps were NOT run."
            )
        lines.append(summary)
        return "\n".join(lines)


def normalize_steps(
    raw: Any, max_steps: int = MAX_STEPS
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Accept ``[{"tool": ..., "args": {...}}]`` or ``[["tool", {...}], ...]``."""
    problems: list[str] = []
    steps: list[tuple[str, dict[str, Any]]] = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            return [], [f"'steps' is a string that is not valid JSON: {e}"]
    if not isinstance(raw, list) or not raw:
        return [], ["'steps' must be a non-empty list"]
    if len(raw) > max_steps:
        return [], [f"too many steps ({len(raw)} > {max_steps}); split the batch"]
    for i, item in enumerate(raw, 1):
        if isinstance(item, dict) and "tool" in item:
            tool, args = item["tool"], item.get("args") or {}
        elif isinstance(item, (list, tuple)) and 1 <= len(item) <= 2:
            tool, args = item[0], (item[1] if len(item) == 2 else {})
        else:
            problems.append(
                f"step {i}: expected {{'tool': name, 'args': {{...}}}}, got {item!r}"[:200]
            )
            continue
        if not isinstance(tool, str) or not isinstance(args, dict):
            problems.append(f"step {i}: 'tool' must be a string and 'args' an object")
            continue
        steps.append((tool, args))
    return steps, problems


def validate_step(schema: dict[str, Any], args: dict[str, Any], validator: Any = None) -> list[str]:
    """Human-readable schema violations for one call (empty list = valid). Pass a prebuilt
    ``validator`` when validating many steps of the same tool."""
    from jsonschema import Draft202012Validator

    validator = validator or Draft202012Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(args), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in err.path) or "(arguments)"
        out.append(f"{where}: {err.message}"[:300])
    # jsonschema ignores unknown keys unless additionalProperties is false: flag typos ourselves.
    props = schema.get("properties", {})
    if props:
        for key in args:
            if key not in props:
                close = difflib.get_close_matches(key, list(props), n=1)
                hint = (
                    f" (did you mean '{close[0]}'?)"
                    if close
                    else f" (known: {', '.join(sorted(props))})"
                )
                out.append(f"unknown argument '{key}'{hint}")
    return out


def validate_batch(
    steps: list[tuple[str, dict[str, Any]]], schemas: dict[str, dict[str, Any]]
) -> list[str]:
    from jsonschema import Draft202012Validator

    problems: list[str] = []
    validators: dict[str, Any] = {}
    for i, (tool, args) in enumerate(steps, 1):
        if tool in NOT_BATCHABLE:
            problems.append(f"step {i}: '{tool}' cannot be nested in a batch")
            continue
        schema = schemas.get(tool)
        if schema is None:
            close = difflib.get_close_matches(tool, list(schemas), n=3)
            hint = f" (did you mean {', '.join(close)}?)" if close else ""
            problems.append(f"step {i}: unknown tool '{tool}'{hint}")
            continue
        if tool not in validators:
            validators[tool] = Draft202012Validator(schema)
        problems += [f"step {i} {tool}: {p}" for p in validate_step(schema, args, validators[tool])]
    return problems


_FAIL_MARKERS = ("error in ", "unknown tool", "error:", "failed:")


def _looks_failed(output: str) -> bool:
    """Some tools report failure as text instead of raising: treat those as failed steps."""
    return output.strip().lower()[:120].startswith(_FAIL_MARKERS)


def run_batch(
    raw_steps: Any,
    dispatch: Callable[[str, dict[str, Any]], str],
    schemas: dict[str, dict[str, Any]],
    stop_on_error: bool = True,
    dry_run: bool = False,
    clock: Callable[[], float] = time.time,
) -> BatchReport:
    steps, problems = normalize_steps(raw_steps)
    report = BatchReport(total=len(steps), dry_run=dry_run)
    if not problems:
        problems = validate_batch(steps, schemas)
    if problems:
        report.problems = problems
        return report
    if dry_run:
        return report
    for i, (tool, args) in enumerate(steps, 1):
        t0 = clock()
        try:
            out, ok = dispatch(tool, dict(args)), True
        except Exception as e:  # keep the error visible, never crash the batch runner
            out, ok = f"{type(e).__name__}: {e}", False
        if ok and _looks_failed(out):
            ok = False
        report.steps.append(StepResult(i, tool, args, ok, out, clock() - t0))
        if not ok and stop_on_error:
            report.stopped_early = i < len(steps)
            break
    return report


_CONSTRAINT_TOOLS = (
    "catia_coincidence_constraint", "catia_contact_constraint",
    "catia_offset_constraint", "catia_angle_constraint",
)


def with_prepared_geometry(raw: Any) -> Any:
    """Insert ONE ``catia_prepare_geometry`` step after the last ``catia_add_component``.

    It resolves every face/axis/edge the constraints designate with a single opening per part file
    instead of one per constraint (a window opens, is searched and closes, the screen reloads: ~1 s
    each). Returns ``raw`` untouched when there is nothing to prepare, when a prepare step already
    exists, or when the scenario is malformed (the normal validation then explains why).
    """
    steps, problems = normalize_steps(raw, max_steps=MAX_SCENARIO_STEPS)
    if problems or any(t == "catia_prepare_geometry" for t, _ in steps):
        return raw
    items, seen, last_add = [], set(), -1
    for i, (tool, args) in enumerate(steps):
        if tool == "catia_add_component":
            last_add = i
        if tool in _CONSTRAINT_TOOLS:
            for comp, elem in (("component1", "element1"), ("component2", "element2")):
                el = args.get(elem)
                if not isinstance(el, dict) or not el or "plane" in el or not args.get(comp):
                    continue
                key = (args[comp], json.dumps(el, sort_keys=True))
                if key not in seen:
                    seen.add(key)
                    items.append({"component": args[comp], "element": el})
    if not items or last_add < 0:
        return raw
    listed = json.loads(raw) if isinstance(raw, str) else list(raw)
    listed.insert(last_add + 1, {"tool": "catia_prepare_geometry", "args": {"items": items}})
    return listed
