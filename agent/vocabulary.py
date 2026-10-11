"""Per-clinic STT vocabulary: names the speech recognizer should expect on this clinic's calls.

STT models guess unfamiliar proper nouns as nearby common words ("Sharma" came back as "Harmer"
and "Sharpa", LEARNING.md #16). The production fix is custom vocabulary at the recognizer
(Deepgram nova-3 `keyterm` prompting, Google speech adaptation, Azure phrase lists), built from
the business's own records, so it works for any clinic without per-name tuning.

Built from the DB after the call starts and applied with the public
`AgentSession.update_options(keyterms=...)`, so it never delays the greeting. Experiment E9
(scripts/name_stt_eval.py) measures whether it helps before it is turned on by default.
"""

import os
import re

from agent import tools

TITLES = {"dr", "doctor", "mr", "mrs", "ms", "prof"}
MAX_KEYTERMS = 100  # Deepgram's documented limit per request


def enabled() -> bool:
    return os.environ.get("STT_KEYTERMS", "off") == "on"


def keyterms_for(doctor_names: list[str], clinic_name: str) -> list[str]:
    """Full names without titles, plus surnames alone, plus the clinic name. Deduplicated."""
    terms: list[str] = []
    for full_name in doctor_names:
        words = [w for w in full_name.split() if re.sub(r"\W", "", w).lower() not in TITLES]
        if words:
            terms += [" ".join(words), words[-1]]
    terms.append(clinic_name)
    return list(dict.fromkeys(terms))[:MAX_KEYTERMS]


async def clinic_keyterms() -> list[str]:
    doctors = await tools.load_doctors()
    return keyterms_for([d.full_name for d in doctors], tools.CLINIC_NAME)
