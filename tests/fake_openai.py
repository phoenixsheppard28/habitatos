"""Scripted Chat Completions API over a mock HTTP transport."""

import json
from itertools import count

import httpx
from openai import OpenAI


def tool_use(name: str, **arguments) -> dict:
    return {"name": name, "arguments": arguments}


def text(value: str) -> str:
    return value


class FakeOpenAI:
    def __init__(self, turns: list[list[dict] | str], repeat_last: bool = False):
        self.turns = list(turns)
        self.repeat_last = repeat_last
        self.requests: list[dict] = []
        self.ids = count(1)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        turn = self.turns.pop(0) if len(self.turns) > 1 or not self.repeat_last else self.turns[0]
        calls = [{"id": f"call_{next(self.ids)}", "type": "function",
                  "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])}}
                 for call in turn] if isinstance(turn, list) else []
        message = {"role": "assistant", "content": turn if isinstance(turn, str) else None,
                   "tool_calls": calls or None, "refusal": None}

        return httpx.Response(200, json={
            "id": f"chatcmpl_{next(self.ids)}", "object": "chat.completion", "created": 1,
            "model": "gpt-4.1-mini", "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def client(self) -> OpenAI:
        return OpenAI(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(self.handle)))

    def tool_results(self) -> list[str]:
        return [message["content"] for request in self.requests for message in request["messages"]
                if message["role"] == "tool"]
