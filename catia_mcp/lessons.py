"""Lessons learned: a small knowledge base that keeps AI agents from repeating known CATIA mistakes.

Two sources are merged by :func:`load`:

* the built-in lessons shipped with the package (``catia_mcp/data/lessons.json``);
* the user's own lessons, appended as JSON Lines by :func:`add_user_lesson`
  (``$CATIA_MCP_HOME/lessons.jsonl``, else ``%APPDATA%/catia-mcp/lessons.jsonl``,
  else ``~/.catia-mcp/lessons.jsonl``).

Standard library only; nothing here needs CATIA, so it is fully testable offline.

Lesson schema (dict)::

    id, title, area, severity, symptom, error_patterns (case-insensitive regexes
    matched against the real error text), cause, rule, example (optional),
    tools (MCP tool names), proof, source ('builtin' | 'user')
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

AREAS = (
    "com", "sketch", "part", "boolean", "topology", "measure",
    "assembly", "display", "process", "drawing", "performance",
)
SEVERITIES = ("critical", "high", "medium", "low")
_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}  # lower = more severe

_BUILTIN_NAME = "lessons.json"
_pattern_cache: dict[str, re.Pattern[str] | None] = {}


# --------------------------------------------------------------------------- helpers
def _norm(text: str) -> str:
    """Lowercase and strip accents."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _norm(title)).strip()


def _compile(pattern: str) -> re.Pattern[str] | None:
    if pattern not in _pattern_cache:
        try:
            _pattern_cache[pattern] = re.compile(pattern, re.IGNORECASE)
        except re.error:
            _pattern_cache[pattern] = None
    return _pattern_cache[pattern]


def _clean(entry: Any, source: str) -> dict[str, Any] | None:
    """Validate and normalise one lesson; None when unusable."""
    if not isinstance(entry, dict):
        return None
    title, rule = entry.get("title"), entry.get("rule")
    if not isinstance(title, str) or not title.strip() or not isinstance(rule, str) or not rule.strip():
        return None
    area = entry.get("area") if entry.get("area") in AREAS else "process"
    severity = entry.get("severity") if entry.get("severity") in SEVERITIES else "medium"
    patterns = [p for p in (entry.get("error_patterns") or []) if isinstance(p, str)]
    tools = [t for t in (entry.get("tools") or []) if isinstance(t, str)]
    out = {
        "id": str(entry.get("id") or ""),
        "title": title.strip(),
        "area": area,
        "severity": severity,
        "symptom": str(entry.get("symptom") or ""),
        "error_patterns": patterns,
        "cause": str(entry.get("cause") or ""),
        "rule": rule.strip(),
        "tools": tools,
        "proof": str(entry.get("proof") or ""),
        "source": str(entry.get("source") or source),
    }
    if entry.get("example"):
        out["example"] = str(entry["example"])
    return out


# --------------------------------------------------------------------------- loading
def builtin_path() -> Path:
    return Path(__file__).resolve().parent / "data" / _BUILTIN_NAME


def user_lessons_path() -> Path:
    """Where user lessons live (resolved at call time so tests can redirect it)."""
    home = os.environ.get("CATIA_MCP_HOME")
    if home:
        return Path(home) / "lessons.jsonl"
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "catia-mcp" / "lessons.jsonl"
    return Path.home() / ".catia-mcp" / "lessons.jsonl"


def _load_builtin() -> list[dict[str, Any]]:
    raw: str | None = None
    try:  # works from a wheel too, provided the data file is packaged
        from importlib import resources

        raw = (resources.files("catia_mcp") / "data" / _BUILTIN_NAME).read_text(encoding="utf-8")
    except Exception:
        raw = None
    if raw is None:
        try:
            raw = builtin_path().read_text(encoding="utf-8")
        except OSError:
            return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [c for c in (_clean(e, "builtin") for e in data) if c]


def _load_user(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        cleaned = _clean(entry, "user")
        if cleaned:
            if not cleaned["id"]:
                cleaned["id"] = f"U{len(out) + 1:03d}"
            cleaned["source"] = "user"
            out.append(cleaned)
    return out


def load(user_path: Path | None = None) -> list[dict[str, Any]]:
    """Built-in lessons followed by the user's; invalid user lines are skipped."""
    return _load_builtin() + _load_user(Path(user_path) if user_path else user_lessons_path())


# --------------------------------------------------------------------------- error hints
def _match_length(lesson: dict[str, Any], text: str) -> int:
    best = 0
    for p in lesson["error_patterns"]:
        rx = _compile(p)
        if rx is None:
            continue
        m = rx.search(text)
        if m:
            best = max(best, len(m.group(0)) or 1)
    return best


def hint_for_error(text: str, tool: str | None = None, limit: int = 2) -> str:
    """'' or a block of '[lesson Lxxx] title: rule' lines for lessons whose error_pattern matches.

    Ranking: lessons naming ``tool`` first, then tool-specific before generic
    lessons, then severity, then the length of the match.
    """
    if not text or limit <= 0:
        return ""
    hits = []
    for lesson in load():
        length = _match_length(lesson, text)
        if length:
            hits.append((lesson, length))
    if not hits:
        return ""

    def key(item: tuple[dict[str, Any], int]) -> tuple[int, int, int, int]:
        lesson, length = item
        tool_match = 0 if (tool and tool in lesson["tools"]) else 1
        generic = 0 if lesson["tools"] else 1
        return (tool_match, generic, _SEV_RANK[lesson["severity"]], -length)

    hits.sort(key=key)
    return "\n".join(f"[lesson {l['id']}] {l['title']}: {l['rule']}" for l, _ in hits[:limit])


# --------------------------------------------------------------------------- instructions
_AREA_TITLES = {
    "com": "COM", "sketch": "Sketch", "part": "Part Design", "boolean": "Boolean",
    "topology": "Topology", "measure": "Measure", "assembly": "Assembly",
    "display": "Display", "process": "Process", "drawing": "Drawing",
    "performance": "Performance",
}
_HEADER = (
    "CATIA V5 golden rules, learned the hard way on a live CATIA V5 R19. "
    "Follow them before calling tools; each line ends with its lesson id."
)
_FOOTER = (
    "For details call catia_lessons(topic). Record any new discovery or mistake "
    "with catia_add_lesson so it is never repeated."
)


def _short(rule: str, limit: int) -> str:
    rule = " ".join(rule.split())
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z])", rule, maxsplit=1)[0]
    if len(first) < 60 and first != rule:  # very short first sentence: keep two
        first = rule
    if len(first) > limit:
        cut = first[: limit - 3]
        first = (cut.rsplit(" ", 1)[0] if " " in cut[40:] else cut).rstrip(" ,;:") + "..."
    return first


def _redundant(lesson: dict[str, Any]) -> bool:
    """True when the rule mostly repeats the title (no need to print both)."""
    words = lambda t: set(re.findall(r"[a-z0-9_]{3,}", _norm(t)))  # noqa: E731
    title, rule = words(lesson["title"]), words(_short(lesson["rule"], 400))
    return bool(rule) and len(title & rule) / len(rule) >= 0.6


def _line(lesson: dict[str, Any], limit: int) -> str:
    text = lesson["title"].rstrip(".")
    if limit > 0 and not _redundant(lesson):
        text += ": " + _short(lesson["rule"], limit)
    return f"- [{_AREA_TITLES[lesson['area']]}] {text} ({lesson['id']})"


def _assemble(tiers: list[tuple[str, list[dict[str, Any]], int]]) -> str:
    lines = [_HEADER]
    for label, items, limit in tiers:
        if not items:
            continue
        lines.append("")
        lines.append(f"## {label}")
        by_area: dict[str, list[dict[str, Any]]] = {}
        for lesson in items:
            by_area.setdefault(lesson["area"], []).append(lesson)
        for area in AREAS:
            for lesson in by_area.get(area, []):
                lines.append(_line(lesson, limit))
    lines.append("")
    lines.append(_FOOTER)
    return "\n".join(lines)


def render_instructions(max_chars: int = 7000) -> str:
    """Compact golden-rules text for the MCP server ``instructions`` (critical, then high).

    One line per rule, grouped by area. Rules are shortened progressively (and the
    lowest-priority high lessons dropped last) until the text fits ``max_chars``.
    """
    lessons = load()
    critical = [l for l in lessons if l["severity"] == "critical"]
    high = [l for l in lessons if l["severity"] == "high"]
    for crit_limit, high_limit in ((200, 70), (170, 45), (150, 0), (120, 0), (0, 0)):
        keep_high = list(high)
        while True:
            text = _assemble([("Critical", critical, crit_limit), ("Important", keep_high, high_limit)])
            if len(text) <= max_chars or not keep_high:
                break
            keep_high.pop()
        if len(text) <= max_chars and len(keep_high) == len(high):
            return text
        if len(text) <= max_chars and crit_limit == 0:
            return text
    keep_crit = list(critical)
    while keep_crit and len(_assemble([("Critical", keep_crit, 0)])) > max_chars:
        keep_crit.pop()
    text = _assemble([("Critical", keep_crit, 0)])
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


# --------------------------------------------------------------------------- search / format
def _haystack(lesson: dict[str, Any]) -> list[tuple[str, int]]:
    return [
        (lesson["id"], 4), (lesson["title"], 3), (lesson["rule"], 2), (lesson["symptom"], 2),
        (lesson["cause"], 1), (lesson.get("example", ""), 1), (" ".join(lesson["tools"]), 2),
        (lesson["area"], 1), (" ".join(lesson["error_patterns"]), 1),
    ]


def search(query: str = "", area: str | None = None, tool: str | None = None, limit: int = 15) -> list[dict[str, Any]]:
    """Simple accent- and case-insensitive full-text search, optionally filtered by area/tool."""
    tokens = [t for t in re.split(r"\W+", _norm(query)) if t]
    scored: list[tuple[int, int, int, dict[str, Any]]] = []
    for idx, lesson in enumerate(load()):
        if area and lesson["area"] != area:
            continue
        if tool and tool not in lesson["tools"]:
            continue
        score = 0
        if tokens:
            fields = [(_norm(text), w) for text, w in _haystack(lesson)]
            matched = 0
            for tok in tokens:
                tok_score = sum(w for text, w in fields if tok in text)
                if tok_score:
                    matched += 1
                    score += tok_score
            if not matched:
                continue
            score += matched * 5  # reward covering more query words
        scored.append((-score, _SEV_RANK[lesson["severity"]], idx, lesson))
    scored.sort(key=lambda s: s[:3])
    return [s[3] for s in scored[: max(0, int(limit))]]


def format_lessons(lessons: Iterable[dict[str, Any]]) -> str:
    """Readable text for a tool result."""
    blocks = []
    for l in lessons:
        lines = [f"[{l['id']}] ({l['severity']}, {l['area']}) {l['title']}"]
        if l.get("symptom"):
            lines.append(f"  Symptom: {l['symptom']}")
        if l.get("cause"):
            lines.append(f"  Cause: {l['cause']}")
        lines.append(f"  Rule: {l['rule']}")
        if l.get("example"):
            lines.append(f"  Example: {l['example']}")
        if l.get("tools"):
            lines.append(f"  Tools: {', '.join(l['tools'])}")
        if l.get("proof"):
            lines.append(f"  Proof: {l['proof']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "No matching lesson."


# --------------------------------------------------------------------------- user lessons
def add_user_lesson(
    title: str,
    rule: str,
    area: str = "process",
    severity: str = "medium",
    symptom: str = "",
    cause: str = "",
    error_patterns: list[str] | None = None,
    tools: list[str] | None = None,
    proof: str = "",
) -> dict[str, Any]:
    """Validate and append a lesson to the user's JSONL file; returns the stored entry.

    Raises ValueError on invalid input or when a lesson with the same normalised title exists.
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required")
    if not isinstance(rule, str) or not rule.strip():
        raise ValueError("rule is required")
    if area not in AREAS:
        raise ValueError(f"area must be one of {', '.join(AREAS)}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    patterns = list(error_patterns or [])
    for p in patterns:
        if not isinstance(p, str):
            raise ValueError("error_patterns must be strings")
        try:
            re.compile(p, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regex {p!r}: {exc}") from exc
    tool_list = [str(t) for t in (tools or [])]

    path = user_lessons_path()
    existing = load(path)
    key = _title_key(title)
    for lesson in existing:
        if _title_key(lesson["title"]) == key:
            raise ValueError(f"duplicate of lesson {lesson['id']}: {lesson['title']}")

    numbers = [int(m.group(1)) for l in existing if (m := re.fullmatch(r"U(\d+)", l["id"]))]
    entry = {
        "id": f"U{(max(numbers) if numbers else 0) + 1:03d}",
        "title": title.strip(),
        "area": area,
        "severity": severity,
        "symptom": symptom,
        "error_patterns": patterns,
        "cause": cause,
        "rule": rule.strip(),
        "tools": tool_list,
        "proof": proof,
        "source": "user",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
