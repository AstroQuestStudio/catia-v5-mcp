"""Scenario runner: play a JSON scenario in CATIA without an MCP client.

    python -m catia_mcp.runner scenario.json [--keep-going] [--dry-run] [--hang-seconds 600]
                                             [--no-kill] [--lock] [--log PATH]
    python -m catia_mcp.runner schema catia_pad catia_hole      # print tool schemas

A scenario is a list of steps ``{"tool": ..., "args": {...}}`` (or ``["tool", {...}]``), or an object
``{"steps": [...], "checks": {...}}`` as written by ``PartScript.write_json`` /
``AssemblyScript.write_json``. A step may carry ``"timeout_s"`` to override ``--hang-seconds``.

What it does, in order:
1. loads the real server and validates EVERY step against the tool schemas (typos, missing or
   mistyped arguments, values outside an enum): a bad scenario never touches CATIA (exit 2);
2. ``--dry-run`` stops there;
3. ``--lock`` waits for the cross-process CATIA lock (several scripts queue instead of interleaving);
   the popup watchdog is started (OK-only dialogs are closed, questions are reported);
4. runs the steps, each under a hang guard: after ``--hang-seconds`` (or the step's ``timeout_s``)
   CATIA is killed (documents saved to disk are safe) unless ``--no-kill``; stops at the first
   failure unless ``--keep-going``;
5. writes ``<scenario>.log`` (``[NN] OK 1.2s tool {args} -> output``) and a summary line, then runs
   the ``checks`` block of the scenario (volumes, bounding box, naming audit, poses, clashes).

Exit codes: 0 all good, 1 a step failed, 2 scenario rejected, 3 CATIA hung, 4 built-in checks failed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from catia_mcp import batch, guard, paths
from catia_mcp.scripting.verify import verify as _verify_checks

EXIT_OK, EXIT_STEP_FAILED, EXIT_REJECTED, EXIT_HANG, EXIT_CHECKS = 0, 1, 2, 3, 4


@dataclass
class RunSummary:
    exit_code: int = EXIT_OK
    total: int = 0
    executed: int = 0
    n_ok: int = 0
    n_err: int = 0
    seconds: float = 0.0
    hung: bool = False
    dry_run: bool = False
    problems: list[str] = field(default_factory=list)        # scenario rejected
    check_problems: list[str] = field(default_factory=list)  # built-in checks
    results: list[batch.StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK


def load_scenario(path: str | Path) -> tuple[list[Any], dict[str, Any]]:
    """(raw steps, checks) from a scenario file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "steps" not in data:
            raise ValueError("scenario object has no 'steps' key")
        return data["steps"], data.get("checks") or {}
    return data, {}


def _prepare(raw: Any) -> tuple[list[tuple[str, dict[str, Any], float | None]], list[str]]:
    """(tool, args, timeout) triples. A legacy ``_timeout_s`` inside args is honoured then removed."""
    steps, problems = batch.normalize_steps(raw, max_steps=batch.MAX_SCENARIO_STEPS)
    if problems:
        return [], problems
    items = json.loads(raw) if isinstance(raw, str) else raw
    out: list[tuple[str, dict[str, Any], float | None]] = []
    for (tool, args), item in zip(steps, items):
        args = dict(args)
        timeout = args.pop("_timeout_s", None)
        if isinstance(item, dict) and item.get("timeout_s") is not None:
            timeout = item["timeout_s"]
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            problems.append(f"step {len(out) + 1} {tool}: timeout must be a positive number of seconds")
            timeout = None
        out.append((tool, args, timeout))
    return out, problems


def format_entry(index: int, ok: bool, seconds: float, tool: str, args: dict[str, Any], output: str) -> str:
    """``[NN] OK 1.2s tool {args} -> first line`` then the other output lines, indented."""
    lines = (output or "").strip().splitlines() or [""]
    head = f"[{index:02d}] {'OK ' if ok else 'ERR'} {seconds:5.1f}s {tool} "
    head += json.dumps(args, ensure_ascii=False) + f" -> {lines[0]}"
    return "\n".join([head] + [f"      {l}" for l in lines[1:]])


def run_scenario(
    raw: Any,
    dispatch: Callable[[str, dict[str, Any]], str],
    schemas: dict[str, dict[str, Any]],
    *,
    checks: dict[str, Any] | None = None,
    keep_going: bool = False,
    dry_run: bool = False,
    hang_seconds: float = 600.0,
    kill: bool = True,
    lock: bool = False,
    log_path: str | Path | None = None,
    out: Callable[[str], None] = print,
    clock: Callable[[], float] = time.time,
    guard_mod: Any = guard,
    watchdog: bool | None = None,
) -> RunSummary:
    """Validate then execute a scenario. ``dispatch(tool, args) -> text`` may raise. Fully testable
    with a fake dispatch (no CATIA, no server)."""
    summary = RunSummary(dry_run=dry_run)
    steps, problems = _prepare(raw)
    if not problems:
        problems = batch.validate_batch([(t, a) for t, a, _ in steps], schemas)
    summary.total = len(steps)
    logf = open(log_path, "w", encoding="utf-8") if (log_path and not dry_run) else None  # noqa: SIM115

    def emit(line: str) -> None:
        out(line)
        if logf:
            logf.write(line + "\n")
            logf.flush()

    try:
        if problems:
            summary.problems = problems
            summary.exit_code = EXIT_REJECTED
            emit(f"SCENARIO REJECTED before running anything: {len(problems)} problem(s). Nothing was executed in CATIA.")
            for p in problems:
                emit(f"  - {p}")
            return summary
        if dry_run:
            emit(f"DRY RUN OK: {len(steps)} step(s) validated against the tool schemas; nothing executed.")
            return summary

        if lock:
            got = guard_mod.acquire_lock()  # blocks until this process owns CATIA
            emit("CATIA lock acquired." if got else "CATIA lock unavailable on this platform (continuing without).")
        if watchdog is None:
            watchdog = paths.env_flag("CATIA_MCP_WATCHDOG", True)
        if watchdog:
            guard_mod.start_watchdog(lambda m: emit(m))

        start = clock()
        for i, (tool, args, tmo) in enumerate(steps, 1):
            limit = tmo or hang_seconds
            # UNVERIFIED-LIVE: taskkill of CNEXT.exe on a real hang, lock queueing between two runners and
            # the popup watchdog need CATIA; everything else is covered by offline tests.
            g = guard_mod.HangGuard(limit, f"step {i} {tool}", kill=kill)
            t0 = clock()
            try:
                with g:
                    text, ok = dispatch(tool, dict(args)), True
            except Exception as e:  # keep the error visible, never crash the runner
                text, ok = f"{type(e).__name__}: {e}", False
            if ok and batch._looks_failed(text):
                ok = False
            if g.fired:
                ok = False
                summary.hung = True
                text = (f"[TIMEOUT] {tool} exceeded {limit:.0f} s; CATIA {'was killed' if kill else 'was NOT killed (--no-kill)'}."
                        f"\n{text}")
                emit(f"[TIMEOUT] step {i} {tool} blocked CATIA for {limit:.0f} s"
                     + (": CATIA killed (relaunched at the next scenario)." if kill else "."))
            dt = clock() - t0
            summary.results.append(batch.StepResult(i, tool, args, ok, text, dt))
            summary.executed += 1
            summary.n_ok += ok
            summary.n_err += not ok
            emit(format_entry(i, ok, dt, tool, args, text))
            if not ok and (not keep_going or (summary.hung and kill)):
                break  # a killed CATIA loses its documents: never carry on blindly
        summary.seconds = clock() - start
        line = f"SUMMARY: {summary.n_ok} OK, {summary.n_err} ERR, {summary.seconds:.0f} s"
        if summary.executed < summary.total:
            line += f"; STOPPED at step {summary.executed}/{summary.total}: the remaining steps were NOT run."
        emit(line)
        if summary.n_err:
            summary.exit_code = EXIT_HANG if summary.hung else EXIT_STEP_FAILED
        elif checks:
            summary.check_problems = _verify_checks(checks, summary.results)
            emit("CHECKS: " + ("all passed." if not summary.check_problems else f"{len(summary.check_problems)} problem(s)"))
            for p in summary.check_problems:
                emit(f"  - {p}")
            if summary.check_problems:
                summary.exit_code = EXIT_CHECKS
        return summary
    finally:
        if logf:
            logf.close()


# ── schema sub-command ──────────────────────────────────────────────────────────────────
def show_schemas(names: list[str], schemas: dict[str, dict[str, Any]], out: Callable[[str], None] = print) -> int:
    if not names or names == ["--list"]:
        for n in sorted(schemas):
            out(n)
        return 0
    rc = 0
    for raw in names:
        name = raw if raw in schemas else f"catia_{raw}"
        if name not in schemas:
            close = difflib.get_close_matches(raw, list(schemas), n=3)
            out(f"{raw}: unknown tool" + (f" (did you mean {', '.join(close)}?)" if close else ""))
            rc = 1
            continue
        s = schemas[name]
        out(name + " " + json.dumps({"required": s.get("required", []), "properties": s.get("properties", {})},
                                    ensure_ascii=False))
    return rc


def _default_server() -> Any:
    from catia_mcp.server import CATIAMCPServer

    return CATIAMCPServer()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m catia_mcp.runner",
        description="Play a JSON scenario of CATIA tool calls. Use 'schema <tool...>' to print tool schemas.",
    )
    ap.add_argument("scenario", help="scenario JSON file")
    ap.add_argument("--keep-going", action="store_true", help="do not stop at the first failing step")
    ap.add_argument("--dry-run", action="store_true", help="validate every step, execute nothing")
    ap.add_argument("--hang-seconds", type=float, default=600.0,
                    help="max seconds per step before CATIA is considered hung (default 600)")
    ap.add_argument("--no-kill", action="store_true", help="on a hang, log it but do not kill CATIA")
    ap.add_argument("--lock", action="store_true", help="queue behind other runners (cross-process CATIA lock)")
    ap.add_argument("--no-prepare", action="store_true",
                    help="do not add the single catia_prepare_geometry step (one part opening per constraint)")
    ap.add_argument("--log", help="log file (default: the scenario path with a .log extension)")
    return ap


def main(argv: list[str] | None = None, server_factory: Callable[[], Any] | None = None,
         guard_mod: Any = guard) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    make = server_factory or _default_server
    if argv and argv[0] == "schema":
        server = make()
        return show_schemas(argv[1:], {d["name"]: d["inputSchema"] for d in server.tool_definitions()})
    args = build_parser().parse_args(argv)
    try:
        raw, checks = load_scenario(args.scenario)
    except (OSError, ValueError) as e:
        print(f"cannot read scenario {args.scenario}: {e}")
        return EXIT_REJECTED
    server = make()
    schemas = {d["name"]: d["inputSchema"] for d in server.tool_definitions()}
    # Scenarios written by hand or by other tools gain the batched designation too. Not when the
    # scenario carries built-in checks: they refer to step numbers, which an inserted step would shift.
    if not checks and not args.no_prepare and "catia_prepare_geometry" in schemas:
        raw = batch.with_prepared_geometry(raw)
    log_path = args.log or str(Path(args.scenario).with_suffix(".log"))
    summary = run_scenario(
        raw,
        lambda tool, a: server.dispatch(tool, a, trace=False),
        schemas,
        checks=checks,
        keep_going=args.keep_going,
        dry_run=args.dry_run,
        hang_seconds=args.hang_seconds,
        kill=not args.no_kill,
        lock=args.lock,
        log_path=log_path,
        guard_mod=guard_mod,
    )
    if not args.dry_run and summary.executed:
        print(f"log: {log_path}")
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
