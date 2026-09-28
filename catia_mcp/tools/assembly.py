"""Assembly (Product) tools for CATIA V5 — rewritten and proven live 2026-09-28.

What was wrong before (see _project_meta/CATIA_COM_PITFALLS.md §10):
- constraint type codes were guessed: coincidence used 0 (= Reference/Fix),
  angle used 2 (= coincidence), contact used 3 (= concentricity). The real
  CatConstraintType values were read from CATIA's type library (MECMOD):
  0 Reference(fix), 1 Distance(offset), 2 On(coincidence), 6 Angle,
  20 SurfContact, ...
- constraints received Product objects or feature references instead of
  references built in the ASSEMBLY context. A face of a component must be
  referenced as  "<Root>/<Instance>/!<selection name of the face in its part>",
  and a whole component as "<Root>/<Instance>/!<Root>/<Instance>/".
- Position.GetComponents fills a ByRef array: from Python it stays zeros, so
  every position read was (0,0,0). Read through VBScript instead.
- AddNewProduct("Part") created a Product, not a Part (AddNewComponent is right).

Geometry of a component is designated like in Part Design: a 3D point ON the
face/edge, given in the component's OWN part coordinates (the coordinates of
its drawing / of the CATPart), which stay valid wherever the component sits.
To search a component's topology its part needs a window: the part is opened
with DisplayFileAlerts off (otherwise CATIA pops a modal "open it again?"
dialog that blocks automation — seen live), and the product window is
re-activated right after. CATIA does not close the window of a part used by
an open product; it stays open and is reused by the next designation.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from catia_mcp import geometry
from catia_mcp.connection import CATIAConnection

# CatConstraintType (MECMOD type library)
CST_FIX = 0
CST_OFFSET = 1
CST_COINCIDENCE = 2
CST_ANGLE = 6
CST_CONTACT = 20
CST_NAMES = {0: "fix", 1: "offset", 2: "coincidence", 3: "concentricity", 6: "angle",
             7: "planar_angle", 8: "parallelism", 11: "perpendicularity", 20: "surface_contact",
             21: "line_contact", 22: "point_contact"}
CST_STATUS = {0: "OK", 1: "not satisfied", 2: "wrong orientation/side", 3: "wrong value",
              4: "wrong geometry type", 5: "broken"}

_POSITION_VBS = """
Function CATMain(prod)
  Dim a(11)
  prod.Position.GetComponents a
  CATMain = a
End Function
"""

# Clash between all components (ByRef point coordinates -> VBScript). The Clash object
# is removed afterwards: it would otherwise stay under Applications in the product.
_CLASH_VBS = """
Function CATMain(prod)
  Dim cl: Set cl = prod.GetTechnologicalObject("Clashes")
  Dim c: Set c = cl.Add
  c.ComputationType = 0
  c.InterferenceType = 0
  c.Compute
  Dim confs: Set confs = c.Conflicts
  Dim n: n = confs.Count
  Dim out(): ReDim out(n)
  out(0) = CStr(n)
  Dim i, k
  Dim p(2)
  For i = 1 To n
    Set k = confs.Item(i)
    k.GetFirstPointCoordinates p
    out(i) = k.FirstProduct.Name & "|" & k.SecondProduct.Name & "|" & k.Type & "|" & k.Value & "|" & p(0) & "|" & p(1) & "|" & p(2)
  Next
  cl.Remove cl.Count
  CATMain = out
End Function
"""

_ELEMENT_SCHEMA = {
    "type": "object",
    "description": (
        "Geometry of the component, in the component's OWN part coordinates (mm): "
        "{'face_point': [x,y,z]} a point inside a face; {'axis_point': [x,y,z]} a point "
        "on a cylindrical/conical face, meaning its AXIS; {'edge_point': [x,y,z]}; or "
        "{'plane': 'xy'|'yz'|'zx'} one of the component's origin planes. Use "
        "catia_list_faces on the part to find points, radii and axes."
    ),
    "properties": {
        "face_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "axis_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "edge_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "plane": {"type": "string", "enum": ["xy", "yz", "zx"]},
    },
}


def _bi_schema(extra: dict[str, Any], required_extra: list[str], what: str) -> dict[str, Any]:
    props = {
        "component1": {"type": "string", "description": "Instance name (e.g. 'Engine.1'), or a path 'Sub.1/Part.1' for a sub-assembly child."},
        "element1": _ELEMENT_SCHEMA,
        "component2": {"type": "string", "description": "Second instance name or path."},
        "element2": _ELEMENT_SCHEMA,
        "name": {"type": "string", "description": f"Explicit name for the {what} in the tree (recommended)."},
    }
    props.update(extra)
    return {
        "type": "object",
        "properties": props,
        "required": ["component1", "element1", "component2", "element2", *required_extra],
    }


class AssemblyTools:
    """Tools for assembly (Product) operations in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    # ------------------------------------------------------------------ schema
    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_add_component",
                "description": (
                    "Insert existing CATPart/CATProduct file(s) into the active assembly "
                    "(Insérer un composant existant), optionally inside a sub-assembly. "
                    "Returns the new instance name(s), to use in constraints."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Full path of the .CATPart/.CATProduct."},
                        "parent": {"type": "string", "description": "Sub-assembly path to insert into (default: root product)."},
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_add_new_part",
                "description": (
                    "Create a new empty Part inside the assembly (or a sub-assembly), with "
                    "its part number. It must be modelled and then saved (catia_save_all) "
                    "before faces can be designated for constraints."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part number, e.g. 'Shaft_A'."},
                        "parent": {"type": "string", "description": "Sub-assembly path (default: root)."},
                    },
                    "required": ["part_number"],
                },
            },
            {
                "name": "catia_add_sub_assembly",
                "description": (
                    "Create a new sub-assembly (Product) inside the assembly: the way to "
                    "structure a big assembly (e.g. Wheel / Shaft sub-assemblies)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part number of the sub-assembly."},
                        "parent": {"type": "string", "description": "Parent sub-assembly path (default: root)."},
                    },
                    "required": ["part_number"],
                },
            },
            {
                "name": "catia_duplicate_component",
                "description": (
                    "Insert N more instances of an existing component (same reference, "
                    "like 'Définir une multi-instance'), each shifted by 'step' mm from the "
                    "previous one. Constrain them afterwards."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component": {"type": "string"},
                        "count": {"type": "integer", "minimum": 1},
                        "step": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                                 "description": "[dx, dy, dz] shift between instances (default [0, 0, 50])."},
                    },
                    "required": ["component", "count"],
                },
            },
            {
                "name": "catia_fix_constraint",
                "description": "Fix a component in space (Fixer). The first/reference component of an assembly must be fixed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component": {"type": "string", "description": "Instance name or path."},
                        "name": {"type": "string", "description": "Explicit constraint name (recommended)."},
                    },
                    "required": ["component"],
                },
            },
            {
                "name": "catia_coincidence_constraint",
                "description": (
                    "Coincidence (Coïncidence) between two elements of two components: axis/axis "
                    "(use axis_point on both), plane/plane, face/face, point/axis..."
                ),
                "inputSchema": _bi_schema({}, [], "coincidence"),
            },
            {
                "name": "catia_contact_constraint",
                "description": "Surface contact (Contact) between two faces of two components (type verified: catCstTypeSurfContact = 20).",
                "inputSchema": _bi_schema({}, [], "contact"),
            },
            {
                "name": "catia_offset_constraint",
                "description": "Offset (Décalage) between two planar faces/planes: signed distance in mm (0 = touching).",
                "inputSchema": _bi_schema({
                    "offset": {"type": "number", "description": "Distance in mm."},
                    "orientation": {"type": "string", "enum": ["same", "opposite"],
                                    "description": "Normals same or opposite direction (optional)."},
                }, ["offset"], "offset constraint"),
            },
            {
                "name": "catia_angle_constraint",
                "description": "Angle between two planes/faces/axes of two components, in degrees.",
                "inputSchema": _bi_schema({"angle": {"type": "number", "description": "Angle in degrees."}},
                                          ["angle"], "angle constraint"),
            },
            {
                "name": "catia_update_assembly",
                "description": "Update the assembly (Mise à jour): solves the constraints and moves the components.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "catia_move_component",
                "description": "Translate/rotate a component (before constraining it, to pre-place it).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component": {"type": "string"},
                        "tx": {"type": "number", "default": 0}, "ty": {"type": "number", "default": 0},
                        "tz": {"type": "number", "default": 0},
                        "rx": {"type": "number", "default": 0, "description": "degrees"},
                        "ry": {"type": "number", "default": 0}, "rz": {"type": "number", "default": 0},
                    },
                    "required": ["component"],
                },
            },
            {
                "name": "catia_list_components",
                "description": "Product tree: every component (recursively), its part number, file and position (origin + axes).",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "catia_list_constraints",
                "description": "Every constraint of the root assembly with its type and status (OK / broken...), plus a naming audit.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "catia_clash_analysis",
                "description": (
                    "Interference check of the whole assembly (Analyse > Interférence, DMU Space "
                    "Analysis): lists every CLASH (two components interpenetrate; 'at' = a point "
                    "of the interference, assembly coordinates) and counts the CONTACTS. A correct assembly has 0 "
                    "clash (except intended press fits / cosmetic threads). The analysis object "
                    "is removed afterwards so the product tree stays clean."
                ),
                "inputSchema": {"type": "object", "properties": {
                    "max_list": {"type": "integer", "default": 60, "description": "Max clashes listed."},
                }},
            },
            {
                "name": "catia_clean_display",
                "description": (
                    "Presentation view before screenshots: hide every origin/offset plane, sketch "
                    "and axis system of every part, and the assembly constraint symbols (green "
                    "markers). Display only: nothing is saved (call it AFTER saving)."
                ),
                "inputSchema": {"type": "object", "properties": {
                    "hide_tree": {"type": "boolean", "default": False,
                                  "description": "Also hide the specification tree (window Layout = geometry only), so the "
                                                 "3D view is centred and unobstructed. false (default) shows it again."},
                }},
            },
            {
                "name": "catia_save_all",
                "description": (
                    "Save the whole assembly (Gestion des enregistrements): every new or "
                    "modified part/sub-product is saved into 'folder' as <PartNumber>.CATPart/"
                    ".CATProduct, then the root product as <file_name>. Documents that "
                    "already have a file and are unmodified are left untouched."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "folder": {"type": "string", "description": "Target folder (created if needed)."},
                        "file_name": {"type": "string", "description": "Root product file name, e.g. 'EngineAssembly.CATProduct'."},
                    },
                    "required": ["folder"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        match tool_name:
            case "catia_add_component":
                return self._add_component(arguments)
            case "catia_add_new_part":
                return self._add_new(arguments, "Part")
            case "catia_add_sub_assembly":
                return self._add_new(arguments, "Product")
            case "catia_duplicate_component":
                return self._duplicate(arguments)
            case "catia_fix_constraint":
                return self._fix(arguments)
            case "catia_coincidence_constraint":
                return self._bi_constraint(arguments, CST_COINCIDENCE, "coincidence")
            case "catia_contact_constraint":
                return self._bi_constraint(arguments, CST_CONTACT, "contact")
            case "catia_offset_constraint":
                return self._bi_constraint(arguments, CST_OFFSET, "offset", value=arguments["offset"])
            case "catia_angle_constraint":
                return self._bi_constraint(arguments, CST_ANGLE, "angle", value=arguments["angle"])
            case "catia_update_assembly":
                return self._update()
            case "catia_move_component":
                return self._move(arguments)
            case "catia_list_components":
                return self._list_components()
            case "catia_list_constraints":
                return self._list_constraints()
            case "catia_save_all":
                return self._save_all(arguments)
            case "catia_clash_analysis":
                return self._clash(arguments)
            case "catia_clean_display":
                return self._clean_display(bool(arguments.get("hide_tree")))
            case _:
                raise ValueError(f"Unknown assembly tool: {tool_name}")

    # ----------------------------------------------------------------- helpers
    def _root(self) -> Any:
        return self.conn.get_active_product()

    def _component(self, path: str) -> Any:
        """Resolve 'Inst.1' or 'Sub.1/Inst.1' from the root product."""
        node = self._root()
        for name in [p for p in path.replace("\\", "/").split("/") if p]:
            children = node.Products
            found = None
            for i in range(1, children.Count + 1):
                if children.Item(i).Name == name:
                    found = children.Item(i)
                    break
            if found is None:
                available = [children.Item(i).Name for i in range(1, children.Count + 1)]
                raise RuntimeError(f"Component '{name}' not found under '{node.Name}'. Available: {available}")
            node = found
        return node

    def _instance_path(self, path: str) -> str:
        return f"{self._root().Name}/" + "/".join(p for p in path.replace("\\", "/").split("/") if p)

    def _parent(self, path: str | None) -> Any:
        return self._component(path) if path else self._root()

    def _geometry_name(self, comp: Any, element: dict[str, Any]) -> str:
        """Selection/BRep name of the element inside the component's part."""
        ref_product = comp.ReferenceProduct
        partdoc = ref_product.Parent
        try:
            part = partdoc.Part
        except Exception as e:
            raise RuntimeError(f"'{comp.Name}' is not a part (constrain a part inside it).") from e

        if "plane" in element:
            origin = part.OriginElements
            plane = {"xy": origin.PlaneXY, "yz": origin.PlaneYZ, "zx": origin.PlaneZX}[element["plane"]]
            return plane.Name

        kind, point = (
            ("edge", element["edge_point"]) if "edge_point" in element
            else ("face", element.get("face_point") or element.get("axis_point"))
        )
        if point is None:
            raise ValueError(f"Element must give face_point, axis_point, edge_point or plane: {element}")
        if not partdoc.FullName or not os.path.isfile(partdoc.FullName):
            raise RuntimeError(
                f"Part '{ref_product.PartNumber}' has no file yet: save the assembly "
                "(catia_save_all) before designating its geometry."
            )

        app = self.conn.app
        product_window = app.ActiveWindow
        previous_alerts = app.DisplayFileAlerts
        count_before = app.Documents.Count
        doc = None
        try:
            app.DisplayFileAlerts = False
            # Proven live: with alerts off, Open() of a file already used by the
            # product loads a SECOND copy of it (8 part documents for 3
            # components after a few constraints), and that copy later makes
            # Save As of the real one fail. The copy has the same topology, so
            # the selection name is valid for the product's instance; the copy
            # is closed right after (it is not used by any product, so it can).
            doc = app.Documents.Open(partdoc.FullName)
            ref, _ = geometry.pick_in_part(app, doc, doc.Part, kind, point)
            if "axis_point" in element:
                # A cylindrical FACE gives "wrong geometry type" in a coincidence
                # (proven live); CATIA's own macros reference its axis as
                # "Axis:(<face selection name>)".
                return f"Axis:({ref.DisplayName})"
            return ref.DisplayName
        finally:
            if doc is not None and app.Documents.Count > count_before:
                try:
                    doc.Close()
                except Exception:
                    pass
            app.DisplayFileAlerts = previous_alerts
            try:
                product_window.Activate()
            except Exception:
                pass

    def _reference(self, path: str, element: dict[str, Any]) -> Any:
        comp = self._component(path)
        name = self._geometry_name(comp, element)
        return self._root().CreateReferenceFromName(f"{self._instance_path(path)}/!{name}")

    def _finish_constraint(self, cst: Any, name: str | None, label: str) -> str:
        root = self._root()
        if name:
            try:
                cst.Name = name
            except Exception:
                pass
        root.Update()
        status = CST_STATUS.get(getattr(cst, "Status", -1), "unknown")
        msg = f"{label}: constraint '{cst.Name}' created, status {status}."
        if status != "OK":
            msg += (
                " WARNING: CATIA could not satisfy it — check the elements (wrong face?), "
                "the orientation, or conflicting constraints (catia_list_constraints)."
            )
        return msg

    # ------------------------------------------------------------------- tools
    def _add_component(self, args: dict[str, Any]) -> str:
        path = args["file_path"]
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        parent = self._parent(args.get("parent"))
        before = {parent.Products.Item(i).Name for i in range(1, parent.Products.Count + 1)}
        parent.Products.AddComponentsFromFiles([path], "All")
        new = [parent.Products.Item(i).Name for i in range(1, parent.Products.Count + 1)
               if parent.Products.Item(i).Name not in before]
        return f"Inserted {os.path.basename(path)} into '{parent.Name}' as instance(s): {new}"

    def _add_new(self, args: dict[str, Any], kind: str) -> str:
        parent = self._parent(args.get("parent"))
        comp = parent.Products.AddNewComponent(kind, args["part_number"])
        return f"New {kind} '{comp.PartNumber}' inserted into '{parent.Name}' as instance '{comp.Name}'."

    def _duplicate(self, args: dict[str, Any]) -> str:
        comp = self._component(args["component"])
        parent = comp.Parent.Parent  # Products collection -> owning product
        step = args.get("step") or [0, 0, 50]
        base = self._position(comp)
        created = []
        for k in range(1, int(args["count"]) + 1):
            new = parent.Products.AddComponent(comp.ReferenceProduct)
            m = list(base)
            for j in range(3):
                m[9 + j] += k * step[j]
            new.Position.SetComponents(m)
            created.append(new.Name)
        return f"{len(created)} more instance(s) of '{comp.PartNumber}': {created}"

    def _fix(self, args: dict[str, Any]) -> str:
        path = args["component"]
        inst = self._instance_path(path)
        ref = self._root().CreateReferenceFromName(f"{inst}/!{inst}/")
        cst = self._root().Connections("CATIAConstraints").AddMonoEltCst(CST_FIX, ref)
        return self._finish_constraint(cst, args.get("name"), f"Fix '{path}'")

    def _bi_constraint(self, args: dict[str, Any], cst_type: int, label: str, value: float | None = None) -> str:
        ref1 = self._reference(args["component1"], args["element1"])
        ref2 = self._reference(args["component2"], args["element2"])
        cst = self._root().Connections("CATIAConstraints").AddBiEltCst(cst_type, ref1, ref2)
        if value is not None:
            cst.Dimension.Value = value
        if args.get("orientation"):
            # CatConstraintOrientation: 0 same, 1 opposite
            cst.Orientation = 0 if args["orientation"] == "same" else 1
        text = f"{label.capitalize()} {args['component1']} / {args['component2']}"
        if value is not None:
            text += f" = {value}"
        return self._finish_constraint(cst, args.get("name"), text)

    def _update(self) -> str:
        self._root().Update()
        return "Assembly updated (constraints solved). " + self._constraint_summary()

    def _position(self, comp: Any) -> list[float]:
        return list(geometry.run_vbs(self.conn.app, _POSITION_VBS, [comp]))

    def _move(self, args: dict[str, Any]) -> str:
        comp = self._component(args["component"])
        m = self._position(comp)
        rx, ry, rz = (math.radians(args.get(k, 0)) for k in ("rx", "ry", "rz"))
        if rx or ry or rz:
            cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
            rot = [[cy * cz, cz * sx * sy - cx * sz, sx * sz + cx * cz * sy],
                   [cy * sz, cx * cz + sx * sy * sz, cx * sy * sz - cz * sx],
                   [-sy, cy * sx, cx * cy]]
            # columns of the component's axes are m[0:3], m[3:6], m[6:9]
            axes = [m[0:3], m[3:6], m[6:9]]
            new_axes = [[sum(rot[i][k] * a[k] for k in range(3)) for i in range(3)] for a in axes]
            m[0:9] = new_axes[0] + new_axes[1] + new_axes[2]
        m[9] += args.get("tx", 0)
        m[10] += args.get("ty", 0)
        m[11] += args.get("tz", 0)
        comp.Position.SetComponents(m)
        return f"'{args['component']}' now at origin {[round(v, 3) for v in m[9:12]]}"

    def _list_components(self) -> str:
        def walk(node: Any, path: str) -> list[dict[str, Any]]:
            out = []
            for i in range(1, node.Products.Count + 1):
                comp = node.Products.Item(i)
                p = f"{path}/{comp.Name}" if path else comp.Name
                m = self._position(comp)
                try:
                    file = comp.ReferenceProduct.Parent.FullName
                except Exception:
                    file = None
                item = {
                    "path": p, "part_number": comp.PartNumber, "file": file,
                    "origin": [round(v, 3) for v in m[9:12]],
                    "axes": [[round(v, 4) for v in m[k:k + 3]] for k in (0, 3, 6)],
                }
                children = walk(comp, p) if comp.Products.Count else []
                if children:
                    item["children"] = children
                out.append(item)
            return out

        comps = walk(self._root(), "")
        if not comps:
            return "No components in the active assembly."
        return json.dumps(comps, indent=1, ensure_ascii=False)

    def _constraint_summary(self) -> str:
        cons = self._root().Connections("CATIAConstraints")
        bad = []
        for i in range(1, cons.Count + 1):
            c = cons.Item(i)
            if getattr(c, "Status", 0) != 0:
                bad.append(f"{c.Name} ({CST_STATUS.get(c.Status, c.Status)})")
        return f"{cons.Count} constraint(s), " + (f"NOT OK: {bad}" if bad else "all OK.")

    def _list_constraints(self) -> str:
        from catia_mcp.naming import is_default_name

        cons = self._root().Connections("CATIAConstraints")
        rows = []
        for i in range(1, cons.Count + 1):
            c = cons.Item(i)
            row = {"name": c.Name, "type": CST_NAMES.get(c.Type, c.Type),
                   "status": CST_STATUS.get(c.Status, c.Status)}
            if is_default_name(c.Name):
                row["audit"] = "DEFAULT NAME"
            rows.append(row)
        if not rows:
            return "No constraints in the active assembly."
        return json.dumps({"constraints": rows, "summary": self._constraint_summary()}, indent=1, ensure_ascii=False)

    def _clash(self, args: dict[str, Any]) -> str:
        rows = list(geometry.run_vbs(self.conn.app, _CLASH_VBS, [self._root()]))
        n = int(rows[0])
        clashes, contacts = [], 0
        for r in rows[1:n + 1]:
            first, second, typ, value, x, y, z = str(r).split("|")
            if int(typ) == 0:
                # Conflict.Value: CATIA's own magnitude (not a reliable depth: 66.6 reported for a
                # 0.5 mm radial press fit, seen live) — used only to rank the clashes.
                clashes.append({"between": [first, second], "catia_value": round(abs(float(value.replace(",", "."))), 3),
                                "at": [round(float(c.replace(",", ".")), 1) for c in (x, y, z)]})
            else:
                contacts += 1
        clashes.sort(key=lambda c: -c["catia_value"])
        out = {"clashes": len(clashes), "contacts": contacts,
               "list": clashes[: int(args.get("max_list", 60))]}
        head = ("NO CLASH" if not clashes else f"{len(clashes)} CLASH(ES)") + f", {contacts} contact(s)."
        return head + "\n" + json.dumps(out, ensure_ascii=False, indent=1)

    def _clean_display(self, hide_tree: bool = False) -> str:
        doc = self.conn.app.ActiveDocument
        sel = doc.Selection
        done = {}
        # Language-independent search types (internal names, valid in the French UI too).
        for query, label in (("CATPrtSearch.Plane,all", "planes"), ("CATPrtSearch.Sketch,all", "sketches"),
                             ("CATPrtSearch.AxisSystem,all", "axis systems")):
            try:
                sel.Clear()
                sel.Search(query)
                if sel.Count:
                    done[label] = sel.Count
                    sel.VisProperties.SetShow(1)
            except Exception as e:  # noqa: BLE001 — keep going, report
                done[label] = f"not hidden ({str(e)[:60]})"
        # Constraints of the root AND of every sub-assembly (a sub-product kept its 58
        # green markers when only the root ones were hidden — seen live).
        n_cst = 0
        seen: set[str] = set()

        def hide_constraints(prod: Any) -> None:
            nonlocal n_cst
            try:
                ref = prod.ReferenceProduct
            except Exception:
                ref = prod
            key = getattr(ref, "PartNumber", "") or ref.Name
            if key in seen:
                return
            seen.add(key)
            try:
                cons = ref.Connections("CATIAConstraints")
                if cons.Count:
                    sel.Clear()
                    for i in range(1, cons.Count + 1):
                        sel.Add(cons.Item(i))
                    sel.VisProperties.SetShow(1)
                    n_cst += cons.Count
            except Exception:
                pass
            for i in range(1, ref.Products.Count + 1):
                child = ref.Products.Item(i)
                if child.Products.Count or child.ReferenceProduct.Products.Count:
                    hide_constraints(child)

        try:
            hide_constraints(doc.Product)
        except Exception:
            pass
        if n_cst:
            done["constraints"] = n_cst
        sel.Clear()
        try:
            # CatSpecsAndGeomWindowLayout: 0 specs only, 1 geometry only, 2 both (INFITF).
            # The layout persists on CATIA's window (the user then saw no tree, 2026-09-28):
            # always set it explicitly, tree shown unless asked otherwise.
            self.conn.app.ActiveWindow.Layout = 1 if hide_tree else 2
            done["tree"] = "hidden" if hide_tree else "shown"
        except Exception as e:  # noqa: BLE001
            done["tree"] = f"layout unchanged ({str(e)[:60]})"
        return "Hidden for presentation: " + ", ".join(f"{v} {k}" for k, v in done.items())

    def _save_all(self, args: dict[str, Any]) -> str:
        """Save every document of the assembly INTO `folder` (Save As), children
        first, then the root product.

        Proven live: saving only the root product fails ("les données importées
        ont été modifiées dans la session") as soon as a part is dirty — and
        designating geometry (temporary points) marks parts dirty. Saving every
        document into the target folder is also what the TP asks ("propager le
        répertoire") and never overwrites the original files the parts were
        inserted from. Alerts are off so no modal dialog can block automation.
        """
        folder = os.path.abspath(args["folder"])
        os.makedirs(folder, exist_ok=True)
        app = self.conn.app
        root = self._root()
        root_doc = root.Parent
        saved: list[str] = []
        seen: set[str] = set()

        def target_for(doc: Any, ref: Any) -> str:
            has_file = bool(doc.FullName) and os.path.isfile(doc.FullName)
            if has_file:
                return os.path.join(folder, os.path.basename(doc.FullName))
            ext = ".CATProduct" if doc.Name.endswith(".CATProduct") else ".CATPart"
            return os.path.join(folder, f"{ref.PartNumber}{ext}")

        def walk(node: Any) -> None:
            for i in range(1, node.Products.Count + 1):
                ref = node.Products.Item(i).ReferenceProduct
                doc = ref.Parent
                key = doc.FullName or doc.Name
                if key in seen:
                    continue
                seen.add(key)
                if ref.Products.Count:
                    walk(ref)
                target = target_for(doc, ref)
                if os.path.normcase(doc.FullName or "") == os.path.normcase(target):
                    doc.Save()
                else:
                    check_free(doc, target)
                    save_as(doc, target)
                saved.append(target)

        # A target file already loaded as ANOTHER document makes SaveAs fail
        # with a generic error (proven live) — say it clearly instead.
        loaded = {}
        for i in range(1, app.Documents.Count + 1):
            d = app.Documents.Item(i)
            if d.FullName:
                loaded.setdefault(os.path.normcase(d.FullName), []).append(d)

        def check_free(doc: Any, target: str) -> None:
            others = [d for d in loaded.get(os.path.normcase(target), []) if d.Name != doc.Name or d.FullName != doc.FullName]
            if others:
                raise RuntimeError(
                    f"'{target}' is already open in this session as another document: "
                    "Save As cannot overwrite it. Close the session (catia_close_all) and "
                    "rebuild, or choose another folder."
                )

        def save_as(doc: Any, target: str) -> None:
            # Proven live: SaveAs of a part inside an assembly RAISES a generic
            # error although the file IS written and the document re-pointed
            # (FullName = target, Saved = True). Judge by the result, not by the
            # return code; re-raise only if the file really was not written.
            try:
                doc.SaveAs(target)
            except Exception:
                if not (os.path.isfile(target)
                        and os.path.normcase(doc.FullName or "") == os.path.normcase(target)):
                    raise

        previous_alerts = app.DisplayFileAlerts
        try:
            app.DisplayFileAlerts = False
            walk(root)
            target = os.path.join(folder, args.get("file_name") or f"{root.PartNumber}.CATProduct")
            if os.path.normcase(root_doc.FullName or "") == os.path.normcase(target):
                root_doc.Save()
            else:
                save_as(root_doc, target)
            saved.append(target)
        finally:
            app.DisplayFileAlerts = previous_alerts
        return f"Saved {len(saved)} file(s) into {folder}: {[os.path.basename(s) for s in saved]}"
