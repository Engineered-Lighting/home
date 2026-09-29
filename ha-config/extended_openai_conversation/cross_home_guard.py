"""Deterministic guard: Los Angeles's assistant never acts on Victoria.

Model-originated device actions are already disabled in this integration.
This guard also stops the model from *claiming* it switched something in
Victoria or "both homes": such requests get a fixed reply that points to
Home's confirmed cross-home lighting (docs/CROSS-HOME-LIGHTING.md), before any
prompt or tool is involved. Questions about Victoria (not actions) pass through.

Pure module with no Home Assistant imports, so it can be tested standalone.
"""

from __future__ import annotations

import re
import unicodedata

_OTHER_HOME = re.compile(r"\b(?:victoria|both (?:homes|houses)|the other (?:home|house)|both places)\b")
_ACTION = re.compile(
    r"\b(?:turn|switch|set|dim|brighten|toggle|shut|open|close|lock|unlock|start|stop|run|play|pause|"
    r"activate|enable|disable|arm|disarm)\b"
)
_QUESTION = re.compile(r"^(?:is|are|was|were|what|which|who|when|where|why|how|did|does|do|can you tell|tell me)\b")

REPLY = ("I can't control devices in Victoria from here. In the Home chat, ask for example "
         "\"turn off the kitchen light in Victoria\".")


def cross_home_action_reply(text: str | None) -> str | None:
    """Return the fixed reply for a request to act on Victoria or both homes, else None."""
    value = unicodedata.normalize("NFKC", text or "").lower().replace("’", "'")
    value = re.sub(r"\s+", " ", value).strip()
    if not value or len(value) > 500 or not _OTHER_HOME.search(value) or not _ACTION.search(value):
        return None
    if _QUESTION.match(value) and not re.match(r"^(?:can|could|would|will) you\b", value):
        return None  # "is the light on in victoria" asks; it does not act
    return REPLY
