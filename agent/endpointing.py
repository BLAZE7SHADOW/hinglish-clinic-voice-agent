"""Phase-aware endpointing: how long to wait for the caller depends on what the agent just asked.

The week 2 voice test (LEARNING #27): 27 of 52 turns waited the full 2.5 s end-of-turn delay. The
text turn detector scores short complete answers ("Five thirty is fine.") as unfinished, so
LiveKit waits `max_delay` on them. For a phone number said in groups that wait is right; after
"Shall I book it?" it's dead air.

IVR systems have set the "incomplete timeout" per prompt for decades. Here code picks the phase
after every agent message, from call state and the question asked, and sets LiveKit's
`max_delay` for the caller's next turn:

- long: a phone number, a date of birth or a spelling is expected (callers pause between groups)
- short: a yes/no (a read-back is waiting), a choice of time or doctor, "anything else?"
- default: everything else, unchanged from LiveKit's 2.5 s

Off by default (`ENDPOINTING=off`) so the baseline stays the baseline; the voice session turns it
on and compares, with each turn's phase recorded in telemetry.
"""

import logging
import os
import re
from dataclasses import dataclass

from livekit.agents import AgentSession, ConversationItemAddedEvent, llm

from agent.state import CallState

logger = logging.getLogger("endpointing")

_LONG = re.compile(r"(?i)\b(phone|mobile|number|date of birth|birth date|born|spell|letter)\b")
_SHORT = re.compile(
    r"(?i)\b(which (time|one|doctor|slot)|would you like|shall i|is that right|anything else|"
    r"works (best )?for you|suits you|prefer|or)\b[^.?!]*\?\s*$"
)


@dataclass(frozen=True)
class Settings:
    short_max_s: float = 1.0
    default_max_s: float = 2.5  # LiveKit's streaming default: unchanged unless a phase says so
    long_max_s: float = 3.0

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            short_max_s=float(os.environ.get("ENDPOINT_SHORT_MAX_S", cls.short_max_s)),
            default_max_s=float(os.environ.get("ENDPOINT_DEFAULT_MAX_S", cls.default_max_s)),
            long_max_s=float(os.environ.get("ENDPOINT_LONG_MAX_S", cls.long_max_s)),
        )
        if not 0.5 <= settings.short_max_s <= settings.default_max_s <= settings.long_max_s <= 6:
            raise ValueError(
                "need 0.5 <= ENDPOINT_SHORT_MAX_S <= ENDPOINT_DEFAULT_MAX_S <= "
                f"ENDPOINT_LONG_MAX_S <= 6, got {settings}"
            )
        return settings


def enabled() -> bool:
    return os.environ.get("ENDPOINTING", "off") == "phase"


def phase(agent_text: str, state: CallState) -> str:
    """ "short", "long" or "default": what the caller's next turn is likely to be."""
    if state.awaiting_yes:
        if state.pending is not None and state.pending.key.startswith("name|"):
            return "long"  # a correction may be a spelling, with pauses between letters
        return "short"  # a read-back: yes, no, or a short correction
    if _LONG.search(agent_text):
        return "long"
    if _SHORT.search(agent_text.strip()):
        return "short"
    return "default"


class PhaseEndpointing:
    def __init__(self, session: AgentSession[CallState], settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self.current = "default"

    def on_item(self, ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, llm.ChatMessage) or item.role != "assistant":
            return
        new = phase(item.text_content or "", self._session.userdata)
        if new == self.current:
            return
        self.current = new
        delay = {
            "short": self._settings.short_max_s,
            "long": self._settings.long_max_s,
            "default": self._settings.default_max_s,
        }[new]
        self._session.update_options(endpointing_opts={"max_delay": delay})
        logger.debug("endpointing phase %s: max_delay %.1f s", new, delay)


def attach(session: AgentSession[CallState], settings: Settings) -> PhaseEndpointing:
    handler = PhaseEndpointing(session, settings)
    session.on("conversation_item_added", handler.on_item)
    return handler
