"""CLI: python -m fetch [agent|run]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import uuid4
from dotenv import load_dotenv

from fetch.models import FetchRequest, FetchRequestInput, QuerySpec, FetchRequirements, TimeRange
from fetch.run import run


async def _agent_cli(prompt: str) -> None:
    from fetch.run import run_with_agent, save_run
    req = FetchRequest(request_id=uuid4().hex, query_id="cli-query", input=FetchRequestInput(
        query=QuerySpec(query_id="cli-query", question=prompt, task_type="discovery")))
    result = await run_with_agent(req)
    result.extensions["run_directory"] = save_run(req.model_dump(mode="json"), result.model_dump(mode="json"))
    print(result.model_dump_json(indent=2))


def main_agent() -> None:
    load_dotenv()
    prompt = " ".join(sys.argv[1:]) or "What datasets have I downloaded?"
    asyncio.run(_agent_cli(prompt))


def main_run() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run fetch lane (deterministic or agent)")
    parser.add_argument("--agent", action="store_true", help="Use OpenRouter agent loop")
    parser.add_argument("--question", default="Find demo movement and rainfall fixtures")
    parser.add_argument("--species", nargs="*", default=None)
    parser.add_argument(
        "--data-kinds",
        nargs="*",
        default=None,
    )
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--start", help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end", help="Inclusive YYYY-MM-DD")
    args = parser.parse_args()

    species = args.species if args.species is not None else ([] if args.agent else ["example-antelope"])
    kinds = args.data_kinds if args.data_kinds is not None else ([] if args.agent else ["animal_locations", "rainfall_observations"])
    req = FetchRequest(
        request_id=uuid4().hex,
        query_id="cli-query-001",
        input=FetchRequestInput(
            query=QuerySpec(
                query_id="cli-query-001",
                question=args.question,
                task_type="discovery",
                species=list(species),
                time_range=TimeRange(start=args.start, end=args.end),
            ),
            requirements=FetchRequirements(
                species=list(species),
                data_kinds=list(kinds),
                bbox=args.bbox, start=args.start, end=args.end,
            ),
        ),
    )
    resp = run(req, use_agent=args.agent)
    print(resp.model_dump_json(indent=2))


def main_environment() -> None:
    from fetch import paths
    from fetch.connectors.environment import fetch_environment, validate_request
    parser = argparse.ArgumentParser(description="Download raw satellite/rainfall assets with per-run receipts")
    parser.add_argument("--bbox", nargs=4, type=float, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--sources", nargs="+", choices=["sentinel-2", "modis", "chirps"], default=["sentinel-2", "modis", "chirps"])
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--max-days", type=int, default=3)
    parser.add_argument("--max-mb", type=int, default=2048)
    parser.add_argument("--max-file-mb", type=int, default=512)
    parser.add_argument("--cloud-cover", type=float, default=30)
    parser.add_argument("--discover-only", action="store_true")
    args = parser.parse_args()
    try:
        validate_request(args.bbox, args.start, args.end, args.sources, args.max_items, args.max_days, args.max_mb * 1024**2)
        if args.max_file_mb <= 0 or not 0 <= args.cloud_cover <= 100:
            raise ValueError("Invalid file budget or cloud cover")
    except ValueError as exc:
        parser.error(str(exc))
    directory = paths.DATA_ROOT / "runs" / uuid4().hex
    directory.mkdir(parents=True)
    (directory / "request.json").write_text(json.dumps(vars(args), indent=2))
    (directory / "manifest.jsonl").touch()
    def on_event(event):
        with (directory / "events.jsonl").open("a") as log:
            log.write(json.dumps(event) + "\n")
        if event.get("manifest"):
            with (directory / "manifest.jsonl").open("a") as log:
                log.write(json.dumps(event["manifest"]) + "\n")
        print(f"{event['status']}: {event.get('dataset_id', event.get('source'))}", file=sys.stderr)
    result = fetch_environment(args.bbox, args.start, args.end, sources=args.sources,
                               max_items=args.max_items, max_days=args.max_days,
                               max_bytes=args.max_mb * 1024**2, max_file_bytes=args.max_file_mb * 1024**2,
                               cloud_cover=args.cloud_cover, discover_only=args.discover_only, on_event=on_event)
    result["run_directory"] = str(directory)
    (directory / "response.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "outcomes"}, indent=2))
    if result['status'] == 'insufficient_data':
        raise SystemExit(2)


def main_download() -> None:
    from fetch.service import download_dataset
    from fetch.run import save_run
    parser = argparse.ArgumentParser(description="Download a known study without an LLM")
    parser.add_argument("dataset_id")
    args = parser.parse_args()
    result = download_dataset(args.dataset_id)
    payload = result.model_dump(mode="json") if hasattr(result, 'model_dump') else result
    response = {"raw_artifacts": [payload]} if hasattr(result, 'model_dump') else payload
    response['run_directory'] = save_run(vars(args), response)
    print(json.dumps(response, indent=2))
    if not hasattr(result, 'model_dump'):
        raise SystemExit(2)


def main() -> None:
    load_dotenv()
    if len(sys.argv) > 1 and sys.argv[1] in ("environment", "download"):
        command = sys.argv.pop(1)
        (main_environment if command == "environment" else main_download)()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        sys.argv.pop(1)
        main_run()
    else:
        if len(sys.argv) > 1 and sys.argv[1] == "agent":
            sys.argv.pop(1)
        main_agent()


if __name__ == "__main__":
    main()
