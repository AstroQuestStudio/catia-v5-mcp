"""Common machinery of PartScript and AssemblyScript: step list, JSON, validation, execution."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catia_mcp import batch
from catia_mcp.scripting._common import ScriptError, make_step
from catia_mcp.scripting.verify import verify as _verify_checks

SCENARIO_FORMAT = "catia-mcp-scenario/1"
_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


def load_schemas(server: Any = None) -> dict[str, dict[str, Any]]:
    """Real tool schemas ({tool name: JSON schema}) from the server (no CATIA needed)."""
    if server is None:
        if _SCHEMA_CACHE:
            return _SCHEMA_CACHE
        from catia_mcp.server import CATIAMCPServer  # lazy: importing the server is slow

        server = CATIAMCPServer()
        schemas = {d["name"]: d["inputSchema"] for d in server.tool_definitions()}
        _SCHEMA_CACHE.update(schemas)
        return _SCHEMA_CACHE
    return {d["name"]: d["inputSchema"] for d in server.tool_definitions()}


@dataclass
class ScriptResult:
    """Outcome of ``script.run()``: the batch report plus the built-in checks."""

    report: batch.BatchReport
    check_problems: list[str] = field(default_factory=list)
    log_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.report.ok and not self.check_problems

    def text(self) -> str:
        lines = [self.report.text()]
        if not self.report.problems and not self.report.dry_run:
            lines.append("CHECKS: " + ("all passed." if not self.check_problems
                                       else f"{len(self.check_problems)} problem(s)"))
            lines += [f"  - {p}" for p in self.check_problems]
        return "\n".join(lines)


class ScriptBase:
    """Emits ``{"tool": ..., "args": {...}}`` steps. Never imports COM."""

    kind = "script"

    def __init__(self, name: str, folder: str | Path) -> None:
        self.name = name
        self.folder = Path(folder).expanduser().resolve()
        self._steps: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    # -- emission ----------------------------------------------------------------------
    def _emit(self, tool: str, args: dict[str, Any] | None = None, timeout_s: float | None = None) -> int:
        """Append a step, return its 1-based number (the ``[NN]`` of the run log)."""
        self._steps.append(make_step(tool, dict(args or {}), timeout_s))
        return len(self._steps)

    def raw(self, tool: str, args: dict[str, Any] | None = None, timeout_s: float | None = None) -> int:
        """Escape hatch for a tool without a DSL method. Still validated against the real schema."""
        from catia_mcp.naming import is_creation_tool

        if not isinstance(tool, str) or not tool.startswith("catia_"):
            raise ScriptError(f"raw: {tool!r} is not a catia_* tool name.")
        args = dict(args or {})
        if is_creation_tool(tool):
            from catia_mcp.scripting._common import check_name

            check_name(args.get("name"), f"raw {tool}")
        return self._emit(tool, args, timeout_s)

    def _before_finish(self) -> None:
        """Hook: refuse ``steps()`` while something is half-built (open sketch...)."""

    def steps(self) -> list[dict[str, Any]]:
        self._before_finish()
        return copy.deepcopy(self._steps)

    def checks_spec(self) -> dict[str, Any]:
        return {}

    def scenario(self) -> dict[str, Any]:
        """The full scenario: steps + built-in checks (what ``write_json`` writes)."""
        return {"format": SCENARIO_FORMAT, "name": self.name, "kind": self.kind,
                "steps": self.steps(), "checks": self.checks_spec()}

    def ensure_dirs(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "screens").mkdir(exist_ok=True)

    def write_json(self, path: str | Path | None = None) -> Path:
        """Write the scenario (default ``<folder>/_build/<name>.json``); runnable by the runner CLI."""
        p = Path(path) if path else self.folder / "_build" / f"{self.name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.scenario(), indent=1, ensure_ascii=False), encoding="utf-8")
        return p

    # -- validation / execution --------------------------------------------------------
    def validate(self, schemas: dict[str, dict[str, Any]] | None = None) -> list[str]:
        """Problems found validating EVERY step against the real tool schemas (empty = valid)."""
        steps, problems = batch.normalize_steps(self.steps())
        if problems:
            return problems
        return batch.validate_batch(steps, schemas or load_schemas())

    def run(self, server: Any = None, dry_run: bool = False, stop_on_error: bool = True,
            hang_seconds: float = 600.0, kill: bool = False, log: bool = True) -> ScriptResult:
        """Validate then execute through ``batch.run_batch`` (stops at the first failure).

        ``server`` = a CATIAMCPServer (created on demand) or any object with ``tool_definitions()``
        and ``dispatch(name, args, trace=False)``. ``dry_run`` validates only.
        """
        if server is None:
            from catia_mcp.server import CATIAMCPServer

            server = CATIAMCPServer()
        schemas = load_schemas(server)
        steps = self.steps()
        timeouts = [s.get("timeout_s") for s in steps]
        counter = {"i": 0}

        def dispatch(tool: str, args: dict[str, Any]) -> str:
            from catia_mcp import guard

            i = counter["i"]
            counter["i"] += 1
            limit = timeouts[i] if i < len(timeouts) and timeouts[i] else hang_seconds
            with guard.HangGuard(limit, f"step {i + 1} {tool}", kill=kill) as g:
                out = server.dispatch(tool, args, trace=False)
            if g.fired:
                raise TimeoutError(f"step exceeded {limit:.0f} s (CATIA {'killed' if kill else 'flagged'})")
            return out

        if not dry_run:
            self.ensure_dirs()
        report = batch.run_batch(steps, dispatch, schemas, stop_on_error=stop_on_error, dry_run=dry_run)
        problems: list[str] = []
        if not dry_run and not report.problems and len(report.steps) == len(steps):
            problems = _verify_checks(self.checks_spec(), report.steps)
        result = ScriptResult(report, problems)
        if log and not dry_run:
            result.log_path = self.folder / f"{self.name}.log"
            result.log_path.write_text(result.text(), encoding="utf-8")
        return result


def cli(script: ScriptBase, argv: list[str] | None = None) -> int:
    """``if __name__ == '__main__': sys.exit(cli(script))`` for generated scripts.

    ``--json`` only writes the scenario, ``--dry-run`` validates against the real schemas,
    otherwise the script is run in CATIA (stop at the first error, ``--keep-going`` to continue).
    """
    import argparse

    ap = argparse.ArgumentParser(description=f"{script.kind} script '{script.name}'")
    ap.add_argument("--json", action="store_true", help="only write the scenario JSON")
    ap.add_argument("--dry-run", action="store_true", help="validate against the tool schemas, run nothing")
    ap.add_argument("--keep-going", action="store_true", help="do not stop at the first failing step")
    args = ap.parse_args(argv)
    try:
        js = script.write_json()
    except ScriptError as e:
        print(f"SCRIPT ERROR: {e}")
        return 2
    print(f"{len(script.steps())} steps -> {js}")
    if args.json:
        return 0
    res = script.run(dry_run=args.dry_run, stop_on_error=not args.keep_going)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(res.text())
    return 0 if res.ok else 1
