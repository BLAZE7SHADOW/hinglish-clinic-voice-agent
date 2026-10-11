"""The eval sandbox: a separate database and a frozen clock (design §16.2).

Evals never touch real systems. `enter()` is the one switch: it points DATABASE_URL at
EVAL_DATABASE_URL, sets EVAL_MODE=1 (which agent/db.py checks on every connect) and freezes
the clock, so every run sees the same schedule. It refuses to start if the eval database is
missing or is the real one.
"""

import os

from dotenv import load_dotenv

# A Monday morning. Seeded data is relative to this date, so "kal" is always Tuesday.
FAKE_NOW = "2026-10-12T10:00:00+05:30"


class NoSandbox(RuntimeError):
    pass


def enter(fake_now: str = FAKE_NOW) -> None:
    load_dotenv(".env")
    eval_url = os.environ.get("EVAL_DATABASE_URL")
    real_url = os.environ.get("REAL_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not eval_url:
        raise NoSandbox(
            "EVAL_DATABASE_URL is not set. Evals need their own database (a second Supabase "
            "project or a local Postgres), never the real one."
        )
    if eval_url == real_url:
        raise NoSandbox("EVAL_DATABASE_URL is the same as DATABASE_URL")
    os.environ["REAL_DATABASE_URL"] = real_url
    os.environ["DATABASE_URL"] = eval_url
    os.environ["EVAL_MODE"] = "1"
    os.environ["AGENT_FAKE_NOW"] = fake_now


async def prepare() -> None:
    """Migrate and re-seed the sandbox. Safe to repeat; refuses anything but the sandbox."""
    from agent.tools import common, read
    from db import seed
    from scripts import migrate

    await migrate.main()
    await seed.seed()
    # Re-seeding recreates the clinic and doctors with new ids; the tools' short-lived caches
    # would still hold the old ones and find no working hours.
    common._clinic_cache = None
    read._doctors_cache = None
