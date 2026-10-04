import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from habitat.db import database_url
from habitat.migrate import migrate


@unittest.skipUnless(os.environ.get("HABITAT_MIGRATION_TESTS") == "1", "requires a disposable database server")
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.admin = psycopg.connect(database_url(), autocommit=True)
        self.addCleanup(self.admin.close)
        name = f"habitat_migration_test_{uuid4().hex}"
        self.admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        self.addCleanup(
            self.admin.execute,
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)),
        )
        self.url = make_conninfo(database_url(), dbname=name)

    def test_fresh_database_and_repeat_start(self):
        applied = migrate(self.url)
        self.assertEqual(len(applied), 9)
        self.assertEqual(migrate(self.url), [])

        with psycopg.connect(self.url) as database:
            self.assertEqual(database.execute("SELECT count(*) FROM latest_datasets").fetchone()[0], 0)
            self.assertEqual(database.execute("SELECT count(*) FROM habitat_schema_migrations").fetchone()[0], 9)
            self.assertEqual(database.execute("SELECT ST_SRID(ST_Point(36, -2, 4326))").fetchone()[0], 4326)
            database.execute("SELECT * FROM recipe_animal_daily_movement LIMIT 0")

    def test_failure_rolls_back_and_retry_applies_only_pending_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "001_first.sql").write_text("CREATE TABLE first_table (id integer);", encoding="utf-8")
            failing = path / "002_second.sql"
            failing.write_text("CREATE TABLE second_table (id integer); SELECT 1 / 0;", encoding="utf-8")

            with self.assertRaises(psycopg.errors.DivisionByZero):
                migrate(self.url, path)

            with psycopg.connect(self.url) as database:
                self.assertIsNone(database.execute("SELECT to_regclass('second_table')").fetchone()[0])
                self.assertEqual(database.execute("SELECT filename FROM habitat_schema_migrations").fetchall(),
                                 [("001_first.sql",)])

            failing.write_text("CREATE TABLE second_table (id integer);", encoding="utf-8")
            self.assertEqual(migrate(self.url, path), ["002_second.sql"])

    def test_changed_applied_migration_blocks_new_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = path / "001_first.sql"
            first.write_text("CREATE TABLE first_table (id integer);", encoding="utf-8")
            migrate(self.url, path)
            first.write_text("CREATE TABLE changed_table (id integer);", encoding="utf-8")
            (path / "002_second.sql").write_text("CREATE TABLE second_table (id integer);", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "missing or changed"):
                migrate(self.url, path)

            with psycopg.connect(self.url) as database:
                self.assertIsNone(database.execute("SELECT to_regclass('second_table')").fetchone()[0])
