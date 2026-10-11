"""Postgres access: connection settings from DATABASE_URL, a per-process pool, timed reads
and writes.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import unquote

import asyncpg


def connect_kwargs(dsn: str) -> dict:
    """Split a postgresql:// URL into asyncpg keyword arguments.

    Splits on the LAST '@', so a raw '@' (or ':' / '#') in the password works. A plain
    URL parser stops at the first '@' and mistakes the rest of the password for the host.
    Percent-encoded passwords (%40) are decoded too.
    """
    rest = dsn.split("://", 1)[1]
    creds, _, location = rest.rpartition("@")
    user, _, password = creds.partition(":")
    hostport, _, database = location.partition("/")
    host, _, port = hostport.partition(":")
    return {
        "user": unquote(user),
        "password": unquote(password),
        "host": host,
        "port": int(port or 5432),
        "database": database.split("?", 1)[0] or "postgres",
        # Supabase's transaction pooler doesn't support prepared statements.
        "statement_cache_size": 0,
    }


# One pool per call process (LiveKit runs each call in its own process), with all 3 connections
# opened at call start in the background: find_slots runs 3 reads in parallel, and each new
# connection costs ~360 ms of setup that the first tool call would otherwise pay.
_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock | None = None

QUERY_TIMEOUT_S = 2.0  # below what a caller tolerates in silence (design §9)
WRITE_TIMEOUT_S = 3.0  # a whole write transaction: hold, confirm, event row
READ_RETRY_BACKOFF_S = 0.2


async def get_pool():
    global _pool, _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    async with _pool_lock:
        if _pool is None:
            assert_safe_target()
            _pool = await asyncpg.create_pool(
                min_size=3, max_size=3, **connect_kwargs(os.environ["DATABASE_URL"])
            )
    return _pool


async def read(query: str, *args):
    """Run a read with a timeout, retrying once (reads only; writes never retry blindly)."""
    for attempt in (1, 2):
        try:
            pool = await get_pool()
            return await asyncio.wait_for(pool.fetch(query, *args), QUERY_TIMEOUT_S)
        except (TimeoutError, OSError, asyncpg.PostgresConnectionError):
            if attempt == 2:
                raise
            await asyncio.sleep(READ_RETRY_BACKOFF_S)


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """One write transaction, never retried here (design §9: writes never retry blindly).

    The caller wraps it in `asyncio.timeout(WRITE_TIMEOUT_S)`. A timeout leaves the outcome
    unknown: the write may have committed. Retry only with the same idempotency key, which
    returns the existing row instead of writing a second one (design §16.3).
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        yield conn


class UnsafeTarget(RuntimeError):
    """An eval tried to use a database that isn't the eval sandbox."""


def assert_safe_target() -> None:
    """Evals never touch real systems (design §16.2): a hard switch, checked on every connect.

    With EVAL_MODE=1 the only allowed database is EVAL_DATABASE_URL, and it must differ from the
    one in .env's DATABASE_URL (the runner swaps it in before anything connects).
    """
    if os.environ.get("EVAL_MODE") != "1":
        return
    eval_url = os.environ.get("EVAL_DATABASE_URL")
    if not eval_url or os.environ.get("DATABASE_URL") != eval_url:
        raise UnsafeTarget("EVAL_MODE=1 needs DATABASE_URL to be EVAL_DATABASE_URL")
    if eval_url == os.environ.get("REAL_DATABASE_URL"):
        raise UnsafeTarget("EVAL_DATABASE_URL is the real database")
