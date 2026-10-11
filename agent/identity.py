"""Caller identity details from spoken words: phone numbers and names, parsed in code.

Callers read phone numbers in parts ("nine eight seven, double four...", "nau aath saat"), and STT
writes them as digits, words or a mix. Code turns them into one E.164 form so a lookup never
depends on how the LLM copied them. Nothing here logs the values (design §11).
"""

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
