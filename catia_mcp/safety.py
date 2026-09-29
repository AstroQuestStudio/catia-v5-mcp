"""Safety tiers: decide how much an agent is allowed to change.

Three tiers, from least to most powerful::

    READ       inspect only: listing, measuring, screenshots, lessons, drawing analysis
    WRITE      modelling: sketches, features, bodies, constraints, saving, exporting
    DANGEROUS  can destroy work: delete a feature, close documents (unsaved changes are lost)

The tier is fixed at start-up by ``CATIA_MCP_SAFETY`` (``read`` | ``write`` | ``dangerous``;
default ``dangerous``, i.e. no restriction, so existing setups keep working). A review agent
should run with ``read``; an unattended modelling agent on valuable files with ``write``.

The tier is a **ratchet**: the model may lower it at runtime with ``catia_set_safety`` (a
compromised or confused agent can always make itself safer) but can never raise it. Raising needs
a human: restart the server with another environment value. There is deliberately no secret for
the model to supply: a secret that appears in a tool call is a secret the model can leak.
"""

from __future__ import annotations

from catia_mcp import annotations

READ, WRITE, DANGEROUS = "read", "write", "dangerous"
TIERS = (READ, WRITE, DANGEROUS)
_RANK = {t: i for i, t in enumerate(TIERS)}

# Tools that can destroy work. Everything else that is not read-only is WRITE.
DANGEROUS_TOOLS = frozenset({"catia_delete_feature", "catia_close_document", "catia_close_all"})

# Server-level tools that only touch the server itself: always allowed.
# catia_batch checks every step it contains against the tier; recording a lesson touches only the
# local lessons file.
ALWAYS_ALLOWED = frozenset({"catia_get_safety_state", "catia_set_safety", "catia_batch", "catia_add_lesson"})

# Not read-only by name, but they cannot change a model: display, connection, opening a file for inspection.
READ_TIER_EXTRA = frozenset({
    "catia_connect", "catia_disconnect", "catia_set_view", "catia_fit_all", "catia_clean_display",
    "catia_open_document", "catia_prepare_geometry",
})


def parse(value: str | None) -> str:
    """Tier for a CATIA_MCP_SAFETY value. Unknown values raise: a typo must not silently
    grant (or deny) more than intended."""
    if value is None or not value.strip():
        return DANGEROUS
    v = value.strip().lower()
    if v not in _RANK:
        raise ValueError(f"Unknown safety tier '{value}'. Use one of: {', '.join(TIERS)}.")
    return v


def required_tier(tool: str) -> str:
    """Lowest tier a tool needs."""
    if tool in ALWAYS_ALLOWED or tool in READ_TIER_EXTRA or annotations.is_read_only(tool):
        return READ
    if tool in DANGEROUS_TOOLS:
        return DANGEROUS
    return WRITE


class Gate:
    """Holds the current tier and answers 'may this tool run?'."""

    def __init__(self, tier: str = DANGEROUS) -> None:
        self.tier = tier

    def allows(self, tool: str) -> bool:
        return _RANK[required_tier(tool)] <= _RANK[self.tier]

    def blocked_message(self, tool: str) -> str:
        need = required_tier(tool)
        return (
            f"Blocked by safety tier '{self.tier}': {tool} needs '{need}'. "
            "The tier can only be raised by a human (restart the server with "
            f"CATIA_MCP_SAFETY={need}); it cannot be raised from a tool call."
        )

    def blocked(self, tools: list[str]) -> list[str]:
        """Names among ``tools`` that the current tier forbids (for batch pre-validation)."""
        return [t for t in tools if not self.allows(t)]

    def lower(self, tier: str) -> str:
        """Ratchet: lower (or keep) the tier; raising is refused."""
        tier = parse(tier) if tier else self.tier
        if _RANK[tier] > _RANK[self.tier]:
            raise PermissionError(
                f"Refused: the tier is '{self.tier}' and cannot be raised to '{tier}' from a tool "
                "call. Restart the server with a different CATIA_MCP_SAFETY value."
            )
        self.tier = tier
        return self.tier
