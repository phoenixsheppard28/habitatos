from openai import OpenAI

from habitat.config import settings

FETCH_MODEL = "gpt-4.1-mini"
CATALOG_MODEL = "gpt-4.1"


def client() -> OpenAI:
    api_key = settings().openai_api_key
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to the .env file at the project root.")

    return OpenAI(api_key=api_key)
