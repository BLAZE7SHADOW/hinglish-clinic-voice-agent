"""Did the caller give a clear yes to a read-back? Checked in code before any write.

The LLM calling `confirm_booking` is only its claim that the caller agreed. The rule "read back
doctor, date and time and wait for a clear yes" is enforced here, from the transcript:
1. The caller's latest turn came after the hold (a "yes" to an earlier question doesn't count).
2. The agent's message just before it played to the end, not interrupted: a caller who cut in
   may not have heard the time.
3. That turn is a clear yes: at least one yes word and no word that means no, wait or change.

Asymmetric on purpose. A missed yes costs one more "just to confirm, shall I book it?"; a
false yes books the wrong slot. So anything mixed ("yes but make it six") is not a yes.
"""

import re
from dataclasses import dataclass

from livekit.agents import llm

# Yes words, English and Hinglish (Roman and Devanagari, as Deepgram and Sarvam write them).
_YES_GROUPS = (
    "yes yeah yep yup ok okay okey sure correct right fine confirm confirmed please go ahead",
    "book done perfect",
    "haan han haa ha ji jee bilkul theek thik sahi kar karo kardo kijiye dijiye",
    "हाँ हां जी ठीक बिल्कुल सही",
)
# Any of these makes the turn not a clear yes, whatever else it says. Not "na": "haan na" is an
# emphatic yes in Hinglish.
_NOT_YES_GROUPS = (
    "no not nope nah don't dont wait hold stop change instead but actually wrong",
    "another different other sorry",
    "nahi nahin mat ruko ruk galat lekin par badlo badal dusra doosra",
    "नहीं मत रुको गलत लेकिन",
)
_YES = frozenset(w for group in _YES_GROUPS for w in group.split())
_NOT_YES = frozenset(w for group in _NOT_YES_GROUPS for w in group.split())
MAX_WORDS = 12  # a long turn is the caller saying something else, not a plain yes


def is_clear_yes(text: str) -> bool:
    words = re.findall(r"[a-z']+|[ऀ-ॣ०-ॿ]+", text.lower())
    return (
        0 < len(words) <= MAX_WORDS
        and any(w in _YES for w in words)
        and not any(w in _NOT_YES for w in words)
    )


@dataclass(frozen=True)
class Confirmation:
    ok: bool
    reason: str = ""


def _messages(history: llm.ChatContext) -> list[llm.ChatMessage]:
    return [i for i in history.items if i.type == "message" and i.role in ("user", "assistant")]


def user_turns(history: llm.ChatContext) -> int:
    """How many caller turns the call has had so far."""
    return sum(1 for m in _messages(history) if m.role == "user")


def check_yes(history: llm.ChatContext, *, after_user_turn: int) -> Confirmation:
    """Whether the caller's latest turn is a clear yes to a read-back given after `after_user_turn`."""
    messages = _messages(history)
    last_user = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].role == "user"), None
    )
    if last_user is None or user_turns(history) <= after_user_turn:
        return Confirmation(False, "the caller hasn't answered the read-back yet")
    before = messages[last_user - 1] if last_user > 0 else None
    if before is None or before.role != "assistant":
        return Confirmation(False, "read back doctor, day and time first")
    if before.interrupted:
        return Confirmation(False, "the read-back was interrupted; read it back again in full")
    if not is_clear_yes(messages[last_user].text_content or ""):
        return Confirmation(False, "the caller's answer was not a clear yes; ask again")
    return Confirmation(True)
