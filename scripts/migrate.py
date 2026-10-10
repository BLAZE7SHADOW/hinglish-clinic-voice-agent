"""Apply db/migrations/*.sql in name order, once each.

    uv run python -m scripts.migrate

Applied files are tracked in a schema_migrations table, so re-running is safe.
"""

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

MIGRATIONS = Path("db/migrations")


async def main() -> None:
    load_dotenv(".env")
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        await conn.execute(
            "create table if not exists schema_migrations"
            " (name text primary key, applied_at timestamptz not null default now())"
        )
        applied = {r["name"] for r in await conn.fetch("select name from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                print(f"skip   {path.name}")
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("insert into schema_migrations (name) values ($1)", path.name)
            print(f"apply  {path.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
