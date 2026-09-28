"""Scenario that inspects existing CATParts (to prepare assembly constraints): no COM here."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from catia_mcp.scripting._common import ScriptError, make_step, num


def inspect_steps(files: Iterable[str | Path], faces: bool = False, timeout_s: float = 150.0) -> list[dict[str, Any]]:
    """Steps that open each CATPart and read volume / centre of gravity / bounding box (and, with
    ``faces=True``, the face list: type, radius, axis, inside point). NEVER use ``faces`` on a dense part
    (grooved tyre, gear, big pattern): it takes minutes at 100 % CPU. Play the result with the runner:
    ``json.dump(inspect_steps([...]), open("inspect.json", "w"))`` then
    ``python -m catia_mcp.runner inspect.json --keep-going``.
    """
    timeout = num(timeout_s, "timeout_s", positive=True)
    steps: list[dict[str, Any]] = [make_step("catia_close_all", {})]
    n = 0
    for f in files:
        path = Path(f).expanduser()
        if path.suffix.lower() != ".catpart":
            raise ScriptError(f"inspect_steps: {str(f)!r} is not a .CATPart file.")
        n += 1
        steps += [
            make_step("catia_open_document", {"file_path": str(path.resolve())}),
            make_step("catia_get_inertia", {}),
            make_step("catia_get_bounding_box", {}, timeout),
        ]
        if faces:
            steps.append(make_step("catia_list_faces", {}, timeout))
        steps.append(make_step("catia_close_all", {}))
    if not n:
        raise ScriptError("inspect_steps: give at least one .CATPart file.")
    return steps
