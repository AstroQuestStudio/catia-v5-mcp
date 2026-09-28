"""Optional feature journal: a CSV line and a CATIA window screenshot after each feature tool.

Off by default. Enable with ``CATIA_MCP_AUTOTRACE=1``. Output goes to
``<state dir>/trace`` (see ``paths.home``; override with ``CATIA_MCP_TRACE_DIR``):

* ``feature_log.csv`` : timestamp, piece, feature_index, operation, params, status, note
* ``<piece>/<timestamp>_<piece>_<NN>_<operation>.png`` : the CATIA window after the operation

Useful for reports, audits and post-mortems of an unattended session. It never raises: a
failure here (e.g. no CATIA window) must not take down the CATIA operation that already ran.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from catia_mcp import paths
from catia_mcp.capture import capture_catia_window

logger = logging.getLogger("catia_mcp.autotrace")

# tool name -> operation label used in the CSV
TRACED_TOOLS: dict[str, str] = {
    "catia_pad": "Pad",
    "catia_pocket": "Pocket",
    "catia_shaft": "Shaft",
    "catia_groove": "Groove",
    "catia_fillet": "Fillet",
    "catia_chamfer": "Chamfer",
    "catia_hole": "Hole",
    "catia_rect_pattern": "Pattern_rect",
    "catia_circ_pattern": "Pattern_circ",
    "catia_mirror": "Mirror",
    "catia_shell": "Shell",
    "catia_draft": "Draft",
    "catia_thickness": "Thickness",
    "catia_thread": "Thread",
    "catia_new_body": "New_Body",
    "catia_boolean_operation": "Boolean",
}

FIELDNAMES = ["timestamp", "piece", "feature_index", "operation", "params", "status", "note"]


def trace_dir() -> Path:
    env = os.environ.get("CATIA_MCP_TRACE_DIR")
    return Path(env) if env else paths.home() / "trace"


def is_traced(tool_name: str) -> bool:
    return tool_name in TRACED_TOOLS


def resolve_piece_id(active_document_name: str) -> str:
    """'Flange.CATPart' -> 'Flange'."""
    return Path(active_document_name).stem


def log_csv_path() -> Path:
    return trace_dir() / "feature_log.csv"


def next_feature_index(csv_path: Path, piece_id: str) -> int:
    """Continuous feature index per piece (never reset between features)."""
    if not csv_path.exists():
        return 1
    max_index = 0
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("piece") != piece_id:
                continue
            try:
                max_index = max(max_index, int(row.get("feature_index", "")))
            except ValueError:
                continue
    return max_index + 1


def log_feature_to_csv(
    csv_path: Path,
    piece_id: str,
    feature_index: int,
    operation: str,
    params: str,
    status: str = "OK",
    note: str = "",
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "piece": piece_id,
                "feature_index": feature_index,
                "operation": operation,
                "params": params,
                "status": status,
                "note": note,
            }
        )


def capture_active_window(piece_id: str, action_index: int, description: str) -> str | None:
    """Screenshot the CATIA window only (not the desktop). None when it cannot be taken."""
    try:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        safe = description.replace(" ", "_").replace("/", "-")
        name = f"{stamp}_{piece_id}_{action_index:02d}_{safe}.png"
        return capture_catia_window(trace_dir() / piece_id / name)
    except Exception as e:
        logger.warning("CATIA window capture failed: %s", e)
        return None


def trace_after_tool_call(
    connection: Any, tool_name: str, arguments: dict[str, Any], result_text: str
) -> str | None:
    """Best-effort capture + CSV log right after a traced tool succeeded.

    Returns a short status suffix for the tool result, or None if the tool is not traced.
    """
    if not is_traced(tool_name):
        return None
    try:
        piece_id = resolve_piece_id(connection.active_document.Name)
    except Exception as e:
        return f"[auto-trace] skipped: no active document ({e})"

    operation = TRACED_TOOLS[tool_name]
    csv_path = log_csv_path()
    feature_index = next_feature_index(csv_path, piece_id)
    screenshot = capture_active_window(piece_id, feature_index, operation)
    try:
        log_feature_to_csv(
            csv_path, piece_id, feature_index, operation,
            json.dumps(arguments, ensure_ascii=False), "OK", result_text[:200],
        )
    except Exception as e:
        return f"[auto-trace] capture={'ok' if screenshot else 'FAILED'}, log FAILED ({e})"
    shot = screenshot if screenshot else "FAILED (see server log)"
    return f"[auto-trace] {piece_id} #{feature_index:02d} {operation} -> {csv_path.name}; screenshot: {shot}"
