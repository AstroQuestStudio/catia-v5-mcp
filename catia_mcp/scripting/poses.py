"""Component pose tracking, identical to what ``catia_move_component`` does in CATIA.

``catia_move_component`` reads the component's current 3x4 position (three axis vectors and an
origin), rotates the AXES by the world-frame matrix R(rx, ry, rz) (angles in degrees, applied as
Rz * Ry * Rx), then adds (tx, ty, tz) to the ORIGIN. The rotation never moves the origin: a part
rotates about its own origin, and the translation is in world coordinates. The DSL replays the same
arithmetic so it knows where every component was put BEFORE the constraint solver runs, and can
check afterwards that the solver did not throw one of them into a wrong solution.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

MOVE_KEYS = ("tx", "ty", "tz", "rx", "ry", "rz")


def rot_matrix(rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> list[list[float]]:
    """Same matrix as ``AssemblyTools._move`` (degrees in, row-major out)."""
    ax, ay, az = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx, cy, sy, cz, sz = (math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay),
                              math.cos(az), math.sin(az))
    return [
        [cy * cz, cz * sx * sy - cx * sz, sx * sz + cx * cz * sy],
        [cy * sz, cx * cz + sx * sy * sz, cx * sy * sz - cz * sx],
        [-sy, cy * sx, cx * cy],
    ]


@dataclass
class Pose:
    """Position of a component: ``axes`` = its X, Y, Z axes in world coordinates; ``origin`` in mm."""

    axes: list[list[float]] = field(default_factory=lambda: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    origin: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    def moved(self, tx: float = 0.0, ty: float = 0.0, tz: float = 0.0,
              rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> Pose:
        axes = [list(a) for a in self.axes]
        if rx or ry or rz:
            r = rot_matrix(rx, ry, rz)
            axes = [[sum(r[i][k] * a[k] for k in range(3)) for i in range(3)] for a in axes]
        return Pose(axes, [self.origin[0] + tx, self.origin[1] + ty, self.origin[2] + tz])

    def to_json(self) -> dict[str, Any]:
        return {"axes": [[round(v, 9) for v in a] for a in self.axes],
                "origin": [round(v, 9) for v in self.origin]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Pose:
        return cls([list(map(float, a)) for a in d["axes"]], list(map(float, d["origin"])))


def _walk(items: list[dict[str, Any]], out: dict[str, dict[str, Any]]) -> None:
    for x in items:
        out[x["path"]] = x
        _walk(x.get("children", []), out)


def parse_components(output: str | list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``catia_list_components`` output (JSON text or already parsed) -> {instance path: entry}."""
    data = output
    if isinstance(output, str):
        start = output.find("[")
        if start < 0:
            return {}
        try:
            data = json.loads(output[start:])
        except ValueError:
            return {}
    flat: dict[str, dict[str, Any]] = {}
    _walk(data, flat)  # type: ignore[arg-type]
    return flat


def check_poses(expected: dict[str, Pose], components: str | list[dict[str, Any]],
                tol_mm: float = 0.05, tol_axes: float = 1e-3) -> list[str]:
    """Compare the solved positions to the intended ones. Empty list = every component in place."""
    final = parse_components(components)
    if not final:
        return ["no component list found in the output (run list_components after update_assembly)"]
    bad: list[str] = []
    for inst, pose in expected.items():
        x = final.get(inst)
        if x is None:
            bad.append(f"{inst}: missing from the assembly (wrong instance name?)")
            continue
        d = math.dist(x["origin"], pose.origin)
        da = max(math.dist(x["axes"][k], pose.axes[k]) for k in range(3))
        if d > tol_mm or da > tol_axes:
            bad.append(
                f"{inst}: expected origin {[round(v, 3) for v in pose.origin]} axes "
                f"{[[round(v, 3) for v in a] for a in pose.axes]}, got origin {x['origin']} axes "
                f"{x['axes']} (off by {d:.3f} mm). The solver moved it: a constraint has the wrong "
                "sign/orientation or picks the wrong face."
            )
    return bad
