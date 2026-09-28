"""Batch validation and execution: pure Python, no CATIA."""

from __future__ import annotations

import pytest

from catia_mcp import batch

SCHEMAS = {
    "catia_pad": {
        "type": "object",
        "properties": {
            "height": {"type": "number"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "name": {"type": "string"},
            "boom": {"type": "boolean"},
            "soft_fail": {"type": "boolean"},
        },
        "required": ["height"],
    },
    "catia_fit_all": {"type": "object", "properties": {}},
}


def fake_dispatch(log):
    def dispatch(tool, args):
        log.append((tool, args))
        if args.get("boom"):
            raise RuntimeError("COM exploded")
        if args.get("soft_fail"):
            return "Error in catia_pad: something"
        return f"done {tool}"

    return dispatch


def test_valid_batch_runs_in_order():
    log = []
    report = batch.run_batch(
        [{"tool": "catia_pad", "args": {"height": 10}}, ["catia_fit_all"]],
        fake_dispatch(log),
        SCHEMAS,
    )
    assert report.ok and [t for t, _ in log] == ["catia_pad", "catia_fit_all"]
    assert "SUMMARY: 2 OK, 0 ERR" in report.text()


def test_rejects_before_running_anything():
    log = []
    report = batch.run_batch(
        [
            {"tool": "catia_pad", "args": {"height": 10}},
            {"tool": "catia_pad", "args": {"hieght": 5}},
            {"tool": "catia_padd", "args": {}},
        ],
        fake_dispatch(log),
        SCHEMAS,
    )
    assert not report.ok and log == []  # nothing executed, not even the valid first step
    text = report.text()
    assert "unknown argument 'hieght' (did you mean 'height'?)" in text
    assert "unknown tool 'catia_padd' (did you mean catia_pad" in text
    assert "Nothing was executed" in text


def test_type_and_enum_errors_are_reported():
    problems = batch.validate_batch(
        [("catia_pad", {"height": "ten"}), ("catia_pad", {"height": 1, "direction": "sideways"})],
        SCHEMAS,
    )
    assert any("is not of type 'number'" in p for p in problems)
    assert any("sideways" in p for p in problems)


def test_stop_on_error_and_keep_going():
    steps = [
        {"tool": "catia_pad", "args": {"height": 1, "boom": True}},
        {"tool": "catia_fit_all"},
    ]
    stopped = batch.run_batch(steps, fake_dispatch([]), SCHEMAS)
    assert not stopped.ok and len(stopped.steps) == 1 and stopped.stopped_early
    assert "RuntimeError: COM exploded" in stopped.text()
    assert "NOT run" in stopped.text()

    log = []
    kept = batch.run_batch(steps, fake_dispatch(log), SCHEMAS, stop_on_error=False)
    assert len(kept.steps) == 2 and not kept.ok


def test_soft_failures_count_as_errors():
    report = batch.run_batch(
        [{"tool": "catia_pad", "args": {"height": 1, "soft_fail": True}}],
        fake_dispatch([]),
        SCHEMAS,
    )
    assert not report.ok


def test_dry_run_executes_nothing():
    log = []
    report = batch.run_batch(
        [{"tool": "catia_pad", "args": {"height": 1}}], fake_dispatch(log), SCHEMAS, dry_run=True
    )
    assert report.ok and log == [] and "DRY RUN OK: 1 step" in report.text()


def test_nested_batch_and_malformed_steps():
    schemas = {**SCHEMAS, "catia_batch": {"type": "object"}}
    assert batch.run_batch([{"tool": "catia_batch"}], fake_dispatch([]), schemas).problems
    assert batch.run_batch([], fake_dispatch([]), SCHEMAS).problems
    assert batch.run_batch("not json", fake_dispatch([]), SCHEMAS).problems
    assert batch.run_batch([42], fake_dispatch([]), SCHEMAS).problems
    assert batch.run_batch('[["catia_fit_all"]]', fake_dispatch([]), SCHEMAS).ok


def test_step_limit():
    steps = [["catia_fit_all"]] * (batch.MAX_STEPS + 1)
    assert "too many steps" in batch.run_batch(steps, fake_dispatch([]), SCHEMAS).problems[0]


@pytest.mark.parametrize("text", ["Error in x: y", "Unknown tool: 'z'", "error: no doc"])
def test_failure_markers(text):
    assert batch._looks_failed(text)
