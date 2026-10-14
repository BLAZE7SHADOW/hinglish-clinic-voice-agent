"""Caller identity details from spoken words: phone numbers and names, parsed in code.

Callers read phone numbers in parts ("nine eight seven, double four...", "nau aath saat"), and STT
writes them as digits, words or a mix. Code turns them into one E.164 form so a lookup never
depends on how the LLM copied them. Nothing here logs the values (design §11).
"""

import difflib
import re

_DIGIT_WORDS = {
    # English
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    # Hindi (romanized; common spellings)
    "shunya": "0",
    "shoonya": "0",
    "ek": "1",
    "do": "2",
    "teen": "3",
    "char": "4",
    "chaar": "4",
    "paanch": "5",
    "panch": "5",
    "chhe": "6",
    "che": "6",
    "saat": "7",
    "aath": "8",
    "ath": "8",
    "nau": "9",
}
_REPEAT_WORDS = {"double": 2, "triple": 3}  # "double four" = 44, the usual Indian way of saying it


def parse_phone(text: str) -> str | None:
    """An Indian mobile number in E.164 (+91XXXXXXXXXX), or None if it isn't one.

    Accepts 10 digits starting 6-9, with an optional +91, 91 or 0 in front.
    """
    digits: list[str] = []
    repeat = 1
    for token in re.findall(r"\d+|[a-z]+", text.lower()):
        if token in _REPEAT_WORDS:
            repeat = _REPEAT_WORDS[token]
            continue
        if token.isdigit():
            # "double 4" repeats one digit; "98765" is a run of digits as written
            digits.append(token[0] * repeat + token[1:])
        elif token in _DIGIT_WORDS:
            digits.append(_DIGIT_WORDS[token] * repeat)
        repeat = 1
    number = "".join(digits)
    if len(number) == 12 and number.startswith("91"):
        number = number[2:]
    elif len(number) == 11 and number.startswith("0"):
        number = number[1:]
    if len(number) == 10 and number[0] in "6789":
        return f"+91{number}"
    return None


_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "shree", "smt", "kumari", "ji", "sahab"}


def normalize_name(text: str) -> str:
    """Lower case, letters only, honorifics dropped: "Mr. Rohan  Joshi ji" -> "rohan joshi".

    Exact comparison after this, never fuzzy: a near-miss on a name must fail verification, not
    pass it (LEARNING #16 on why thresholds fitted to our own calls were rejected).
    """
    words = re.findall(r"[a-zऀ-ॿ]+", text.lower())
    return " ".join(w for w in words if w not in _HONORIFICS)


# Spelled names: "K a b I r", "k-a-b-i-r", "B for ball", "bravo". STT writes a spelling as
# single letters, often across several segments; the LLM merges or drops them (LEARNING #25).
_NATO = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e",
    "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i", "juliet": "j", "juliett": "j",
    "kilo": "k", "lima": "l", "mike": "m", "november": "n", "oscar": "o", "papa": "p",
    "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t", "uniform": "u", "victor": "v",
    "whiskey": "w", "xray": "x", "yankee": "y", "zulu": "z",
}  # fmt: skip
MIN_SPELLED_LETTERS = 3
SPELLING_MATCH = 0.5  # how alike a heard word and a spelling must be to be the same word  # "a" and "i" are also words: two letters in a row prove nothing


def parse_spelled_name(text: str) -> str | None:
    """The longest spelled-out word in `text` ("K a b I r" -> "Kabir"), or None.

    A letter followed by "for <word>" or "as in <word>" counts once ("B for ball" = b). Of
    several spellings the longest wins, and the later one on a tie: callers repeat a corrected
    spelling at the end.
    """
    tokens = re.findall(r"[a-z]+|[.!?]", text.lower().replace("x-ray", "xray"))
    runs: list[str] = []
    current: list[str] = []
    since_stop = 0  # letters since the last full stop
    i = 0
    while i < len(tokens):
        t = tokens[i]
        i += 1
        if t in ".!?":
            # "B I r. B I r." is two spellings; "K. A. B. I. R." is one, a letter per stop.
            if since_stop > 1 and current:
                runs.append("".join(current))
                current = []
            since_stop = 0
            continue
        letter = t if len(t) == 1 else _NATO.get(t)
        if letter is None:
            if current:
                runs.append("".join(current))
                current = []
            since_stop = 0
            continue
        current.append(letter)
        since_stop += 1
        if tokens[i : i + 1] == ["for"]:  # "b for ball": skip the example word
            i += 2
        elif tokens[i : i + 2] == ["as", "in"]:
            i += 3
    if current:
        runs.append("".join(current))
    best = ""
    for run in runs:
        if len(run) >= MIN_SPELLED_LETTERS and len(run) >= len(best):
            best = run
    return best.capitalize() or None


def apply_spelling(name: str, spelled: str | None) -> str:
    """The caller's name with the spelled word put in place of the word it spells.

    The spelling replaces the word that sounds most like it ("Kavir" -> "Kabir"), or the whole
    name if it is one word. If no word is close, the name is the spelling: the caller spelled
    it because the name was wrong.
    """
    if not spelled:
        return name.strip()
    words = name.split()
    for k, word in enumerate(words):  # a partial spelling: only part of one word was wrong
        if fixed := _splice(word, spelled):
            words[k] = fixed
            return " ".join(words)
    if len(words) <= 1:
        return spelled

    def score(word: str) -> float:
        return difflib.SequenceMatcher(None, _fold(word), _fold(spelled)).ratio()

    # Every word that sounds like the spelling is the same word heard differently: "Kavita
    # Kabir" + "Kabir" is one name said and then spelled, not two names (R2 re-test, #28).
    alike = [k for k, word in enumerate(words) if score(word) >= SPELLING_MATCH]
    if not alike:
        return spelled
    kept = [w for k, w in enumerate(words) if k not in alike[1:]]
    kept[alike[0]] = spelled
    return " ".join(kept)


def _splice(word: str, part: str) -> str | None:
    """A partial spelling fixes the end or start of a word: "Kavir" + "bir" -> "Kabir"."""
    w, p = _fold(word), _fold(part)
    if len(p) >= len(w):
        return None
    if w.endswith(p):
        return (word[: len(word) - len(part)] + part.lower()).capitalize()
    if w.startswith(p):
        return (part + word[len(part) :]).capitalize()
    return None


def _fold(s: str) -> str:
    """Letters that sound alike in Indian English map to one (LEARNING #25): b/v, v/w, z/j."""
    return s.lower().translate(str.maketrans("vwz", "bvj"))
