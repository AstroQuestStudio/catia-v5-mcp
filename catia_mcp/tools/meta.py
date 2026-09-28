"""Server-level tools: batch execution and the lessons ledger.

These tools do not touch CATIA themselves; they orchestrate the other tools (``catia_batch``)
or expose the accumulated knowledge base to the agent (``catia_lessons``, ``catia_add_lesson``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from catia_mcp import batch

if TYPE_CHECKING:
    from catia_mcp.server import CATIAMCPServer


class MetaTools:
    def __init__(self, server: CATIAMCPServer) -> None:
        self.server = server

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_batch",
                "description": (
                    "Run MANY catia_* tool calls in ONE round trip (10-100x fewer LLM turns). "
                    "Every step is validated against the tool schemas BEFORE anything runs "
                    "(unknown tool, misspelt/missing argument, wrong type, bad enum value): a "
                    "typo in step 31 is reported up front and CATIA is left untouched. Steps then "
                    "run in order and stop at the first failure (stop_on_error). Use dry_run=true "
                    "to only validate. Compact per-step report with timings. Not nestable. "
                    "Prefer it for any sequence you already know (sketch -> pad -> rename -> "
                    "check), and keep separate calls for steps that depend on a value you must "
                    "read first (e.g. face points from catia_list_faces)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": (
                                "Ordered calls: [{\"tool\": \"catia_create_sketch\", "
                                "\"args\": {\"plane\": \"xy\", \"name\": \"Sketch_Base\"}}, ...]"
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "tool": {"type": "string"},
                                    "args": {"type": "object"},
                                },
                                "required": ["tool"],
                            },
                        },
                        "stop_on_error": {
                            "type": "boolean",
                            "description": "Stop at the first failing step (default true).",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Only validate every step; execute nothing (default false).",
                        },
                    },
                    "required": ["steps"],
                },
            },
            {
                "name": "catia_lessons",
                "description": (
                    "Search the knowledge base of proven CATIA V5 automation pitfalls and rules "
                    "(each one verified live). Call it BEFORE a risky operation (topology "
                    "selection, boolean ops, assembly constraints, measurements) or right after "
                    "an unexplained COM error. With no query it returns the critical rules."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keywords, e.g. 'pocket direction' or 'UpdateObject'."},
                        "area": {
                            "type": "string",
                            "description": "Filter: com, sketch, part, boolean, topology, measure, assembly, display, process, drawing, performance.",
                        },
                        "tool": {"type": "string", "description": "Filter by tool name, e.g. catia_pocket."},
                        "limit": {"type": "integer", "description": "Max entries (default 15)."},
                    },
                },
            },
            {
                "name": "catia_add_lesson",
                "description": (
                    "Record a NEW pitfall or rule you discovered so that every future session "
                    "(yours and other agents') is warned automatically. Use it as soon as you "
                    "find something that failed, surprised you, or needed a workaround. Give the "
                    "exact observed error text in error_patterns (regex) so the hint is attached "
                    "to that error next time, and state how it was proven. Stored in the user's "
                    "lessons file (not in the package); duplicates are refused."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "One-line summary of the pitfall."},
                        "rule": {"type": "string", "description": "Imperative, actionable rule (what to do / avoid)."},
                        "area": {"type": "string", "description": "com|sketch|part|boolean|topology|measure|assembly|display|process|drawing|performance"},
                        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                        "symptom": {"type": "string", "description": "What was observed."},
                        "cause": {"type": "string", "description": "Root cause, if known."},
                        "error_patterns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Regexes matching the error text this lesson explains.",
                        },
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "proof": {"type": "string", "description": "How it was verified (what was tried, what worked)."},
                    },
                    "required": ["title", "rule"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_batch":
                return self._batch(arguments)
            case "catia_lessons":
                return self._lessons(arguments)
            case "catia_add_lesson":
                return self._add_lesson(arguments)
            case _:
                raise ValueError(f"Unknown meta tool: {tool_name}")

    def _batch(self, a: dict[str, Any]) -> str:
        schemas = {d["name"]: d["inputSchema"] for d in self.server.tool_definitions()}
        report = batch.run_batch(
            a.get("steps"),
            lambda tool, args: self.server.dispatch(tool, args),
            schemas,
            stop_on_error=a.get("stop_on_error", True),
            dry_run=a.get("dry_run", False),
        )
        return report.text()

    @staticmethod
    def _load_lessons():
        try:
            from catia_mcp import lessons
        except ImportError as e:  # pragma: no cover - the module ships with the package
            raise RuntimeError(f"lessons module unavailable: {e}") from e
        return lessons

    def _lessons(self, a: dict[str, Any]) -> str:
        lessons = self._load_lessons()
        found = lessons.search(
            a.get("query", ""), area=a.get("area"), tool=a.get("tool"), limit=int(a.get("limit", 15))
        )
        if not found:
            return "No matching lesson. If you find a new pitfall, record it with catia_add_lesson."
        return lessons.format_lessons(found)

    def _add_lesson(self, a: dict[str, Any]) -> str:
        lessons = self._load_lessons()
        entry = lessons.add_user_lesson(**{k: v for k, v in a.items() if v is not None})
        return f"Lesson {entry['id']} recorded: {entry['title']}. It will be shown to every future session."
