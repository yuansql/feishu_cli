"""P2P unknown → Hermes classify. Not an ever-growing alias table."""

from __future__ import annotations

from ..core.session import looks_like_followup
from ..compose.llm import classify_intent, hermes_available
from .intents import Intent, looks_like_daily_brief, parse_intent


def refine_p2p_intent(text: str, intent: Intent | None = None) -> Intent:
    """Keep parse_intent hits; unknown → product/followup, else Hermes classify."""
    intent = intent if intent is not None else parse_intent(text)
    if intent.action != "unknown":
        return intent
    asked = (text or "").strip()
    if looks_like_daily_brief(asked):
        return Intent(action="brief")
    if looks_like_followup(asked):
        return intent
    if not hermes_available():
        return intent
    classified = classify_intent(asked)
    if classified is not None and classified.action not in {"", "unknown"}:
        return classified
    return intent
