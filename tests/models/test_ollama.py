"""The Ollama adapter: one non-streaming /api/chat request, tokens from the reply.

No network in tests: the transport is a fake. The host comes from the
caller (OLLAMA_HOST in .env, read by the composition root), never from code.
"""

from __future__ import annotations

import pytest

from firm.models.ollama import OllamaProvider


def test_a_chat_request_and_its_token_counts() -> None:
    sent = {}

    def post(url, body, *, timeout):
        sent.update(url=url, body=body, timeout=timeout)
        return {
            "message": {"role": "assistant", "content": "hello"},
            "prompt_eval_count": 42,
            "eval_count": 7,
            "done": True,
        }

    p = OllamaProvider("http://model-host:11434/", post=post)
    out = p.chat("some-model", [{"role": "user", "content": "hi"}], timeout=30)
    assert p.local is True
    assert sent["url"] == "http://model-host:11434/api/chat"
    assert sent["body"] == {
        "model": "some-model",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }
    assert sent["timeout"] == 30
    assert (out.text, out.input_tokens, out.output_tokens, out.cost_gbp) == ("hello", 42, 7, 0.0)


def test_an_unfinished_or_malformed_reply_is_an_error() -> None:
    p = OllamaProvider("http://h:1", post=lambda *_a, **_k: {"error": "model not found"})
    with pytest.raises(RuntimeError, match="model not found"):
        p.chat("m", [], timeout=1)
