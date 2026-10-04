import argparse
import json
import logging
from datetime import date, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, ValidationError

from habitat.config import PROJECT_ROOT, settings
from habitat.db import database_url

logger = logging.getLogger(__name__)
FEATURE_LIMIT = 20_000


def connection():
    database = psycopg.connect(database_url(), autocommit=True, connect_timeout=10, row_factory=dict_row)
    database.execute("SET TIME ZONE 'UTC'")
    database.execute("SET statement_timeout = '30s'")
    return database


def public_dataset(database, dataset_id):
    row = database.execute(
        "SELECT descriptor FROM latest_datasets WHERE dataset_id = %s AND access_scope = 'public' "
        "AND status = 'ready' AND source_id NOT LIKE 'fixture%%'",
        (dataset_id,),
    ).fetchone()
    if row is None:
        raise LookupError("This dataset is not available in the public catalog.")

    return row["descriptor"]


def catalog():
    with connection() as database:
        rows = database.execute(
            "SELECT descriptor FROM latest_datasets WHERE access_scope = 'public' AND status = 'ready' "
            "AND source_id NOT LIKE 'fixture%' ORDER BY family, dataset_id"
        ).fetchall()

    fields = ("dataset_id", "version", "family", "source_id", "description", "summary", "coverage",
              "variables", "row_count", "created_at", "raw_artifact_refs")
    return {"datasets": [{key: row["descriptor"].get(key) for key in fields} for row in rows],
            "assistant_available": bool(settings().anthropic_api_key)}


def dataset_features(dataset_id):
    with connection() as database:
        dataset = public_dataset(database, dataset_id)
        parameters = {"dataset": dataset_id, "version": str(dataset["version"]), "limit": FEATURE_LIMIT}
        if dataset["family"] == "animal_locations":
            query = """
                SELECT entity_id, species, day AS observed_at, last_fix_at, fix_count,
                       longitude, latitude, cell_id, daily_displacement_km,
                       source_record_id, dataset_version
                FROM recipe_animal_daily_movement
                WHERE source_dataset_id = %(dataset)s AND dataset_version = %(version)s
                  AND access_scope = 'public'
            """
            geometry = "jsonb_build_object('type', 'Point', 'coordinates', jsonb_build_array(longitude, latitude))"
            metrics = """count(DISTINCT entity_id) AS entities, sum(fix_count) AS fixes,
                         count(daily_displacement_km) AS segments,
                         sum(daily_displacement_km) AS distance_km,
                         avg(daily_displacement_km) AS mean_distance_km"""
        elif dataset["family"] == "cell_observations":
            query = """
                SELECT o.cell_id, o.time_start AS observed_at, o.time_end AS observed_until,
                       o.variable, o.value, o.unit, o.quality_flag, o.valid_fraction,
                       o.source_item_id AS source_record_id, o.dataset_version, g.geometry
                FROM current_cell_observations o JOIN grid_cells g USING (cell_id)
                WHERE o.dataset_id = %(dataset)s AND o.dataset_version <= %(version)s::integer
                  AND EXISTS (SELECT 1 FROM latest_datasets d WHERE d.dataset_id = o.dataset_id
                              AND d.access_scope = 'public' AND d.status = 'ready')
            """
            geometry = "ST_AsGeoJSON(geometry)::jsonb"
            metrics = """count(DISTINCT cell_id) AS cells, avg(value) AS mean_value,
                         count(value) AS measured_records"""
        else:
            raise ValueError("This dataset family has no map adapter.")

        rows = database.execute(
            f"WITH observations AS ({query}) SELECT {geometry} AS geometry, "
            "to_jsonb(observations) - 'geometry' - 'longitude' - 'latitude' AS properties "
            "FROM observations ORDER BY observed_at, source_record_id LIMIT %(limit)s", parameters,
        ).fetchall()
        monthly = database.execute(
            f"WITH observations AS ({query}) SELECT to_char(observed_at, 'YYYY-MM') AS month, "
            f"count(*) AS records, {metrics} FROM observations GROUP BY 1 ORDER BY 1", parameters,
        ).fetchall()
        manifests = database.execute(
            "SELECT DISTINCT raw_manifest->'source' AS source, raw_manifest->'rights' AS rights "
            "FROM ingest_batches WHERE series_id = %s AND added_in_version <= %s "
            "AND (superseded_in_version IS NULL OR superseded_in_version > %s)",
            (dataset_id, dataset["version"], dataset["version"]),
        ).fetchall()

    total = sum(month["records"] for month in monthly)
    return {"type": "FeatureCollection", "features": [{"type": "Feature", **row} for row in rows],
            "dataset_id": dataset_id, "version": dataset["version"], "monthly": monthly,
            "total_records": total, "truncated": total > len(rows), "limit": FEATURE_LIMIT,
            "sources": manifests,
            "grain": "Last good fix per animal and UTC day" if dataset["family"] == "animal_locations"
            else "Observation per spatial cell, variable, and acquisition"}


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=12_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    dataset_id: str | None = Field(default=None, max_length=300)
    through: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class Handler(SimpleHTTPRequestHandler):
    def json_response(self, status, payload):
        body = json.dumps(payload, default=lambda value: value.isoformat() if isinstance(value, (date, datetime))
                          else str(value), allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return super().do_GET()

        self.handle_api(lambda: self.get_api(parsed))

    def get_api(self, parsed):
        if parsed.path == "/api/catalog":
            return catalog()
        if parsed.path == "/api/features":
            dataset_id = parse_qs(parsed.query).get("dataset_id", [""])[0]
            if not dataset_id or len(dataset_id) > 300:
                raise ValueError("Select a catalog dataset.")
            return dataset_features(dataset_id)

        raise LookupError("Unknown API endpoint.")

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat":
            return self.json_response(404, {"error": "Unknown API endpoint."})

        self.handle_api(self.chat_api)

    def chat_api(self):
        from habitat.web_assistant import answer

        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 256_000:
            raise ValueError("The request body must contain at most 256 KB.")
        request = ChatRequest.model_validate(json.loads(self.rfile.read(length)))
        if request.messages[-1].role != "user":
            raise ValueError("The last message must contain a user question.")

        return answer(request)

    def handle_api(self, operation):
        try:
            self.json_response(200, operation())
        except LookupError as error:
            self.json_response(404, {"error": str(error)})
        except (ValueError, ValidationError):
            self.json_response(400, {"error": "Invalid request. Check the selected dataset and question."})
        except Exception:
            logger.exception("Workspace API request failed")
            self.json_response(503, {"error": "The backend is unavailable. Check the server logs and connection settings."})


def main():
    parser = argparse.ArgumentParser(description="Serve the Habitat Watch API and built workspace.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    handler = partial(Handler, directory=str(PROJECT_ROOT / "web" / "dist"))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    logger.info("Habitat Watch API: http://%s:%s", args.host, args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
