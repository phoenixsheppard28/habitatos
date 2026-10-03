import anthropic

from habitat.config import settings

FETCH_MODEL = "claude-sonnet-5-5"
CATALOG_MODEL = "claude-opus-5-5"


def client() -> anthropic.Anthropic:
    api_key = settings().anthropic_api_key
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to the .env file at the project root.")

    return anthropic.Anthropic(api_key=api_key)
