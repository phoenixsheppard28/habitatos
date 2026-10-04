import hashlib
import logging
from pathlib import Path

import psycopg

from habitat.db import MIGRATIONS_DIR, database_url

logger = logging.getLogger(__name__)


def migrate(url: str | None = None, directory: Path = MIGRATIONS_DIR) -> list[str]:
    migrations = sorted(directory.glob("*.sql"))
    if not migrations:
        raise RuntimeError(f"No SQL migrations found in {directory}.")

    applied = []
    with psycopg.connect(url or database_url(), autocommit=True, connect_timeout=10) as database:
        database.execute("SELECT pg_advisory_lock(724019381)")
        database.execute("CREATE SCHEMA IF NOT EXISTS extensions")
        database.execute("SET search_path = public, extensions")
        database.execute("""
            CREATE TABLE IF NOT EXISTS habitat_schema_migrations (
                filename text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        recorded = dict(database.execute("SELECT filename, checksum FROM habitat_schema_migrations").fetchall())
        scripts = [(path.name, path.read_text(encoding="utf-8")) for path in migrations]
        checksums = {name: hashlib.sha256(script.encode()).hexdigest() for name, script in scripts}
        for name, checksum in recorded.items():
            if checksums.get(name) != checksum:
                raise RuntimeError(f"Applied migration {name} is missing or changed. Add a new migration instead.")

        for name, script in scripts:
            if name in recorded:
                continue

            with database.transaction():
                database.execute(script)
                database.execute(
                    "INSERT INTO habitat_schema_migrations (filename, checksum) VALUES (%s, %s)",
                    (name, checksums[name]),
                )
            applied.append(name)
            logger.info("Applied %s", name)

    logger.info("Database ready: %d migrations applied, %d already current.", len(applied), len(recorded))

    return applied


def main():
    logging.basicConfig(level=logging.INFO)
    migrate()


if __name__ == "__main__":
    main()
