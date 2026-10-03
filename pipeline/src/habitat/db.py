import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

DATABASE_URL_VARIABLE = "HABITAT_DATABASE_URL"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def database_url() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT.parent / ".env")
    url = os.environ.get(DATABASE_URL_VARIABLE)
    if not url:
        raise RuntimeError(f"set {DATABASE_URL_VARIABLE} in the environment or in .env")

    return url


def connect(url: str | None = None) -> psycopg.Connection:
    """Autocommit connection. Writers open explicit transactions with `connection.transaction()`."""
    connection = psycopg.connect(url or database_url(), autocommit=True)
    connection.execute("SET TIME ZONE 'UTC'")
    return connection


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))
