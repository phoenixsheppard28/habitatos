"""Job persistence. Memory is the default. SQLite keeps a job after the process stops."""

import json
import sqlite3
from pathlib import Path

from workflow.coordinator import Job, StageRecord


class MemoryJobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._by_request: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def find_request(self, request_id: str) -> Job | None:
        return self._by_request.get(request_id)

    def save(self, job: Job) -> None:
        self._jobs[job.job_id] = job
        request_id = job.request.get("request_id")
        if request_id:
            self._by_request[request_id] = job


class SqliteJobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    request_id TEXT UNIQUE,
                    body TEXT NOT NULL
                )
                """
            )

    def get(self, job_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute("SELECT body FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return None if row is None else _load(row[0])

    def find_request(self, request_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute("SELECT body FROM jobs WHERE request_id = ?", (request_id,)).fetchone()
        return None if row is None else _load(row[0])

    def save(self, job: Job) -> None:
        body = json.dumps(_dump(job), sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (job_id, request_id, body) VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET request_id = excluded.request_id, body = excluded.body
                """,
                (job.job_id, job.request.get("request_id"), body),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


def _dump(job: Job) -> dict:
    return {
        "job_id": job.job_id,
        "request": job.request,
        "job_status": job.job_status,
        "user_status": job.user_status,
        "stages": [
            {"name": stage.name, "state": stage.state, "attempts": stage.attempts, "output": stage.output}
            for stage in job.stages
        ],
        "error": job.error,
        "final_output": job.final_output,
    }


def _load(body: str) -> Job:
    data = json.loads(body)
    return Job(
        job_id=data["job_id"],
        request=data["request"],
        job_status=data["job_status"],
        user_status=data["user_status"],
        stages=[StageRecord(**stage) for stage in data["stages"]],
        error=data["error"],
        final_output=data["final_output"],
    )
