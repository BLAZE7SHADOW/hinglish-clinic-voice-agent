"""Postgres access: connection settings from DATABASE_URL, a per-process pool, timed reads."""

import asyncio
import os
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
READ_RETRY_BACKOFF_S = 0.2


async def get_pool():
    global _pool, _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    async with _pool_lock:
        if _pool is None:
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
