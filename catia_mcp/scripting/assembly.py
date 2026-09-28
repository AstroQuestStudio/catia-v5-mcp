"""AssemblyScript: a DSL that EMITS the tool calls needed to assemble parts (no COM, no CATIA).

Proven method, encoded as checks:

1. ``add`` every component, ``fix`` the reference part (the biggest one, which carries the others);
2. PRE-POSITION each other component with ``move`` (the DSL tracks the pose exactly like
   ``catia_move_component``), THEN constrain it: the solver has almost nothing to do and cannot jump
   to a wrong solution;
3. prefer ``contact`` (opposite normals) and ``coincidence`` of real faces (coplanarity) or axes
   (coaxiality). A distance between planes has NO reliable sign: ``offset`` therefore requires an
   explicit ``orientation``;
4. every constraint has an explicit name (``Coax_A_B``, ``Contact_A_B``, ``Dist_A_B_5``, ``Fix_A``);
5. ``finish`` = update, constraint status, component list (poses), clash analysis, save all,
   screenshots; ``verify_poses`` / the run checks compare the solved poses to the intended ones.

Geometry is designated in each part's OWN coordinates: ``axis(x, y, z)`` a point ON a cylindrical face
(= its axis), ``face(x, y, z)`` a point strictly INSIDE a planar face, ``edge(x, y, z)`` a point on an
edge, ``origin_plane("xy")`` one of the part's origin planes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from catia_mcp.scripting._base import ScriptBase
from catia_mcp.scripting._common import ScriptError, check_name, integer, num, one_of, point3
from catia_mcp.scripting.poses import MOVE_KEYS, Pose, check_poses

_ELEMENT_KEYS = {"axis_point": "axis", "face_point": "face", "edge_point": "edge", "plane": "plane"}
_ALIASES = {"axis": "axis_point", "axe": "axis_point", "face": "face_point", "edge": "edge_point",
            "arete": "edge_point", "plane": "plane", "plan": "plane"}
_INSTANCE_RE = re.compile(r"^[^/\\]+\.\d+$")
DEFAULT_VIEWS = ("isometric", "isometric_back")


def axis(x: float, y: float, z: float) -> dict[str, Any]:
    """A point ON a cylindrical/conical face of the part (exactly at its radius): designates its AXIS."""
    return {"axis_point": point3([x, y, z], "axis")}


def face(x: float, y: float, z: float) -> dict[str, Any]:
    """A point strictly INSIDE a planar face (not on its border, not over a hole). 53.99 for a face at 54 fails."""
    return {"face_point": point3([x, y, z], "face")}


def edge(x: float, y: float, z: float) -> dict[str, Any]:
    """A point ON an edge."""
    return {"edge_point": point3([x, y, z], "edge")}


def origin_plane(kind: str) -> dict[str, Any]:
    """One of the part's origin planes: "xy", "yz" or "zx"."""
    return {"plane": one_of(kind, "origin_plane", ("xy", "yz", "zx"))}


def _element(e: Any, what: str) -> tuple[dict[str, Any], str]:
    """Normalise an element to (schema dict, kind in axis/face/edge/plane)."""
    if isinstance(e, (tuple, list)) and len(e) == 2 and isinstance(e[0], str):
        key = _ALIASES.get(e[0].lower())
        if key is None:
            raise ScriptError(f"{what}: unknown element kind {e[0]!r} (axis, face, edge, plane).")
        e = {key: e[1]}
    if not isinstance(e, dict) or len(e) != 1 or next(iter(e)) not in _ELEMENT_KEYS:
        raise ScriptError(f"{what}: expected axis(...), face(...), edge(...) or origin_plane(...), got {e!r}.")
    key, val = next(iter(e.items()))
    if key == "plane":
        return {"plane": one_of(val, f"{what}.plane", ("xy", "yz", "zx"))}, "plane"
    return {key: point3(val, f"{what}.{key}")}, _ELEMENT_KEYS[key]


class AssemblyScript(ScriptBase):
    """Assemble parts. ``AssemblyScript("Gearbox", "out/Gearbox")``; the product is saved as
    ``<folder>/<name>.CATProduct`` together with a copy of every part (originals stay untouched)."""

    kind = "assembly"

    def __init__(self, name: str, folder: str | Path, strict_paths: bool = False, close_all: bool = True) -> None:
        super().__init__(check_name(name, "assembly name"), folder)
        self.strict_paths = strict_paths
        self._instances: dict[str, dict[str, Any]] = {}
        self._counters: dict[tuple[str, str], int] = {}
        self._poses: dict[str, Pose] = {}
        self._fixed: dict[str, Pose] = {}
        self._constrained: set[str] = set()
        self._cnames: set[str] = set()
        self._solve_step: int | None = None
        self._components_step: int | None = None
        self._clash_step: int | None = None
        self._allowed_clashes: list[list[str]] = []
        self._saved = False
        if close_all:
            self._emit("catia_close_all", {})
        self._emit("catia_new_product", {"name": name})

    # ── components ──
    def add(self, part: str | Path, instance_name: str | None = None, *, part_number: str | None = None,
            parent: str | None = None) -> str:
        """Insert a CATPart/CATProduct and return its INSTANCE name (``<PartNumber>.<k>``, ``k`` counting
        the copies of that part). CATIA chooses that name: by default it is predicted from the file name;
        for parts whose PartNumber differs from their file name give ``part_number=`` or the exact
        ``instance_name=`` (see catia_list_components). ``parent`` = a sub-product instance to insert into."""
        path = Path(part).expanduser()
        if path.suffix.lower() not in (".catpart", ".catproduct"):
            raise ScriptError(f"add: {str(part)!r} is not a .CATPart or .CATProduct file.")
        if self.strict_paths and not path.is_file():
            raise ScriptError(f"add: file not found: {path}")
        if parent is not None and parent not in self._instances:
            raise ScriptError(f"add: unknown parent {parent!r} (known: {', '.join(self._instances) or 'none'}).")
        pn = part_number or path.stem
        key = (parent or "", pn)
        if instance_name is None:
            k = self._counters.get(key, 0) + 1
            local = f"{pn}.{k}"
        else:
            local = instance_name.split("/")[-1]
            if not _INSTANCE_RE.match(local):
                raise ScriptError(f"add: instance name {instance_name!r} must look like '<PartNumber>.<k>'.")
            k = self._counters.get(key, 0) + 1
        self._counters[key] = max(self._counters.get(key, 0), k)
        full = f"{parent}/{local}" if parent else local
        if full in self._instances:
            raise ScriptError(f"add: instance {full!r} already exists (CATIA numbers copies .1, .2, ...).")
        args: dict[str, Any] = {"file_path": str(path.resolve())}
        if parent:
            args["parent"] = parent
        # UNVERIFIED-LIVE: predicted instance name "<PartNumber>.<k>" (proven for parts whose PartNumber is
        # the file name); insertion with `parent=` into a sub-product.
        self._emit("catia_add_component", args)
        self._instances[full] = {"file": args["file_path"], "part_number": pn, "parent": parent}
        if not parent:
            self._poses[full] = Pose()
        return full

    def _known(self, inst: str, what: str) -> str:
        if not isinstance(inst, str) or not inst:
            raise ScriptError(f"{what}: instance name required.")
        head = inst.split("/")[0]
        if inst not in self._instances and not (head in self._instances and "/" in inst):
            raise ScriptError(f"{what}: unknown instance {inst!r}. Instances: "
                              f"{', '.join(self._instances) or 'none (call add first)'}.")
        return inst

    # ── pose ──
    def pose(self, instance: str) -> Pose:
        """The intended pose of a top-level instance (as ``catia_move_component`` would leave it)."""
        self._known(instance, "pose")
        if instance not in self._poses:
            raise ScriptError(f"pose: {instance!r} is not a top-level instance; poses are tracked for those only.")
        return self._poses[instance]

    def move(self, instance: str, tx: float = 0.0, ty: float = 0.0, tz: float = 0.0, rx: float = 0.0,
             ry: float = 0.0, rz: float = 0.0, *, pose: dict[str, float] | None = None) -> Pose:
        """Pre-position ``instance`` BEFORE constraining it: rotate its axes by (rx, ry, rz) degrees about
        its own origin (world axes, Rz*Ry*Rx), then translate its origin by (tx, ty, tz) mm in world
        coordinates. Several calls compose. ``pose={"tx": .., "rz": ..}`` is a shortcut for the keywords."""
        self._known(instance, "move")
        if instance not in self._poses:
            raise ScriptError("move: only top-level instances can be pre-positioned.")
        vals = dict(zip(MOVE_KEYS, (tx, ty, tz, rx, ry, rz)))
        if pose:
            unknown = set(pose) - set(MOVE_KEYS)
            if unknown:
                raise ScriptError(f"move: unknown pose key(s) {sorted(unknown)}; use {MOVE_KEYS}.")
            vals.update(pose)
        vals = {k: num(v, f"move.{k}") for k, v in vals.items()}
        args = {k: v for k, v in vals.items() if v}
        if not args:
            raise ScriptError("move: nothing to do (all values are 0).")
        if instance in self._fixed:
            raise ScriptError(f"move: {instance!r} is already fixed; pre-position BEFORE fix().")
        if instance in self._constrained:
            raise ScriptError(f"move: {instance!r} already has constraints; pre-position BEFORE constraining it.")
        self._emit("catia_move_component", {"component": instance, **args})
        self._poses[instance] = self._poses[instance].moved(**vals)
        return self._poses[instance]

    def expected_poses(self) -> dict[str, Pose]:
        """Instances that were pre-positioned or fixed, with the pose the solver must leave them in."""
        moved = {i: p for i, p in self._poses.items() if p != Pose()}
        return {**moved, **self._fixed}

    # ── constraints ──
    def _cname(self, name: str) -> str:
        check_name(name, "constraint name", self._cnames)
        self._cnames.add(name)
        return name

    def fix(self, instance: str, name: str) -> None:
        """Fix a component in space (the reference part). Do it before constraining the others."""
        self._known(instance, "fix")
        if instance in self._fixed:
            raise ScriptError(f"fix: {instance!r} is already fixed.")
        self._cname(name)
        self._emit("catia_fix_constraint", {"component": instance, "name": name})
        self._fixed[instance] = self._poses.get(instance, Pose())
        self._constrained.add(instance)

    def _pair(self, tool: str, i1: str, e1: Any, i2: str, e2: Any, name: str, allowed: dict[str, set[str]],
              extra: dict[str, Any] | None = None) -> None:
        self._known(i1, tool)
        self._known(i2, tool)
        if i1 == i2:
            raise ScriptError(f"{tool}: both elements belong to {i1!r}; constrain two different components.")
        d1, k1 = _element(e1, f"{tool}.element1")
        d2, k2 = _element(e2, f"{tool}.element2")
        if k2 not in allowed.get(k1, set()):
            raise ScriptError(
                f"{tool}: cannot pair a {k1} with a {k2} "
                f"(a {k1} pairs with: {', '.join(sorted(allowed.get(k1, set()))) or 'nothing'}). "
                "Axes go with axes (coaxial), planar faces with planar faces."
            )
        self._cname(name)
        args = {"component1": i1, "element1": d1, "component2": i2, "element2": d2, "name": name}
        args.update(extra or {})
        self._emit(f"catia_{tool}_constraint", args)
        self._constrained.update((i1, i2))

    _PLANAR = {"face": {"face", "plane"}, "plane": {"face", "plane"}}

    def coincidence(self, i1: str, e1: Any, i2: str, e2: Any, name: str) -> None:
        """Coaxial (axis + axis) or coplanar (planar face/plane + planar face/plane)."""
        self._pair("coincidence", i1, e1, i2, e2, name,
                   {**self._PLANAR, "axis": {"axis"}, "edge": {"edge"}})

    def contact(self, i1: str, e1: Any, i2: str, e2: Any, name: str) -> None:
        """Face-to-face contact with OPPOSITE normals (parts touch, on opposite sides)."""
        # UNVERIFIED-LIVE: contact between origin planes is refused here (only real faces were proven)
        self._pair("contact", i1, e1, i2, e2, name, {"face": {"face"}})

    def offset(self, i1: str, e1: Any, i2: str, e2: Any, value: float, name: str, *,
               orientation: str | None = None, allow_unsigned: bool = False) -> None:
        """Distance ``value`` mm between two planar faces/planes (or two axes). A distance has no reliable
        sign: ``orientation`` ("same" / "opposite" normals) is REQUIRED, and the run checks the result.
        Prefer contact/coincidence on real faces whenever the geometry offers one."""
        v = num(value, "offset.value", nonzero=True)
        if orientation is None:
            if not allow_unsigned:
                raise ScriptError("offset: orientation='same'|'opposite' is required (an unsigned distance "
                                  "let a solver stack two wheels on the same side while every constraint said OK). "
                                  "Or use contact()/coincidence(), or pass allow_unsigned=True to accept the risk.")
        else:
            one_of(orientation, "offset.orientation", ("same", "opposite"))
        extra: dict[str, Any] = {"offset": v}
        if orientation:
            extra["orientation"] = orientation
        self._pair("offset", i1, e1, i2, e2, name, {**self._PLANAR, "axis": {"axis"}}, extra)

    def angle(self, i1: str, e1: Any, i2: str, e2: Any, value: float, name: str) -> None:
        """Angle in degrees between two planar faces/planes, axes or edges."""
        v = num(value, "angle.value", lo=-360.0, hi=360.0)
        # UNVERIFIED-LIVE: angle constraint (not used in the reference assemblies; schema-valid only)
        self._pair("angle", i1, e1, i2, e2, name,
                   {"face": {"face", "plane"}, "plane": {"face", "plane"}, "axis": {"axis", "edge"},
                    "edge": {"axis", "edge"}}, {"angle": v})

    # ── solve, verify, save ──
    def update(self) -> int:
        """Solve the constraints; the output says ``all OK`` or lists the broken ones."""
        self._solve_step = self._emit("catia_update_assembly", {})
        return self._solve_step

    def list_constraints(self) -> int:
        return self._emit("catia_list_constraints", {})

    def list_components(self) -> int:
        """Instance names and solved positions: the input of ``verify_poses`` and of the run checks."""
        self._components_step = self._emit("catia_list_components", {})
        return self._components_step

    def clash_analysis(self, max_list: int = 60, timeout_s: float = 900.0,
                       allow: Iterable[Sequence[str]] = ()) -> int:
        """Interference analysis between all components. ``allow`` = pairs of instance names (substrings)
        that may interpenetrate on purpose (press fits). Anything else fails the checks."""
        ml = integer(max_list, "clash_analysis.max_list")
        for pair in allow:
            if len(pair) != 2:
                raise ScriptError("clash_analysis.allow: pairs of two instance names.")
            self._allowed_clashes.append([str(pair[0]), str(pair[1])])
        self._clash_step = self._emit("catia_clash_analysis", {"max_list": ml},
                                      timeout_s=num(timeout_s, "timeout_s", positive=True))
        return self._clash_step

    def save_all(self, file_name: str | None = None) -> Path:
        """Save the product and a copy of every part/sub-product into ``folder``."""
        fn = file_name or f"{self.name}.CATProduct"
        if not fn.lower().endswith(".catproduct"):
            raise ScriptError("save_all: file_name must end with .CATProduct")
        self._emit("catia_save_all", {"folder": str(self.folder), "file_name": fn})
        self._saved = True
        return self.folder / fn

    def clean_display(self, hide_tree: bool = False) -> int:
        """Hide planes/sketches/axes (and the tree with ``hide_tree``). Display only: do it AFTER save_all."""
        return self._emit("catia_clean_display", {"hide_tree": True} if hide_tree else {})

    def screenshot(self, view: str, index: int, mode: str = "window") -> None:
        one_of(view, "screenshot.view", ("front", "back", "top", "bottom", "left", "right", "isometric",
                                         "isometric_back", "isometric_left", "isometric_right"))
        one_of(mode, "screenshot.mode", ("window", "viewer"))
        self._emit("catia_set_view", {"view": view})
        self._emit("catia_fit_all", {})
        self._emit("catia_screenshot", {"file_path": str(self.folder / "screens" / f"{index:02d}_{view}.png"),
                                        "mode": mode})

    def finish(self, views: Sequence[str] = DEFAULT_VIEWS, clash: bool = True,
               allow_clashes: Iterable[Sequence[str]] = (), file_name: str | None = None) -> Path:
        """Standard ending: update, constraint status, poses, clash analysis, save_all, clean display,
        then one screenshot per view (the first shows the tree, the next ones do not)."""
        if not self._instances:
            raise ScriptError("finish: the assembly is empty.")
        self.update()
        self.list_constraints()
        self.list_components()
        if clash:
            self.clash_analysis(allow=allow_clashes)
        target = self.save_all(file_name)
        self.clean_display()
        for i, v in enumerate(views, 1):
            if i == 2:
                self.clean_display(hide_tree=True)
            self.screenshot(v, i, "window" if i == 1 else "viewer")
        if len(views) >= 2:
            self.clean_display(hide_tree=False)  # the setting sticks to CATIA's window: restore the tree
        return target

    def verify_poses(self, components_output: str | list[dict[str, Any]], tol: float = 0.05) -> list[str]:
        """Compare the solved positions (``catia_list_components`` output) with the intended poses."""
        return check_poses(self.expected_poses(), components_output, tol)

    def _before_finish(self) -> None:
        pass

    def checks_spec(self) -> dict[str, Any]:
        spec: dict[str, Any] = {}
        if self._solve_step:
            spec["solve"] = {"step": self._solve_step}
        if self._components_step:
            spec["components"] = {
                "step": self._components_step,
                "instances": [i for i, v in self._instances.items() if not v["parent"]],
                "poses": {i: p.to_json() for i, p in self.expected_poses().items()},
            }
        if self._clash_step:
            spec["clash"] = {"step": self._clash_step, "allowed": self._allowed_clashes}
        return spec
