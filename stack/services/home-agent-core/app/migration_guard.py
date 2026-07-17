from __future__ import annotations

import asyncio
import os
import sys

from .db import Database


async def verify_exact_revision(expected_revision: str) -> int:
    database_url = os.environ.get("HOME_AGENT_DATABASE_URL", "")
    if not database_url:
        raise SystemExit("migration revision verification requires the database URL")
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix(
            "postgresql://"
        )

    database = Database(database_url)
    try:
        actual_revision = await database.migration_revision()
    finally:
        await database.close()
    if actual_revision != expected_revision:
        raise SystemExit(
            "migration revision verification failed; rebuild descendant or "
            "unreviewed databases instead of skipping the deployable hotfix"
        )
    return 0


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1]:
        raise SystemExit("usage: python -m app.migration_guard EXPECTED_REVISION")
    return asyncio.run(verify_exact_revision(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
