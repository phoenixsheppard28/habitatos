"""A fake Messages API. The real SDK tool runner talks to it through an httpx2 mock transport."""

import json
from itertools import count

import anthropic
import httpx2


def tool_use(name: str, **arguments) -> dict:
    return {"type": "tool_use", "name": name, "input": arguments}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


class FakeClaude:
    """Answers each request with the next scripted turn. A turn is a list of content blocks."""

    def __init__(self, turns: list[list[dict]], repeat_last: bool = False):
        self.turns = list(turns)
        self.repeat_last = repeat_last
        self.requests: list[dict] = []
        self.ids = count(1)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        blocks = self.turns.pop(0) if len(self.turns) > 1 or not self.repeat_last else self.turns[0]
        content = [{**block, "id": f"toolu_{next(self.ids)}"} if block["type"] == "tool_use" else block for block in blocks]
        stop_reason = "tool_use" if any(block["type"] == "tool_use" for block in content) else "end_turn"
        return httpx2.Response(200, json={
            "id": f"msg_{next(self.ids)}", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    def client(self) -> anthropic.Anthropic:
        transport = httpx2.MockTransport(self.handle)
        return anthropic.Anthropic(api_key="test", http_client=anthropic.DefaultHttpxClient(transport=transport))

    def tool_results(self) -> list[str]:
        return [
            block["content"] for request in self.requests for message in request["messages"]
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"] if block.get("type") == "tool_result"
        ]
