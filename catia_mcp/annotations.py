"""MCP tool annotations (readOnlyHint / destructiveHint / idempotentHint).

Clients use them to auto-approve harmless calls (listing, measuring) and to ask before risky
ones (closing documents, deleting features, booleans that rewrite a body). They are derived
from the tool name so a new tool gets a sensible default without any extra registration:
unclassified tools are treated as ordinary, non-destructive modelling writes.
"""

from __future__ import annotations

_READ_ONLY_PREFIXES = (
    "catia_get_",
    "catia_list_",
    "catia_measure_",
    "catia_sketch_get_",
    "drawing_",
)
_READ_ONLY = {
    "catia_lessons",
    "catia_clash_analysis",   # computes and removes its own temporary clash object
    "catia_screenshot",       # writes an image file, never changes the model
    "catia_audit",
    "catia_audit_model",      # reads the model and reports; the fixes it proposes are separate calls
    "catia_describe_model",
    "catia_drawing_info",
    "catia_drawing_check",
}
_DESTRUCTIVE = {
    "catia_delete_feature",
    "catia_close_document",
    "catia_close_all",
    "catia_boolean_operation",
}
# Same input, same resulting model state: safe to retry.
_IDEMPOTENT = {
    "catia_connect", "catia_disconnect", "catia_set_view", "catia_fit_all", "catia_clean_display",
    "catia_update_part", "catia_update_assembly", "catia_activate_body", "catia_hide_show_body",
    "catia_gsd_set_active_geoset", "catia_save_document", "catia_save_all",
}


def is_read_only(name: str) -> bool:
    return name in _READ_ONLY or name.startswith(_READ_ONLY_PREFIXES)


def hints(name: str) -> dict[str, bool]:
    """Annotation fields for one tool (camelCase, as in the MCP specification)."""
    read_only = is_read_only(name)
    out = {"readOnlyHint": read_only, "openWorldHint": False}
    if not read_only:
        out["destructiveHint"] = name in _DESTRUCTIVE
        out["idempotentHint"] = name in _IDEMPOTENT
    return out
