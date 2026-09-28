"""Offline tests of ``python -m catia_mcp.runner`` with a fake dispatch/server (no CATIA)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from catia_mcp import guard, runner
from catia_mcp.scripting import PartScript, load_schemas

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def schemas():
    return load_schemas()


class FakeGuard:
    """Stands in for catia_mcp.guard: real HangGuard (never kills), recorded lock/watchdog calls."""

    def __init__(self):
        self.locked = 0
        self.watchdog_started = 0
        self.kills = 0
        outer = self

        class NoKillHangGuard(guard.HangGuard):
            def _fire(self):
                self.fired = True
                if self.kill:
                    outer.kills += 1  # would be `taskkill CNEXT.exe`

        self.HangGuard = NoKillHangGuard

    def acquire_lock(self, *a, **k):
        self.locked += 1
        return True

    def start_watchdog(self, report=None):
        self.watchdog_started += 1
        return True


class Recorder:
    def __init__(self, fail_on=(), outputs=None, sleep=None):
        self.calls = []
        self.fail_on = set(fail_on)  # 1-based call numbers that raise
        self.outputs = outputs or {}
        self.sleep = sleep or {}     # call number -> seconds

    def __call__(self, tool, args):
        n = len(self.calls) + 1
        self.calls.append((tool, args))
        if n in self.sleep:
            time.sleep(self.sleep[n])
        if n in self.fail_on:
            raise RuntimeError(f"boom at {n}")
        return self.outputs.get(tool, f"done {tool}")


STEPS = [
    {"tool": "catia_new_part", "args": {"name": "Demo"}},
    {"tool": "catia_create_sketch", "args": {"plane": "xy", "name": "Sk_Demo"}},
    {"tool": "catia_sketch_circle", "args": {"cx": 0, "cy": 0, "radius": 5}},
    {"tool": "catia_close_sketch", "args": {}},
    {"tool": "catia_pad", "args": {"height": 10, "name": "Demo_Pad_H10"}},
]


def run(steps, dispatch, schemas, tmp_path, **kw):
    lines = []
    kw.setdefault("guard_mod", FakeGuard())
    kw.setdefault("log_path", tmp_path / "scenario.log")
    summary = runner.run_scenario(steps, dispatch, schemas, out=lines.append, **kw)
    return summary, lines


# ── validation happens first ─────────────────────────────────────────────────────────────
def test_invalid_scenario_rejected_before_any_execution(schemas, tmp_path):
    rec = Recorder()
    bad = STEPS[:4] + [{"tool": "catia_pad", "args": {"hieght": 10}}, {"tool": "catia_nope", "args": {}}]
    s, lines = run(bad, rec, schemas, tmp_path)
    assert s.exit_code == runner.EXIT_REJECTED and rec.calls == []
    text = "\n".join(lines)
    assert "SCENARIO REJECTED" in text and "hieght" in text and "did you mean 'height'" in text
    assert "catia_nope" in text
    assert (tmp_path / "scenario.log").read_text(encoding="utf-8").startswith("SCENARIO REJECTED")


def test_lock_and_watchdog_not_started_when_rejected(schemas, tmp_path):
    g = FakeGuard()
    s, _ = run([{"tool": "catia_pad", "args": {}}], Recorder(), schemas, tmp_path, guard_mod=g, lock=True)
    assert s.exit_code == 2 and g.locked == 0 and g.watchdog_started == 0


# ── dry run ──────────────────────────────────────────────────────────────────────────────
def test_dry_run_executes_nothing_and_writes_no_log(schemas, tmp_path):
    rec, g = Recorder(), FakeGuard()
    s, lines = run(STEPS, rec, schemas, tmp_path, dry_run=True, guard_mod=g, lock=True)
    assert s.ok and s.dry_run and rec.calls == []
    assert g.locked == 0 and g.watchdog_started == 0
    assert "DRY RUN OK: 5 step(s)" in lines[0]
    assert not (tmp_path / "scenario.log").exists()


# ── stop at first error / keep going ─────────────────────────────────────────────────────
def test_stops_at_first_error(schemas, tmp_path):
    rec = Recorder(fail_on={3})
    s, lines = run(STEPS, rec, schemas, tmp_path)
    assert s.exit_code == runner.EXIT_STEP_FAILED
    assert len(rec.calls) == 3 and (s.n_ok, s.n_err, s.executed, s.total) == (2, 1, 3, 5)
    assert any("STOPPED at step 3/5" in l for l in lines)


def test_keep_going_runs_every_step(schemas, tmp_path):
    rec = Recorder(fail_on={2, 4})
    s, lines = run(STEPS, rec, schemas, tmp_path, keep_going=True)
    assert len(rec.calls) == 5 and s.n_err == 2 and s.n_ok == 3 and s.exit_code == runner.EXIT_STEP_FAILED
    assert not any("STOPPED" in l for l in lines)


def test_tool_reporting_failure_as_text_counts_as_error(schemas, tmp_path):
    rec = Recorder(outputs={"catia_pad": "Error in catia_pad: sketch not closed"})
    s, _ = run(STEPS, rec, schemas, tmp_path)
    assert s.exit_code == runner.EXIT_STEP_FAILED and s.results[-1].ok is False


# ── hang guard ───────────────────────────────────────────────────────────────────────────
def test_simulated_timeout_is_reported_and_stops_even_with_keep_going(schemas, tmp_path):
    g = FakeGuard()
    rec = Recorder(sleep={2: 0.4})
    s, lines = run(STEPS, rec, schemas, tmp_path, hang_seconds=0.1, keep_going=True, guard_mod=g, kill=True)
    assert s.hung and s.exit_code == runner.EXIT_HANG
    assert g.kills == 1                       # CATIA would have been killed
    assert len(rec.calls) == 2                # a killed CATIA loses its documents: no blind carry on
    assert any("[TIMEOUT]" in l for l in lines)


def test_no_kill_timeout_flags_but_keep_going_continues(schemas, tmp_path):
    g = FakeGuard()
    rec = Recorder(sleep={2: 0.4})
    s, lines = run(STEPS, rec, schemas, tmp_path, hang_seconds=0.1, keep_going=True, guard_mod=g, kill=False)
    assert s.hung and g.kills == 0 and len(rec.calls) == 5 and s.exit_code == runner.EXIT_HANG
    assert any("NOT killed" in l for l in lines)


def test_per_step_timeout_overrides_the_global_one(schemas, tmp_path):
    steps = [dict(s) for s in STEPS]
    steps[1] = {**steps[1], "timeout_s": 5}   # generous: the 0.3 s sleep must not trip it
    rec = Recorder(sleep={2: 0.3})
    s, _ = run(steps, rec, schemas, tmp_path, hang_seconds=0.1, kill=False)
    assert s.ok and not s.hung


def test_legacy_timeout_arg_is_stripped_before_validation_and_dispatch(schemas, tmp_path):
    steps = [["catia_get_bounding_box", {"_timeout_s": 150}]]
    rec = Recorder()
    s, _ = run(steps, rec, schemas, tmp_path)
    assert s.ok and rec.calls == [("catia_get_bounding_box", {})]


# ── log format ───────────────────────────────────────────────────────────────────────────
def test_log_is_written_with_readable_lines_and_summary(schemas, tmp_path):
    rec = Recorder(outputs={"catia_pad": "Pad created\nvolume change +785.40 mm3"}, fail_on={3})
    s, _ = run(STEPS, rec, schemas, tmp_path)
    log = (tmp_path / "scenario.log").read_text(encoding="utf-8").splitlines()
    assert log[0].startswith("[01] OK ") and " catia_new_part {\"name\": \"Demo\"} -> done catia_new_part" in log[0]
    assert log[1].startswith("[02] OK ") and "catia_create_sketch" in log[1]
    assert log[2].startswith("[03] ERR") and "RuntimeError: boom at 3" in log[2]
    assert any(l.startswith("SUMMARY: 2 OK, 1 ERR") and "STOPPED at step 3/5" in l for l in log)
    # multi-line outputs are continued on indented lines
    rec2 = Recorder(outputs={"catia_pad": "Pad created\nvolume change +785.40 mm3"})
    run(STEPS, rec2, schemas, tmp_path)
    log2 = (tmp_path / "scenario.log").read_text(encoding="utf-8").splitlines()
    i = next(k for k, l in enumerate(log2) if l.startswith("[05]"))
    assert log2[i].endswith("-> Pad created") and log2[i + 1] == "      volume change +785.40 mm3"


def test_format_entry():
    line = runner.format_entry(7, True, 1.234, "catia_pad", {"height": 3}, "ok")
    assert line == '[07] OK    1.2s catia_pad {"height": 3} -> ok'


# ── lock / watchdog ──────────────────────────────────────────────────────────────────────
def test_lock_and_watchdog_started_only_when_running(schemas, tmp_path):
    g = FakeGuard()
    s, lines = run(STEPS, Recorder(), schemas, tmp_path, guard_mod=g, lock=True, watchdog=True)
    assert s.ok and g.locked == 1 and g.watchdog_started == 1
    g2 = FakeGuard()
    run(STEPS, Recorder(), schemas, tmp_path, guard_mod=g2, lock=False, watchdog=False)
    assert g2.locked == 0 and g2.watchdog_started == 0


# ── built-in checks from the scenario ────────────────────────────────────────────────────
def test_scenario_checks_are_evaluated_after_the_run(schemas, tmp_path):
    p = PartScript("Disc", tmp_path / "Disc")
    with p.sketch("xy", "Sk_Disc") as sk:
        sk.circle(0, 0, 10)
    p.pad("Disc_D20_T5", 5)
    p.save(views=())
    p.checks(volume=1570.8, bbox=(20, 20, 5))
    scen = p.scenario()
    outs = {"catia_pad": "volume change +1570.80 mm3", "catia_get_inertia": json.dumps({"volume_mm3": 1570.8}),
            "catia_get_bounding_box": json.dumps({"size": [20, 20, 5]}), "catia_get_tree": "AUDIT: OK"}
    s, lines = run(scen["steps"], Recorder(outputs=outs), schemas, tmp_path, checks=scen["checks"])
    assert s.ok and any("CHECKS: all passed" in l for l in lines)
    outs["catia_get_inertia"] = json.dumps({"volume_mm3": 1000.0})
    s2, lines2 = run(scen["steps"], Recorder(outputs=outs), schemas, tmp_path, checks=scen["checks"])
    assert s2.exit_code == runner.EXIT_CHECKS and any("volume" in l for l in lines2)


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
class FakeServer:
    def __init__(self, schemas, recorder=None):
        self._schemas = schemas
        self.rec = recorder or Recorder()

    def tool_definitions(self):
        return [{"name": n, "inputSchema": s} for n, s in self._schemas.items()]

    def dispatch(self, name, args, trace=False):
        assert trace is False
        return self.rec(name, args)


def write(tmp_path, data, name="scenario.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cli_run_writes_log_next_to_scenario(schemas, tmp_path, capsys):
    path = write(tmp_path, {"steps": STEPS})
    srv = FakeServer(schemas)
    rc = runner.main([str(path), "--hang-seconds", "30", "--no-kill"], server_factory=lambda: srv, guard_mod=FakeGuard())
    assert rc == 0 and len(srv.rec.calls) == 5
    assert (tmp_path / "scenario.log").is_file()
    assert "SUMMARY: 5 OK, 0 ERR" in capsys.readouterr().out


def test_cli_dry_run_and_exit_codes(schemas, tmp_path, capsys):
    path = write(tmp_path, [[s["tool"], s["args"]] for s in STEPS])
    srv = FakeServer(schemas)
    assert runner.main([str(path), "--dry-run"], server_factory=lambda: srv, guard_mod=FakeGuard()) == 0
    assert srv.rec.calls == []
    bad = write(tmp_path, [["catia_pad", {}]], "bad.json")
    assert runner.main([str(bad), "--dry-run"], server_factory=lambda: srv, guard_mod=FakeGuard()) == 2
    failing = FakeServer(schemas, Recorder(fail_on={2}))
    assert runner.main([str(path)], server_factory=lambda: failing, guard_mod=FakeGuard()) == 1
    assert runner.main([str(tmp_path / "missing.json")], server_factory=lambda: srv) == 2
    keep = FakeServer(schemas, Recorder(fail_on={2}))
    rc = runner.main([str(path), "--keep-going"], server_factory=lambda: keep, guard_mod=FakeGuard())
    assert rc == 1 and len(keep.rec.calls) == 5
    capsys.readouterr()


def test_cli_lock_flag(schemas, tmp_path):
    path = write(tmp_path, {"steps": STEPS})
    g = FakeGuard()
    rc = runner.main([str(path), "--lock", "--no-kill"], server_factory=lambda: FakeServer(schemas), guard_mod=g)
    assert rc == 0 and g.locked == 1


def test_schema_subcommand(schemas, capsys):
    srv = FakeServer(schemas)
    assert runner.main(["schema", "catia_pad", "hole"], server_factory=lambda: srv) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("catia_pad ") and json.loads(out[0].split(" ", 1)[1])["required"] == ["height"]
    assert out[1].startswith("catia_hole ") and "diameter" in out[1]
    assert runner.main(["schema", "catia_pda"], server_factory=lambda: srv) == 1
    assert "did you mean catia_pad" in capsys.readouterr().out
    assert runner.main(["schema", "--list"], server_factory=lambda: srv) == 0
    assert "catia_pad" in capsys.readouterr().out.splitlines()


def test_load_scenario_shapes(tmp_path):
    raw, checks = runner.load_scenario(write(tmp_path, {"steps": STEPS, "checks": {"audit": {"step": 1}}}))
    assert raw == STEPS and checks == {"audit": {"step": 1}}
    raw, checks = runner.load_scenario(write(tmp_path, STEPS, "list.json"))
    assert raw == STEPS and checks == {}
    with pytest.raises(ValueError):
        runner.load_scenario(write(tmp_path, {"nosteps": 1}, "x.json"))


# ── the shipped example ──────────────────────────────────────────────────────────────────
def test_shipped_scenario_example_is_valid_against_the_real_schemas(schemas, tmp_path):
    path = REPO / "templates" / "scenario_example.json"
    raw, checks = runner.load_scenario(path)
    assert checks and raw[0]["tool"] == "catia_close_all"
    rec = Recorder()
    s, lines = run(raw, rec, schemas, tmp_path, dry_run=True)
    assert s.ok, lines
    text = path.read_text(encoding="utf-8")
    for forbidden in ("C:\\\\Users", "McMaster"):
        assert forbidden not in text


def test_module_entry_point_lists_help():
    ap = runner.build_parser()
    ns = ap.parse_args(["s.json", "--keep-going", "--dry-run", "--hang-seconds", "12", "--no-kill", "--lock"])
    assert ns.keep_going and ns.dry_run and ns.hang_seconds == 12 and ns.no_kill and ns.lock
    assert SimpleNamespace  # keep import used
