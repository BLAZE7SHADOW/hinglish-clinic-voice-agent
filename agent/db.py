"""Postgres connection settings from DATABASE_URL."""

from urllib.parse import unquote


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
