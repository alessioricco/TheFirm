"""Telegram delivery to the owner: one chat, no recipient choice, no secret in any
error or log. A fake transport stands in for the Bot API (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from firm.core.domain import Notification, NotificationLevel
from firm.core.env import load_env_file
from firm.notify.telegram import TelegramError, TelegramNotifier, chat_ids, telegram_from_env

TOKEN = "123456:SECRET-token-value"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


class FakeApi:
    def __init__(self, reply: dict | None = None, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.reply = reply if reply is not None else {"ok": True, "result": {}}
        self.fail = fail

    def __call__(self, url: str, body: bytes | None) -> bytes:
        self.calls.append((url, json.loads(body) if body else None))
        if self.fail:
            raise self.fail
        return json.dumps(self.reply).encode()


def note(text: str = "feed stale", level=NotificationLevel.URGENT) -> Notification:
    return Notification(notification_id="n-1", level=level, text=text, created_at=NOW)


def test_send_posts_to_the_one_owner_chat() -> None:
    api = FakeApi()
    TelegramNotifier(TOKEN, "42", transport=api).send(note())
    ((url, body),) = api.calls
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert body["chat_id"] == "42"
    assert "URGENT" in body["text"] and "feed stale" in body["text"]


def test_errors_never_carry_the_token() -> None:
    api = FakeApi(fail=OSError(f"connection reset talking to /bot{TOKEN}/sendMessage"))
    with pytest.raises(TelegramError) as exc:
        TelegramNotifier(TOKEN, "42", transport=api).send(note())
    assert TOKEN not in str(exc.value) and "SECRET" not in str(exc.value)

    refused = FakeApi(reply={"ok": False, "description": "Bad Request: chat not found"})
    with pytest.raises(TelegramError, match="chat not found"):
        TelegramNotifier(TOKEN, "42", transport=refused).send(note())


def test_chat_ids_lists_who_wrote_to_the_bot() -> None:
    api = FakeApi(
        reply={
            "ok": True,
            "result": [
                {
                    "message": {
                        "chat": {"id": 42, "type": "private", "first_name": "A"},
                        "text": "hi",
                    }
                },
                {
                    "message": {
                        "chat": {"id": 42, "type": "private", "first_name": "A"},
                        "text": "x",
                    }
                },
                {"edited_message": {"chat": {"id": -7, "type": "group", "title": "G"}}},
            ],
        }
    )
    assert chat_ids(TOKEN, transport=api) == [("42", "private", "A"), ("-7", "group", "G")]
    assert api.calls[0][0].endswith("/getUpdates")


def test_configured_only_with_both_token_and_chat() -> None:
    assert telegram_from_env({}) == (None, "TELEGRAM_BOT_TOKEN is not set")
    n, why = telegram_from_env({"TELEGRAM_BOT_TOKEN": TOKEN})
    assert n is None and "TELEGRAM_OWNER_CHAT_ID" in why and TOKEN not in why
    n, why = telegram_from_env({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_OWNER_CHAT_ID": "42"})
    assert isinstance(n, TelegramNotifier) and why == ""
    assert TOKEN not in repr(n)


def test_env_file_fills_only_missing_keys(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text(
        "# comment\nA=1\nB = 'two'\nexport C=\"three\"\nEMPTY=\nKEEP=file\nnot a line\n",
        encoding="utf-8",
    )
    env = {"KEEP": "shell"}
    load_env_file(f, env)
    assert env == {"A": "1", "B": "two", "C": "three", "KEEP": "shell"}
    load_env_file(tmp_path / "missing", env)  # no file: nothing happens
