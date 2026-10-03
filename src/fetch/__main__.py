"""CLI: python -m fetch [agent|run]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from fetch.agent import build_fetch_agent
from fetch.models import FetchRequest, FetchRequestInput, QuerySpec, FetchRequirements, TimeRange
from fetch.run import run
from agents import Runner


async def _agent_cli(prompt: str) -> None:
    agent = build_fetch_agent()
    result = await Runner.run(agent, prompt)
    print(result.final_output)


def main_agent() -> None:
    prompt = " ".join(sys.argv[1:]) or "What datasets have I downloaded?"
    asyncio.run(_agent_cli(prompt))


def main_run() -> None:
    parser = argparse.ArgumentParser(description="Run fetch lane (deterministic or agent)")
    parser.add_argument("--agent", action="store_true", help="Use Groq agent loop")
    parser.add_argument("--question", default="Find demo movement and rainfall fixtures")
    parser.add_argument("--species", nargs="*", default=["example-antelope"])
    parser.add_argument(
        "--data-kinds",
        nargs="*",
        default=["animal_locations", "rainfall_observations"],
    )
    args = parser.parse_args()

    req = FetchRequest(
        request_id="cli-request-001",
        query_id="cli-query-001",
        input=FetchRequestInput(
            query=QuerySpec(
                query_id="cli-query-001",
                question=args.question,
                task_type="discovery",
                species=list(args.species),
                time_range=TimeRange(start="2025-01-01T00:00:00Z", end="2025-01-07T23:59:59Z"),
            ),
            requirements=FetchRequirements(
                species=list(args.species),
                data_kinds=list(args.data_kinds),
            ),
        ),
    )
    resp = run(req, use_agent=args.agent)
    print(resp.model_dump_json(indent=2))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        sys.argv.pop(1)
        main_run()
    else:
        if len(sys.argv) > 1 and sys.argv[1] == "agent":
            sys.argv.pop(1)
        main_agent()


if __name__ == "__main__":
    main()
