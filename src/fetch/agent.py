"""OpenRouter-backed fetch agent (OpenAI Agents SDK)."""

from __future__ import annotations

import os

from agents import Agent
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from dotenv import load_dotenv
from openai import AsyncOpenAI

from fetch.tools import FETCH_AGENT_TOOLS

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-120b"

FETCH_INSTRUCTIONS = """
You are the Habitat Watch fetch assistant. Your job is to find and download
ecological datasets that match the user's request.

Rules:
- Use search_catalog to discover datasets, then inspect_source before downloading.
- Call check_access before download_dataset when access is unclear.
- Use list_downloaded_files to see what is already cached locally.
- Never invent dataset ids, file paths, or download results — only report tool output.
- Prefer fixture sources when live credentials are unavailable.
- If nothing matches, say what you searched and what is missing.
""".strip()


def build_openrouter_model(model: str | None = None) -> OpenAIChatCompletionsModel:
    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key."
        )

    default_headers: dict[str, str] = {}
    referer = os.environ.get("OPENROUTER_HTTP_REFERER")
    title = os.environ.get("OPENROUTER_APP_TITLE", "Habitat Watch Fetch")
    if referer:
        default_headers["HTTP-Referer"] = referer
    if title:
        default_headers["X-Title"] = title

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers=default_headers or None,
    )
    return OpenAIChatCompletionsModel(
        model=model or os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
        openai_client=client,
    )


def build_fetch_agent(model: str | None = None) -> Agent:
    return Agent(
        name="Habitat fetch assistant",
        instructions=FETCH_INSTRUCTIONS,
        model=build_openrouter_model(model),
        tools=FETCH_AGENT_TOOLS,
    )
