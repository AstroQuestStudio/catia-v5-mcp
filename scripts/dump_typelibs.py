"""Dump CATIA V5 Automation type libraries to text files (interfaces, methods, enums).

Status: ``registry`` mode was run live on CATIA V5 R19 (81 libraries written, among them DRAFTINGITF with 119
types, KnowledgewareTypeLib with 83 and CATMat with 18; no document is opened). ``live`` mode is still
UNVERIFIED-LIVE: it creates temporary documents in the running CATIA, so it was not run on a shared session.

Why: signatures and enum values guessed from memory are the main source of COM bugs. The exact
API of the installed CATIA is in its type libraries; this tool writes one text file per library
so you can ``grep`` it (``rg "AddNewChamfer" <out>``) instead of guessing. Libraries of interest
include ``DRAFTINGITF`` (drawings), ``KnowledgewareTypeLib`` (parameters, relations) and
``CATMat`` (materials), plus the part / product / hybrid-shape libraries.

The dumps describe a proprietary API. They must NOT be committed or published: the default
output folder is the server state folder (see ``catia_mcp.paths.home``), never the repository,
and this script refuses to write inside a git working tree.

Two discovery modes:

* ``registry`` (default, Windows): scans the registered type libraries (HKCR\\TypeLib) and keeps
  those whose name, description or file path matches a pattern (``--match``). Needs no open
  document and does not even need CATIA running.
* ``live``: attaches to a running CATIA, creates temporary Part, Product and Drawing documents,
  and follows the libraries of well-known objects one level deep (what the historical tool did).
  The documents are closed afterwards without saving.

Usage::

    python scripts/dump_typelibs.py                       # registry mode, default folder
    python scripts/dump_typelibs.py D:/catia_dump --match DRAFTINGITF --match CATMat
    python scripts/dump_typelibs.py --mode live
    python scripts/dump_typelibs.py --dry-run             # show what would happen, touch nothing
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Case-insensitive patterns matched against a library's name, description and file path.
DEFAULT_PATTERNS = (
    r"DRAFTINGITF", r"Knowledgeware", r"CATMat", r"INFITF", r"MecMod", r"PARTITF",
    r"ProductStructure", r"HybridShape", r"SPATypeLib", r"CATIA",
)

NOTICE = (
    "# NOTICE: generated from a locally installed CATIA. This describes a proprietary API:\n"
    "# keep it on your machine, do not commit it, do not publish it.\n"
)


# ----------------------------------------------------------------------------- paths

def default_output_dir() -> Path:
    """<server state folder>/catia_api (never inside the repository)."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from catia_mcp.paths import home  # local import: the script also works standalone
        base = home(create=False)
    except Exception:  # pragma: no cover - standalone fallback
        env = os.environ.get("CATIA_MCP_HOME")
        if env:
            base = Path(env)
        elif os.environ.get("APPDATA"):
            base = Path(os.environ["APPDATA"]) / "catia-mcp"
        else:
            base = Path.home() / ".catia-mcp"
    return base / "catia_api"


def inside_git_worktree(path: Path) -> bool:
    """True when ``path`` is inside a directory tree that has a ``.git`` (or is this repo)."""
    p = path.resolve()
    if REPO_ROOT == p or REPO_ROOT in p.parents:
        return True
    for parent in (p, *p.parents):
        if (parent / ".git").exists():
            return True
    return False


# ----------------------------------------------------------------------------- describing

_INVKIND = {1: "", 2: "get ", 4: "put ", 8: "putref "}
_SKIP_METHODS = {"QueryInterface", "AddRef", "Release", "GetTypeInfoCount", "GetTypeInfo",
                 "GetIDsOfNames", "Invoke"}


def _vt_name(pythoncom: Any, tl_or_ti: Any, typedesc: Any) -> str:
    """Best-effort readable type name of a TYPEDESC tuple ``(vt, extra)``."""
    try:
        vt, extra = typedesc[0], typedesc[1]
        names = {getattr(pythoncom, n): n[3:].lower() for n in dir(pythoncom) if n.startswith("VT_")}
        if vt == pythoncom.VT_PTR:
            return "ptr " + _vt_name(pythoncom, tl_or_ti, extra)
        if vt == pythoncom.VT_USERDEFINED:
            return tl_or_ti.GetRefTypeInfo(extra).GetDocumentation(-1)[0]
        if vt == pythoncom.VT_SAFEARRAY:
            return "safearray of " + _vt_name(pythoncom, tl_or_ti, extra)
        return names.get(vt & 0xFFF, f"vt{vt}")
    except Exception:
        return "?"


def describe_type(pythoncom: Any, tl: Any, i: int) -> list[str]:
    """Text lines describing type ``i`` of a type library (enum, interface or coclass)."""
    name = tl.GetDocumentation(i)[0]
    kind = tl.GetTypeInfoType(i)
    ti = tl.GetTypeInfo(i)
    attr = ti.GetTypeAttr()
    lines: list[str] = []
    if kind == pythoncom.TKIND_ENUM:
        vals = []
        for k in range(attr.cVars):
            vd = ti.GetVarDesc(k)
            vals.append(f"{ti.GetNames(vd.memid)[0]}={vd.value}")
        lines.append(f"enum {name}: " + ", ".join(vals))
    elif kind in (pythoncom.TKIND_DISPATCH, pythoncom.TKIND_INTERFACE):
        bases = []
        for k in range(attr.cImplTypes):
            try:
                bases.append(ti.GetRefTypeInfo(ti.GetRefTypeOfImplType(k)).GetDocumentation(-1)[0])
            except Exception:
                pass
        lines.append(f"interface {name}" + (f" : {', '.join(bases)}" if bases else ""))
        seen: set[str] = set()
        for k in range(attr.cFuncs):
            fd = ti.GetFuncDesc(k)
            names = ti.GetNames(fd.memid)
            if names[0] in _SKIP_METHODS:
                continue
            params = []
            for j, pname in enumerate(names[1:]):
                try:
                    typ = _vt_name(pythoncom, ti, fd.args[j][0])
                    params.append(f"{pname}: {typ}")
                except Exception:
                    params.append(pname)
            try:
                ret = _vt_name(pythoncom, ti, fd.rettype)
                ret_txt = f" -> {ret}" if ret not in ("void", "?") else ""
            except Exception:
                ret_txt = ""
            sig = f"    {_INVKIND.get(fd.invkind, '?')}{names[0]}({', '.join(params)}){ret_txt}"
            if sig not in seen:
                seen.add(sig)
                lines.append(sig)
    elif kind == pythoncom.TKIND_COCLASS:
        impl = []
        for k in range(attr.cImplTypes):
            try:
                impl.append(ti.GetRefTypeInfo(ti.GetRefTypeOfImplType(k)).GetDocumentation(-1)[0])
            except Exception:
                pass
        lines.append(f"coclass {name}: " + ", ".join(impl))
    return lines


def write_library(pythoncom: Any, tl: Any, out: Path, label: str = "") -> tuple[str, int]:
    """Write one library to ``out/<name>.txt``. Returns (name, number of types)."""
    name = tl.GetDocumentation(-1)[0]
    doc = tl.GetDocumentation(-1)[1] or ""
    lines = [NOTICE, f"# Type library {name}" + (f" - {doc}" if doc else "") + (f" [{label}]" if label else ""),
             "# Dumped by scripts/dump_typelibs.py", ""]
    for i in range(tl.GetTypeInfoCount()):
        try:
            lines += describe_type(pythoncom, tl, i)
        except Exception as e:  # keep going: one unreadable type must not lose the library
            lines.append(f"# (type {i} unreadable: {e})")
    (out / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return name, tl.GetTypeInfoCount()


# ----------------------------------------------------------------------------- discovery

def registry_libraries(patterns: list[str]) -> list[tuple[str, int, int, str]]:
    """Registered type libraries matching a pattern: (guid, major, minor, description)."""
    import winreg  # Windows only

    regs = [re.compile(p, re.IGNORECASE) for p in patterns]
    found: list[tuple[str, int, int, str]] = []
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "TypeLib") as root:
        n = winreg.QueryInfoKey(root)[0]
        for idx in range(n):
            try:
                guid = winreg.EnumKey(root, idx)
                with winreg.OpenKey(root, guid) as gk:
                    for vidx in range(winreg.QueryInfoKey(gk)[0]):
                        ver = winreg.EnumKey(gk, vidx)
                        with winreg.OpenKey(gk, ver) as vk:
                            desc = winreg.QueryValue(vk, "") or ""
                            path = ""
                            for plat in ("win64", "win32"):
                                try:
                                    path = winreg.QueryValue(vk, f"0\\{plat}")
                                    break
                                except OSError:
                                    continue
                        blob = f"{desc} {path}"
                        if any(r.search(blob) for r in regs):
                            major, _, minor = ver.partition(".")
                            found.append((guid, int(major, 16), int(minor or "0", 16), desc))
            except (OSError, ValueError):
                continue
    return found


def follow_references(pythoncom: Any, libs: dict[str, Any]) -> None:
    """Add the libraries referenced by the given ones (one level)."""
    for tl in list(libs.values()):
        for i in range(tl.GetTypeInfoCount()):
            try:
                ti = tl.GetTypeInfo(i)
                for k in range(ti.GetTypeAttr().cImplTypes):
                    try:
                        ref_tl, _ = ti.GetRefTypeInfo(ti.GetRefTypeOfImplType(k)).GetContainingTypeLib()
                        libs.setdefault(ref_tl.GetDocumentation(-1)[0], ref_tl)
                    except Exception:
                        pass
            except Exception:
                pass


def _try(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:
        return None


def live_libraries(pythoncom: Any, win32com_client: Any) -> tuple[dict[str, Any], Callable[[], None]]:
    """Libraries of well-known objects of a running CATIA. Returns (libs, cleanup)."""
    app = win32com_client.GetActiveObject("CATIA.Application")
    docs = []
    seeds: list[Any] = [app, _try(lambda: app.Documents), _try(lambda: app.Windows),
                        _try(lambda: app.SystemService), _try(lambda: app.FileSystem)]
    part_doc = _try(lambda: app.Documents.Add("Part"))
    prod_doc = _try(lambda: app.Documents.Add("Product"))
    draw_doc = _try(lambda: app.Documents.Add("Drawing"))
    docs = [d for d in (part_doc, prod_doc, draw_doc) if d is not None]
    if part_doc is not None:
        part = part_doc.Part
        seeds += [part_doc, part, part.ShapeFactory, part.HybridShapeFactory, part.MainBody,
                  _try(lambda: part.MainBody.Sketches), _try(lambda: part.Parameters),
                  _try(lambda: part.Relations), _try(lambda: part_doc.GetWorkbench("SPAWorkbench")),
                  _try(lambda: part_doc.Selection),
                  _try(lambda: part.GetItem("CATMatManagerVBExt"))]      # materials (unverified name)
    if prod_doc is not None:
        seeds += [prod_doc.Product, _try(lambda: prod_doc.Product.Products),
                  _try(lambda: prod_doc.Product.Connections("CATIAConstraints"))]
    if draw_doc is not None:
        sheets = _try(lambda: draw_doc.Sheets)
        sheet = _try(lambda: sheets.ActiveSheet)
        views = _try(lambda: sheet.Views)
        view = _try(lambda: views.ActiveView)
        seeds += [draw_doc, sheets, sheet, views, view,
                  _try(lambda: view.Dimensions), _try(lambda: view.Texts),
                  _try(lambda: view.GenerativeBehavior)]
    libs: dict[str, Any] = {}
    for obj in seeds:
        if obj is None:
            continue
        try:
            tl, _ = obj._oleobj_.GetTypeInfo().GetContainingTypeLib()
            libs[tl.GetDocumentation(-1)[0]] = tl
        except Exception:
            pass

    def cleanup() -> None:
        for d in docs:
            _try(d.Close)   # temporary documents, never saved

    return libs, cleanup


# ----------------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out", nargs="?", type=Path,
                   help="output folder (default: <server state folder>/catia_api; never inside a git repo)")
    p.add_argument("--mode", choices=("registry", "live"), default="registry")
    p.add_argument("--match", action="append", metavar="REGEX",
                   help="registry mode: pattern on name/description/path (repeatable; replaces the defaults)")
    p.add_argument("--no-follow", action="store_true", help="do not follow referenced libraries")
    p.add_argument("--dry-run", action="store_true", help="print the plan, touch neither CATIA nor disk")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    out: Path = (args.out or default_output_dir()).expanduser()
    patterns = args.match or list(DEFAULT_PATTERNS)
    if inside_git_worktree(out):
        print(f"refusing to write inside a git working tree: {out}\n"
              "type-library dumps describe a proprietary API and must not be published; "
              "choose a folder outside the repository.", file=sys.stderr)
        return 2
    print(f"mode={args.mode} out={out}")
    if args.mode == "registry":
        print("patterns: " + ", ".join(patterns))
    if args.dry_run:
        print("dry run: nothing done")
        return 0
    if sys.platform != "win32":
        print("this tool needs Windows and pywin32 (COM type libraries)", file=sys.stderr)
        return 1
    import pythoncom
    import win32com.client

    cleanup: Callable[[], None] = lambda: None  # noqa: E731
    libs: dict[str, Any] = {}
    if args.mode == "live":
        libs, cleanup = live_libraries(pythoncom, win32com.client)
    else:
        for guid, major, minor, desc in registry_libraries(patterns):
            try:
                tl = pythoncom.LoadRegTypeLib(guid, major, minor, 0)
                libs.setdefault(tl.GetDocumentation(-1)[0], tl)
            except Exception as e:
                print(f"skip {guid} v{major}.{minor} ({desc}): {e}", file=sys.stderr)
    try:
        if not libs:
            print("no type library found; check --match or that CATIA is installed", file=sys.stderr)
            return 1
        if not args.no_follow:
            follow_references(pythoncom, libs)
        out.mkdir(parents=True, exist_ok=True)
        (out / ".gitignore").write_text("*\n", encoding="utf-8")   # belt and braces
        index = []
        for name, tl in sorted(libs.items()):
            try:
                n, count = write_library(pythoncom, tl, out)
                index.append(f"{n}.txt  ({count} types)")
            except Exception as e:
                index.append(f"{name}: FAILED ({e})")
        (out / "INDEX.txt").write_text(NOTICE + "\n".join(index) + "\n", encoding="utf-8")
        print("\n".join(index))
        for want in ("DRAFTINGITF", "KnowledgewareTypeLib", "CATMat"):
            if not any(name.lower() == want.lower() for name in libs):
                print(f"note: {want} not found among the dumped libraries", file=sys.stderr)
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
