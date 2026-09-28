"""Tool sets: expose only the tools a task needs.

102 tool schemas cost ~20k tokens of context on every turn and overwhelm smaller models, which
then pick the wrong tool. ``CATIA_MCP_TOOLSETS`` selects which groups are *advertised*
(``tools/list``). Hidden tools still work when called explicitly and inside ``catia_batch``,
so a profile never breaks an existing script; it only shrinks what the model has to choose from.

Examples::

    CATIA_MCP_TOOLSETS=part            # single-part modelling
    CATIA_MCP_TOOLSETS=assembly        # big assemblies (no sketcher / part design / surfaces)
    CATIA_MCP_TOOLSETS=part,drawing    # presets and groups can be mixed
    (unset) or full                    # everything (default)

The ``meta`` group (batch, lessons) is always advertised.
"""

from __future__ import annotations

GROUPS = (
    "document", "sketch", "part", "body", "boolean", "gsd",
    "assembly", "measure", "export", "drawing", "meta",
)

PRESETS: dict[str, frozenset[str]] = {
    "part": frozenset({"document", "sketch", "part", "body", "boolean", "measure", "export", "meta"}),
    "surface": frozenset({"document", "sketch", "gsd", "part", "body", "measure", "export", "meta"}),
    "assembly": frozenset({"document", "assembly", "measure", "export", "meta"}),
    "review": frozenset({"document", "measure", "export", "drawing", "meta"}),
    "full": frozenset(GROUPS),
}

ALWAYS = frozenset({"meta"})


def parse(spec: str | None) -> frozenset[str]:
    """Groups to advertise for a CATIA_MCP_TOOLSETS value. Unknown names raise ValueError
    (a typo silently hiding every tool would be far worse than a clear startup error)."""
    if not spec or not spec.strip():
        return PRESETS["full"]
    chosen: set[str] = set(ALWAYS)
    for raw in spec.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in PRESETS:
            chosen |= PRESETS[token]
        elif token in GROUPS:
            chosen.add(token)
        else:
            raise ValueError(
                f"Unknown tool set '{token}'. Groups: {', '.join(GROUPS)}. "
                f"Presets: {', '.join(PRESETS)}."
            )
    return frozenset(chosen)


def visible(tool_group: str, enabled: frozenset[str]) -> bool:
    return tool_group in enabled or tool_group in ALWAYS
