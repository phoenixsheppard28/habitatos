"""Real end-to-end example: wildebeest GPS fixes on the Athi-Kaputiei Plains, Kenya, with rainfall and vegetation.

Run with `uv run python examples/athi_kaputiei.py`. A second run downloads and appends nothing new.
"""

import logging
from datetime import date
from pathlib import Path

from habitat.db import connect
from habitat.grid import default_grid
from habitat.ingest import Workspace, run

ATHI_KAPUTIEI = (36.85, -1.60, 37.10, -1.35)
WILDEBEEST_PACKAGE = "5b6706c8-e7e5-46e4-82ba-da5a82324298"
LONG_RAINS_2011 = (date(2011, 3, 1), date(2011, 4, 30))
CLEAR_SENTINEL2_DAY = date(2024, 2, 17)
QUERIES = Path(__file__).with_name("queries.sql")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with connect() as connection:
        workspace = Workspace(Path("data/raw"), connection, default_grid())
        steps = [
            ("movebank", {"package": WILDEBEEST_PACKAGE}),
            ("chirps", {"bbox": ATHI_KAPUTIEI, "start": LONG_RAINS_2011[0], "end": LONG_RAINS_2011[1]}),
            ("modis_mod13q1", {"bbox": ATHI_KAPUTIEI, "start": LONG_RAINS_2011[0], "end": LONG_RAINS_2011[1]}),
            ("sentinel2", {"bbox": ATHI_KAPUTIEI, "start": CLEAR_SENTINEL2_DAY, "end": CLEAR_SENTINEL2_DAY}),
        ]
        for source, arguments in steps:
            outcomes = run(source, workspace, **arguments)
            appended = sum(1 for o in outcomes if o.append and o.append.appended)
            quarantined = [o.quarantine_reason for o in outcomes if o.quarantine_reason]
            print(f"{source}: {appended} new item(s), {len(outcomes) - appended} skipped, quarantined: {quarantined}")

        for title, sql in named_queries(QUERIES.read_text()):
            rows = connection.execute(sql).fetchall()
            print(f"\n-- {title}: {len(rows)} row(s)")
            for row in rows[:5]:
                print(row)


def named_queries(text: str) -> list[tuple[str, str]]:
    """Split a SQL file on `-- name:` lines."""
    queries = []
    for block in text.split("-- name: ")[1:]:
        title, _, sql = block.partition("\n")
        queries.append((title.strip(), sql.strip().rstrip(";")))
    return queries


if __name__ == "__main__":
    main()
