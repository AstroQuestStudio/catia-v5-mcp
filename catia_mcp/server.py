"""CATIA V5 MCP Server.

Main entry point. Exposes all CATIA V5 automation tools via the
Model Context Protocol (MCP) for use with Claude Desktop or Claude Code.

Usage:
    python -m catia_mcp.server
    # or
    catia-mcp  (if installed via pip)
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import importlib
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, ToolAnnotations

from catia_mcp import (
    annotations,
    autotrace,
    batch,
    guard,
    naming,
    paths,
    profiles,
    resources,
    safety,
)
from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.bodies import BodyTools
from catia_mcp.tools.boolean import BooleanTools
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.drawing import DrawingTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.meta import MetaTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

# ── Logging ── (rotating file in the state directory, never inside the package)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        RotatingFileHandler(paths.log_file(), maxBytes=2_000_000, backupCount=2, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("catia_mcp")

_FALLBACK_INSTRUCTIONS = (
    "CATIA V5 automation server. Name every feature, sketch and body you create (the 'name' "
    "argument). Verify with the measurement tools instead of assuming. Batch known sequences "
    "with catia_batch. Call catia_lessons before risky operations and record new discoveries "
    "with catia_add_lesson."
)


def build_instructions() -> str:
    """Server-level instructions sent to every client at connection time."""
    try:
        from catia_mcp import lessons

        return lessons.render_instructions()
    except Exception as e:  # never let the knowledge base prevent the server from starting
        logger.warning("lessons unavailable for instructions: %s", e)
        return _FALLBACK_INSTRUCTIONS


# Tools that never need a live CATIA (server-level knowledge tools, drawing analysis).
_OFFLINE_TOOLS = {
    "catia_connect", "catia_disconnect", "catia_lessons", "catia_add_lesson",
    "catia_get_safety_state", "catia_set_safety",
    "catia_batch",  # only orchestrates: each inner step connects on its own when it needs CATIA
    "drawing_extract_geometry", "drawing_render", "drawing_overlay",
}


class CatiaToolError(RuntimeError):
    """A tool failure; the message already carries any matching lesson hint."""


def with_hint(text: str, tool: str) -> str:
    """Append the lesson that explains this error text, if one matches."""
    try:
        from catia_mcp import lessons

        hint = lessons.hint_for_error(text, tool=tool)
    except Exception:
        return text
    return f"{text}\n{hint}" if hint else text


class CATIAMCPServer:
    """MCP Server that bridges Claude to CATIA V5 via COM Automation."""

    def __init__(self) -> None:
        self.server = Server("catia-v5-mcp", instructions=build_instructions())
        self.connection = CATIAConnection()

        # Initialize tool modules with shared connection
        self.document_tools = DocumentTools(self.connection)
        self.sketcher_tools = SketcherTools(self.connection)
        self.part_design_tools = PartDesignTools(self.connection)
        self.body_tools = BodyTools(self.connection)
        self.boolean_tools = BooleanTools(self.connection)
        self.gsd_tools = GSDTools(self.connection)
        self.assembly_tools = AssemblyTools(self.connection)
        self.measurement_tools = MeasurementTools(self.connection)
        self.export_tools = ExportTools(self.connection)
        self.drawing_tools = DrawingTools(self.connection)
        self.meta_tools = MetaTools(self)

        # (module, tool set group) for every tool module; meta stays last.
        registry = [
            (self.document_tools, "document"),
            (self.sketcher_tools, "sketch"),
            (self.part_design_tools, "part"),
            (self.body_tools, "body"),
            (self.boolean_tools, "boolean"),
            (self.gsd_tools, "gsd"),
            (self.assembly_tools, "assembly"),
            (self.measurement_tools, "measure"),
            (self.export_tools, "export"),
            (self.drawing_tools, "drawing"),
        ]
        registry += self._load_optional_modules()
        registry.append((self.meta_tools, "meta"))
        self._tool_modules = [m for m, _ in registry]

        # Build tool name -> module routing table
        self._tool_router: dict[str, Any] = {}
        self._tool_group: dict[str, str] = {}
        for module, group in registry:
            for tool_def in module.get_tool_definitions():
                self._tool_router[tool_def["name"]] = module
                self._tool_group[tool_def["name"]] = group

        # Which groups are advertised in tools/list (hidden tools still work when called).
        self.enabled_groups = profiles.parse(os.environ.get("CATIA_MCP_TOOLSETS"))

        # How much this session may change (read < write < dangerous). It can only be lowered at
        # runtime; raising it needs a human to restart the server with another value.
        self.safety = safety.Gate(safety.parse(os.environ.get("CATIA_MCP_SAFETY")))

        self._setup_handlers()

    # Modules that are registered when their file exists and imports cleanly: a half-written or
    # broken optional module is logged and skipped, it never takes the whole server down.
    _OPTIONAL_MODULES = (
        ("catia_mcp.tools.drafting", "DraftingTools", "drafting"),
        ("catia_mcp.tools.reverse", "ReverseTools", "reverse"),
    )

    def _load_optional_modules(self) -> list[tuple[Any, str]]:
        found: list[tuple[Any, str]] = []
        for module_name, class_name, group in self._OPTIONAL_MODULES:
            try:
                module = importlib.import_module(module_name)
                found.append((getattr(module, class_name)(self.connection), group))
            except ModuleNotFoundError as e:
                if e.name != module_name:
                    logger.warning("optional module %s failed: %s", module_name, e)
            except Exception as e:
                logger.warning("optional module %s skipped: %s", module_name, e)
        return found

    def advertised_tool_definitions(self) -> list[dict[str, Any]]:
        """Tool schemas shown to the client, after the CATIA_MCP_TOOLSETS filter."""
        return [
            d
            for d in self.tool_definitions()
            if profiles.visible(self._tool_group.get(d["name"], "meta"), self.enabled_groups)
        ]

    def _setup_handlers(self) -> None:
        """Register MCP protocol handlers."""

        @self.server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            tools = [
                Tool(
                    name=d["name"],
                    description=d["description"],
                    inputSchema=d["inputSchema"],
                    annotations=ToolAnnotations(**annotations.hints(d["name"])),
                )
                for d in self.advertised_tool_definitions()
            ]
            logger.info("Listed %d tools", len(tools))
            return tools

        @self.server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> list[TextContent]:
            try:
                return [TextContent(type="text", text=self.dispatch(name, arguments or {}))]
            except Exception as e:
                logger.error("Error in %s: %s", name, e, exc_info=True)
                # Raising makes the SDK answer with isError=True (clients can tell a failure
                # from a result); the message already carries any matching lesson hint.
                raise CatiaToolError(f"Error in {name}: {e}") from e

        # Resources (rules, lessons, guides) and prompts (proven workflows): plain text.
        @self.server.list_resources()
        async def handle_list_resources() -> list[types.Resource]:
            return [
                types.Resource(
                    uri=r["uri"], name=r["name"], description=r["description"], mimeType="text/markdown"
                )
                for r in resources.list_resources()
            ]

        @self.server.read_resource()
        async def handle_read_resource(uri: Any) -> list[ReadResourceContents]:
            return [ReadResourceContents(content=resources.read_resource(str(uri)), mime_type="text/markdown")]

        @self.server.list_prompts()
        async def handle_list_prompts() -> list[types.Prompt]:
            return [
                types.Prompt(
                    name=p["name"],
                    description=p["description"],
                    arguments=[types.PromptArgument(**a) for a in p["arguments"]],
                )
                for p in resources.list_prompts()
            ]

        @self.server.get_prompt()
        async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
            text = resources.render_prompt(name, arguments)
            return types.GetPromptResult(
                description=resources.PROMPTS[name]["description"],
                messages=[
                    types.PromptMessage(role="user", content=TextContent(type="text", text=text))
                ],
            )

    def tool_definitions(self) -> list[dict[str, Any]]:
        """All tool schemas, with the server-managed `name` argument injected."""
        defs = []
        self._server_named_tools: set[str] = set()
        for module in self._tool_modules:
            for tool_def in module.get_tool_definitions():
                if naming.inject_name_property(tool_def):
                    self._server_named_tools.add(tool_def["name"])
                defs.append(tool_def)
        return defs

    _VOLUME_TOOLS = {
        "catia_shaft", "catia_groove", "catia_hole", "catia_fillet", "catia_chamfer",
        "catia_boolean_operation", "catia_rect_pattern", "catia_circ_pattern", "catia_mirror",
        "catia_shell", "catia_draft", "catia_thickness",
    }

    def _measure_body(self, name: str, arguments: dict[str, Any]) -> tuple[str, float] | None:
        """(body name, volume mm³) of the body the tool acts on; None if unmeasurable."""
        try:
            part = self.connection.get_active_part()
            body = None
            if name == "catia_boolean_operation" and arguments.get("target_body"):
                bodies = part.Bodies
                for i in range(1, bodies.Count + 1):
                    if bodies.Item(i).Name == arguments["target_body"]:
                        body = bodies.Item(i)
            body = body or self.connection.get_active_part_body()
            if body.Shapes.Count == 0:
                return body.Name, 0.0
            spa = self.connection.active_document.GetWorkbench("SPAWorkbench")
            return body.Name, spa.GetMeasurable(part.CreateReferenceFromObject(body)).Volume * 1e9
        except Exception:
            return None

    def dispatch(self, name: str, arguments: dict[str, Any], trace: bool = True) -> str:
        """Run one tool exactly as Claude does (also used by harness.py)."""
        logger.info("Tool call: %s(%s)", name, arguments)
        module = self._tool_router.get(name)
        if module is None:
            close = difflib.get_close_matches(name, list(self._tool_router), n=3)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            raise CatiaToolError(f"Unknown tool: '{name}'.{hint}")

        if not self.safety.allows(name):
            raise CatiaToolError(self.safety.blocked_message(name))

        if name not in _OFFLINE_TOOLS and not self.connection.is_connected:
            logger.info("Auto-connected: %s", self.connection.connect())
            self._start_guards()

        if not hasattr(self, "_server_named_tools"):
            self.tool_definitions()
        server_named = name in self._server_named_tools
        new_name = arguments.get("name") if server_named else None
        phases: dict[str, float] = {}
        t0 = time.perf_counter()
        before = naming.snapshot(self.connection) if server_named else None
        phases["snapshot_before"] = time.perf_counter() - t0
        tool_args = {k: v for k, v in arguments.items() if not (server_named and k == "name")}

        note = ""
        # Every solid feature reports its volume change: the cheapest proof that it
        # did what the drawing asks (a pattern that copied a 1.7 mm cut instead of
        # 8 through-holes was caught this way). Tools that already report it skip this.
        measure = name in self._VOLUME_TOOLS
        t0 = time.perf_counter()
        body_before = self._measure_body(name, arguments) if measure else None
        phases["measure_before"] = time.perf_counter() - t0
        # View tools must see a live display (a screenshot taken while redraws
        # are frozen would show a stale image).
        live_view = name in (
            "catia_screenshot", "catia_fit_all", "catia_set_view", "catia_export", "catia_batch",
        )
        offline = name in _OFFLINE_TOOLS
        with contextlib.nullcontext() if (live_view or offline) else self.connection.display_batch():
            try:
                kill = paths.env_flag("CATIA_MCP_HANG_KILL", False)
                with guard.HangGuard(guard.hang_seconds(), name, kill=kill):
                    t0 = time.perf_counter()
                    result = module.execute(name, tool_args)
                    phases["execute"] = time.perf_counter() - t0
            except Exception as e:
                message = f"{type(e).__name__}: {e}"
                if server_named and paths.env_flag("CATIA_MCP_CLEANUP_ON_FAILURE", True):
                    # A failed call must not leave a broken feature in the tree.
                    try:
                        cleanup = naming.rollback(self.connection, before)
                    except Exception as ce:
                        cleanup = f"[cleanup] failed: {ce}"
                    if cleanup:
                        message += "\n" + cleanup
                raise CatiaToolError(with_hint(message, name)) from e
            if measure and body_before and "volume change" not in result:
                t0 = time.perf_counter()
                after = self._measure_body(name, arguments)
                phases["measure_after"] = time.perf_counter() - t0
                if after:
                    result += f"\n[check] volume change {after[1] - body_before[1]:+.2f} mm³ in '{after[0]}'."
            if server_named:
                t0 = time.perf_counter()
                try:
                    note = naming.after_creation(self.connection, before, new_name)
                except Exception as e:
                    note = f"[naming] post-processing failed: {e}"
                phases["naming_after"] = time.perf_counter() - t0
        logger.info("Tool result: %s", result[:200] if len(result) > 200 else result)

        if server_named:
            if note:
                result = f"{result}\n{note}"
            if not new_name:
                result += (
                    "\n[naming] WARNING: no 'name' given — this feature keeps a default "
                    "name, which makes the specification tree unreadable and is flagged by "
                    "tree audits. Rename it with catia_rename_feature."
                )

        if batch._looks_failed(result):
            result = with_hint(result, name)

        if paths.env_flag("CATIA_MCP_PROFILE", False):
            # Where does the time of one call go? Set CATIA_MCP_PROFILE=1 to see it per call.
            parts = " ".join(f"{k}={v:.2f}s" for k, v in phases.items() if v >= 0.005)
            overhead = sum(v for k, v in phases.items() if k != "execute")
            line = f"[perf] {name}: total={sum(phases.values()):.2f}s overhead={overhead:.2f}s ({parts})"
            logger.info(line)
            result = f"{result}\n{line}"

        if trace and paths.env_flag("CATIA_MCP_AUTOTRACE", False):
            # Optional project journal (screenshot + CSV line after each feature tool).
            # Never let a tracing failure hide or override the CATIA operation's own result.
            try:
                trace_note = autotrace.trace_after_tool_call(self.connection, name, arguments, result)
            except Exception as e:
                trace_note = f"[auto-trace] unexpected error: {e}"
                logger.warning(trace_note, exc_info=True)
            if trace_note:
                logger.info(trace_note)
                result = f"{result}\n{trace_note}"
        return result

    def _start_guards(self) -> None:
        """Popup watchdog (default on) and cross-process lock (default off), started once."""
        if paths.env_flag("CATIA_MCP_LOCK", False):
            guard.acquire_lock()
        if paths.env_flag("CATIA_MCP_WATCHDOG", True):
            guard.start_watchdog()

    async def run(self) -> None:
        """Run the MCP server over stdio."""
        logger.info("Starting CATIA V5 MCP Server...")
        logger.info("Registered %d tools across %d modules",
                     len(self._tool_router), len(self._tool_modules))

        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


def main() -> None:
    """Entry point for the CATIA V5 MCP Server."""
    server = CATIAMCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
