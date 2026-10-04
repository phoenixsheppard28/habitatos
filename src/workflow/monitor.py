"""Re-run a saved query when the caller reports a new feature checksum.

The same checksum does not submit again. A failed submit does not consume
the checksum, so the next call with that checksum tries again.
"""

import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class MonitorRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS monitors (
                    monitor_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    checksum TEXT,
                    fingerprint TEXT,
                    job_id TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def register(self, monitor_id: str, request: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO monitors (monitor_id, request_json) VALUES (?, ?)
                ON CONFLICT(monitor_id) DO UPDATE SET
                    request_json = excluded.request_json,
                    checksum = NULL,
                    fingerprint = NULL,
                    job_id = NULL
                """,
                (monitor_id, json.dumps(request)),
            )

    def run_if_changed(self, monitor_id: str, checksum: str, coordinator) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json, checksum, fingerprint, attempt FROM monitors WHERE monitor_id = ?",
                (monitor_id,),
            ).fetchone()
        if row is None:
            raise KeyError(monitor_id)
        request_json, previous_checksum, previous_fingerprint, attempt = row
        if previous_checksum == checksum:
            return {"ran": False, "job_id": _job_id(self, monitor_id), "result_changed": False}
        request = json.loads(request_json)
        request = copy.deepcopy(request)
        # A failed attempt keeps its request_id. The next try uses a new suffix
        # so the coordinator does not return that failed job unchanged.
        request["request_id"] = f"{request['request_id']}-{checksum[:12]}-{attempt}"
        view = coordinator.submit_query(request)
        if not _accepted(view):
            with self._connect() as connection:
                connection.execute(
                    "UPDATE monitors SET attempt = attempt + 1 WHERE monitor_id = ?",
                    (monitor_id,),
                )
            return {"ran": True, "job_id": view.get("job_id"), "result_changed": False, "accepted": False}
        fingerprint = _fingerprint(view.get("result"))
        changed = previous_fingerprint is not None and fingerprint != previous_fingerprint
        with self._connect() as connection:
            connection.execute(
                "UPDATE monitors SET checksum = ?, fingerprint = ?, job_id = ? WHERE monitor_id = ?",
                (checksum, fingerprint, view["job_id"], monitor_id),
            )
        return {"ran": True, "job_id": view["job_id"], "result_changed": changed}

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _job_id(registry: MonitorRegistry, monitor_id: str) -> str | None:
    with registry._connect() as connection:
        row = connection.execute("SELECT job_id FROM monitors WHERE monitor_id = ?", (monitor_id,)).fetchone()
    return None if row is None else row[0]


def _accepted(view: dict) -> bool:
    if not view.get("job_id"):
        return False
    if view.get("status") == "error" or view.get("job_status") == "failed":
        return False
    return True


def _fingerprint(result) -> str:
    raw = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
