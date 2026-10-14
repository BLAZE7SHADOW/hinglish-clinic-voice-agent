"""Did the caller give a clear yes to a read-back? Checked in code before any write.

The LLM calling a write tool is only its claim that the caller agreed. The rule "read back
doctor, date and time and wait for a clear yes" is enforced here, from the transcript:
1. The caller's latest turn came after the read-back was spoken (an earlier yes doesn't count).
2. The agent's message just before it is the read-back code wrote (agent/readback.py), word for
   word, and it played to the end. A caller who cut in may not have heard the time.
3. That turn is a clear yes: at least one yes word and no word that means no, wait or change.

Asymmetric on purpose. A missed yes costs one more "shall I book it?"; a false yes books the
wrong slot. So anything mixed ("yes but make it six") is not a yes.
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
    read_back_again: bool = False  # the read-back wasn't heard: speak it again, don't just ask


def messages(history: llm.ChatContext) -> list[llm.ChatMessage]:
    return [i for i in history.items if i.type == "message" and i.role in ("user", "assistant")]


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+|[\u0900-\u0963\u0966-\u097f]+", text.lower()))


def user_turns(history: llm.ChatContext) -> int:
    """How many caller turns the call has had so far."""
    return sum(1 for m in messages(history) if m.role == "user")


def check_yes(history: llm.ChatContext, *, after_user_turn: int, read_back: str) -> Confirmation:
    """Whether the caller's latest turn is a clear yes to `read_back`, spoken after
    `after_user_turn` and heard in full.
    """
    items = messages(history)
    last_user = next((i for i in range(len(items) - 1, -1, -1) if items[i].role == "user"), None)
    if last_user is None or user_turns(history) <= after_user_turn:
        return Confirmation(False, "the caller hasn't answered the read-back yet")
    before = items[last_user - 1] if last_user > 0 else None
    # Containment, not equality: TTS-aligned text can differ in spacing and punctuation.
    if (
        before is None
        or before.role != "assistant"
        or _words(read_back) not in _words(before.text_content or "")
    ):
        return Confirmation(False, "the caller's answer wasn't to the read-back", True)
    if before.interrupted:
        return Confirmation(False, "the read-back was interrupted", True)
    if not is_clear_yes(items[last_user].text_content or ""):
        return Confirmation(False, "the caller's answer was not a clear yes; ask what they want")
    return Confirmation(True)
