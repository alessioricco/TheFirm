"""Telegram Bot API delivery to the owner's one chat (firm rule 10).

The chat is fixed by `TELEGRAM_OWNER_CHAT_ID`; `send` has no recipient
argument. The bot reads nothing in v1 except `chat_ids`, the owner's helper
to find that ID once. The token is in the URL Telegram requires, so every
error is rebuilt without it, and the notifier's repr hides it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from firm.core.domain import Notification

API = "https://api.telegram.org"
# names of the environment variables (the values live in .env, never in git)
TOKEN_ENV, CHAT_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID")
TIMEOUT_SECONDS = 10

Transport = Callable[[str, bytes | None], bytes]  # (url, JSON body or None for GET) -> body


class TelegramError(RuntimeError):
    """A failed call; its message never contains the token."""


def http_call(url: str, body: bytes | None) -> bytes:
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        data: bytes = resp.read()
    return data


def _call(token: str, method: str, body: dict[str, Any] | None, transport: Transport) -> Any:
    url = f"{API}/bot{token}/{method}"
    try:
        raw = transport(url, json.dumps(body).encode() if body is not None else None)
    except urllib.error.HTTPError as exc:  # Telegram explains refusals in the body
        try:
            raw = exc.read()
        except Exception:
            raise TelegramError(f"{method}: HTTP {exc.code}") from None
    except Exception as exc:
        detail = str(exc).replace(token, "<token>")
        raise TelegramError(f"{method}: {type(exc).__name__}: {detail}") from None
    try:
        reply = json.loads(raw)
    except ValueError:
        raise TelegramError(f"{method}: reply is not JSON") from None
    if not reply.get("ok"):
        desc = str(reply.get("description", "refused")).replace(token, "<token>")
        raise TelegramError(f"{method}: {desc}")
    return reply.get("result")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, *, transport: Transport = http_call) -> None:
        self._token, self._chat, self._transport = token, chat_id, transport

    def __repr__(self) -> str:
        return "TelegramNotifier(<owner chat>)"

    def send(self, notification: Notification) -> None:
        text = f"[{notification.level.value.upper()}] {notification.text}"
        if notification.links:
            text += "\n" + "\n".join(notification.links)
        _call(
            self._token,
            "sendMessage",
            {"chat_id": self._chat, "text": text, "disable_web_page_preview": True},
            self._transport,
        )


def chat_ids(token: str, *, transport: Transport = http_call) -> list[tuple[str, str, str]]:
    """(chat id, type, name) of every chat that recently wrote to the bot."""
    seen: dict[str, tuple[str, str, str]] = {}
    for update in _call(token, "getUpdates", None, transport) or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if chat:
                cid = str(chat["id"])
                name = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
                seen.setdefault(cid, (cid, str(chat.get("type", "")), str(name)))
    return list(seen.values())


def telegram_from_env(env: Mapping[str, str]) -> tuple[TelegramNotifier | None, str]:
    """The owner's notifier, or None and why (never echoing a secret)."""
    token = env.get(TOKEN_ENV, "").strip()
    chat = env.get(CHAT_ENV, "").strip()
    if not token:
        return None, f"{TOKEN_ENV} is not set"
    if not chat:
        return None, f"{CHAT_ENV} is not set (find it with `firm notify chat-id`)"
    return TelegramNotifier(token, chat), ""
