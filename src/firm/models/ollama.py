"""Ollama adapter: a local model server (the DGX), reached at a host from config.

One non-streaming `/api/chat` request per call; tokens come from Ollama's
own counts (`prompt_eval_count`, `eval_count`). Local, so it costs £0 in
money; its real cost is DGX time and tokens, which the gateway records.
The host is passed in (OLLAMA_HOST, read by the composition root), never
written in code.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from firm.models.gateway import Completion, Message

Post = Callable[..., dict[str, Any]]


def post_json(url: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class OllamaProvider:
    local = True

    def __init__(self, host: str, *, post: Post = post_json) -> None:
        self.url = host.rstrip("/") + "/api/chat"
        self._post = post

    def chat(self, model: str, messages: Sequence[Message], *, timeout: float) -> Completion:
        body = {"model": model, "messages": [dict(m) for m in messages], "stream": False}
        reply = self._post(self.url, body, timeout=timeout)
        if "error" in reply or "message" not in reply:
            raise RuntimeError(f"ollama: {reply.get('error', 'malformed reply')}")
        return Completion(
            text=reply["message"].get("content", ""),
            input_tokens=int(reply.get("prompt_eval_count", 0)),
            output_tokens=int(reply.get("eval_count", 0)),
            cost_gbp=0.0,
        )
