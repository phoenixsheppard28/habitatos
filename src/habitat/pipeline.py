"""Fetch temporary raw files, normalize, publish database records, and remove processed downloads."""

import argparse
import logging
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

from recipe.progress import stage

from psycopg.pq import TransactionStatus

from habitat.archive import ChecksumMismatch
from habitat.archive.index import PostgresArtifactIndex, ingested
from habitat.contracts import (
    BBox,
    FetchRequest,
    FetchRequestInput,
    FetchRequirements,
    FetchResponse,
    QuerySpec,
    RawManifest,
    TimeRange,
)
from habitat.db import connect
from habitat.fetch import run as fetch
from habitat.grid import default_grid
from habitat.ingest import IngestOutcome, Workspace, ingest_manifest, publish_changed
from habitat.normalize.rows import series_id
from habitat.sources import SOURCES, get_source
from habitat.storage.series import AppendResult

logger = logging.getLogger(__name__)

NO_PUBLISH_STATUSES = {"insufficient_data", "error"}


@dataclass
class ArtifactOutcome:
    artifact_id: str
    version: str
    source_id: str
    status: str
    reason: str | None = None
    series_id: str | None = None


@dataclass
class PipelineResult:
    fetch: FetchResponse
    outcomes: list[ArtifactOutcome] = field(default_factory=list)
    published: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.fetch.status

    @property
    def warnings(self) -> list[str]:
        return self.fetch.warnings


def run(
    request: FetchRequest, use_agent: bool, workspace: Workspace | None = None, use_ai: bool = False
) -> PipelineResult:
    if workspace is None:
        with connect() as connection:
            return run(request, use_agent, Workspace(connection, default_grid()), use_ai)

    with stage("fetch", "Find and archive source data"):
        response = fetch.run(request, use_agent=use_agent, archive=workspace.archive)
    if response.status in NO_PUBLISH_STATUSES:
        return PipelineResult(response)

    return ingest_and_publish(request, response, workspace, use_ai)


def ingest_and_publish(
    request: FetchRequest, response: FetchResponse, workspace: Workspace, use_ai: bool = False
) -> PipelineResult:
    """Normalize the raw artifacts of one fetch response, append them to their series and publish the changes."""
    request_aoi = tuple(request.input.requirements.bbox) if request.input.requirements.bbox else None
    outcomes, ingests = [], []
    for manifest in response.output.raw_artifacts:
        with stage("normalize", f"Normalize and ingest {manifest.extensions.source_id}"):
            outcome, ingest = process(manifest, workspace, request_aoi or fetched_area(manifest))
        outcomes.append(outcome)
        if ingest is not None:
            ingests.append(ingest)

    with stage("catalog.publish", "Publish updated datasets"):
        published = publish_changed(ingests, workspace, request.access_scope, use_ai)
    with stage("archive.cleanup", "Remove raw files saved in the database"):
        cleanup_published_raw_files(workspace)
    return PipelineResult(response, outcomes, published)


def cleanup_published_raw_files(workspace: Workspace) -> dict[str, int]:
    removed = {"artifacts": 0, "bytes": 0}
    if workspace.connection.info.transaction_status != TransactionStatus.IDLE:
        return removed

    index = PostgresArtifactIndex(workspace.connection)
    manifests = index.published_artifacts()
    if workspace.connection.info.transaction_status != TransactionStatus.IDLE:
        return removed

    with workspace.archive.store.lock():
        for manifest in manifests:
            try:
                size = workspace.archive.store.remove(manifest)
            except (OSError, ChecksumMismatch) as error:
                logger.warning("Raw cleanup failed for %s/%s: %s", manifest.artifact_id, manifest.version, error)
                continue
            if size:
                removed["artifacts"] += 1
                removed["bytes"] += size

    logger.info("Removed %d processed raw artifacts (%d bytes); source metadata remains in the database.",
                removed["artifacts"], removed["bytes"])
    return removed


def fetched_area(manifest: RawManifest) -> BBox | None:
    """On the agent path the area is in the question; the connector recorded the bbox that it fetched."""
    bbox = manifest.extensions.properties.get("requested_bbox")
    return tuple(bbox) if bbox else None


def process(manifest: RawManifest, workspace: Workspace, aoi: BBox | None):
    item = manifest.extensions
    outcome = ArtifactOutcome(manifest.artifact_id, manifest.version, item.source_id, "quarantined")

    source = get_source(item.source_id)
    if source is None:
        outcome.reason = f"unknown source_id {item.source_id!r}; register the source before ingest"
        return outcome, None

    outcome.series_id = series_id(manifest, workspace.grid)
    if ingested(workspace.store, outcome.series_id)(
        item.source_item_id, item.processing_version, item.product_status.value
    ):
        outcome.status = "already_present"
        version = workspace.store.latest_version(outcome.series_id)
        batch = next(batch for batch in version.batches if
                     (batch.source_item_id, batch.processing_version, batch.product_status) ==
                     (item.source_item_id, item.processing_version, item.product_status.value))
        return outcome, IngestOutcome(manifest, AppendResult(outcome.series_id, version.version, batch.batch_key, False))

    ingest = ingest_manifest(manifest, workspace.archive, workspace.store, workspace.grid, aoi)
    if ingest.quarantine_reason:
        outcome.reason = ingest.quarantine_reason
        return outcome, None

    outcome.status = "appended" if ingest.append.appended else "already_present"
    return outcome, ingest


def parse_bbox(value: str) -> BBox:
    west, south, east, north = (float(part) for part in value.split(","))
    return west, south, east, north


def build_request(
    source: str | None,
    bbox: BBox | None,
    start: date | None,
    end: date | None,
    package: str | None,
    question: str | None,
) -> FetchRequest:
    query_id = f"cli-{uuid4().hex[:8]}"
    first, last = (start.isoformat() if start else None), (end.isoformat() if end else None)
    return FetchRequest(
        request_id=uuid4().hex,
        query_id=query_id,
        input=FetchRequestInput(
            query=QuerySpec(
                query_id=query_id,
                question=question or f"Fetch {source}",
                task_type="discovery" if question else "historical",
                time_range=TimeRange(start=first, end=last),
            ),
            requirements=FetchRequirements(
                source_ids=[source] if source else [],
                bbox=list(bbox) if bbox else None,
                start=first,
                end=last,
                package=package,
            ),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch, archive, normalize and publish habitat data.")
    parser.add_argument("source", nargs="?", choices=sorted(SOURCES), help="run one source without the agent")
    parser.add_argument("--bbox", type=parse_bbox, help="west,south,east,north in WGS84; required for satellite sources")
    parser.add_argument("--start", type=date.fromisoformat, help="first day, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="last day, YYYY-MM-DD")
    parser.add_argument("--package", help="dataset of the source: Movebank package UUID, study id, record or fixture id")
    parser.add_argument("--question", help="ask the fetch agent instead of naming a source")
    parser.add_argument("--ai-tags", action="store_true", help="enable model tags; the configured local classifier takes priority")
    args = parser.parse_args()

    if bool(args.source) == bool(args.question):
        parser.error("give a source or --question, not both")
    if args.source:
        source = SOURCES[args.source]
        if source.needs_item and not args.package:
            parser.error(f"{args.source} needs --package")
        if source.needs_area_and_dates and not (args.bbox and args.start and args.end):
            parser.error(f"{args.source} needs --bbox, --start and --end")

    logging.basicConfig(level=logging.INFO)
    request = build_request(args.source, args.bbox, args.start, args.end, args.package, args.question)
    result = run(request, use_agent=bool(args.question), use_ai=args.ai_tags)

    print(f"fetch status: {result.status}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    for outcome in result.outcomes:
        print(f"{outcome.artifact_id}/{outcome.version}: {outcome.reason or outcome.status}")
    for series in result.published:
        print(f"published: {series}")
    if result.status in NO_PUBLISH_STATUSES:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
