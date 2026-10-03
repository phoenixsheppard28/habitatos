"""Re-run a saved query when the caller reports a new feature checksum."""

import copy
import hashlib
import json
import sqlite3
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
                    job_id TEXT
                )
                """
            )

    def register(self, monitor_id: str, request: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO monitors (monitor_id, request_json) VALUES (?, ?)
                ON CONFLICT(monitor_id) DO UPDATE SET request_json = excluded.request_json
                """,
                (monitor_id, json.dumps(request)),
            )

    def run_if_changed(self, monitor_id: str, checksum: str, coordinator) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json, checksum, fingerprint FROM monitors WHERE monitor_id = ?",
                (monitor_id,),
            ).fetchone()
        if row is None:
            raise KeyError(monitor_id)
        request_json, previous_checksum, previous_fingerprint = row
        if previous_checksum == checksum:
            return {"ran": False, "job_id": _job_id(self, monitor_id), "result_changed": False}
        request = json.loads(request_json)
        request = copy.deepcopy(request)
        request["request_id"] = f"{request['request_id']}-{checksum[:12]}"
        view = coordinator.submit_query(request)
        fingerprint = _fingerprint(view.get("result"))
        changed = previous_fingerprint is not None and fingerprint != previous_fingerprint
        with self._connect() as connection:
            connection.execute(
                "UPDATE monitors SET checksum = ?, fingerprint = ?, job_id = ? WHERE monitor_id = ?",
                (checksum, fingerprint, view["job_id"], monitor_id),
            )
        return {"ran": True, "job_id": view["job_id"], "result_changed": changed}

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def _job_id(registry: MonitorRegistry, monitor_id: str) -> str | None:
    with registry._connect() as connection:
        row = connection.execute("SELECT job_id FROM monitors WHERE monitor_id = ?", (monitor_id,)).fetchone()
    return None if row is None else row[0]


def _fingerprint(result) -> str:
    raw = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
