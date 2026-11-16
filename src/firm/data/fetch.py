"""Run a provider's request plan and store every response as a raw capture.

This is the only module that talks to the network, and only when a caller
passes the real `http_get` (the owner-run `scripts/download_data.py` does).
Tests pass a fake transport; the suite blocks sockets anyway.

- Transient failures (incomplete reads, connection errors, timeouts, HTTP
  429 and 5xx) are retried with exponential backoff; other HTTP errors fail
  at once.
- Between requests it waits the provider's `min_interval_s`.
- Every response is stored exactly as received (`write_capture`), with the
  provider's licence and price adjustment in its `.meta.json`. Re-runs are
  safe: captures are write-once and content-hashed, and the normaliser
  merges overlaps.
"""

from __future__ import annotations

import http.client
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from firm.data import providers
from firm.data.reference import InstrumentRecord, latest_instruments, write_instrument
from firm.data.store import Capture, write_capture

USER_AGENT = "firm-data-downloader/0.2"

Transport = Callable[[str], bytes]
Clock = Callable[[], datetime]
Sleep = Callable[[float], None]


def http_get(
    url: str,
    *,
    attempts: int = 5,
    sleep: Sleep = time.sleep,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> bytes:
    """GET with retries on transient failures only."""
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener(req, timeout=30) as resp:  # type: ignore[attr-defined]
                body: bytes = resp.read()
            return body
        except urllib.error.HTTPError as exc:
            if (exc.code != 429 and exc.code < 500) or attempt == attempts:
                raise
            why = f"HTTP {exc.code}"
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts:
                raise
            why = type(exc).__name__
        wait = 2**attempt
        print(f"  retry {attempt}/{attempts - 1} in {wait}s ({why})", file=sys.stderr)
        sleep(wait)
    raise AssertionError("unreachable")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Fetcher:
    """Runs plans for one store. `transport`, `clock`, `sleep` are injectable."""

    root: Path
    transport: Transport = http_get
    clock: Clock = _now
    sleep: Sleep = time.sleep
    log: Callable[[str], None] = print
    captures: list[Capture] = field(default_factory=list)

    def _store(
        self, provider: providers.Provider, req: providers.Request, symbol: str, tf: str | None
    ) -> bytes:
        body = self.transport(req.url)
        captured_at = self.clock()
        cap = write_capture(
            self.root,
            body,
            provider=provider.name,
            kind=req.kind,
            provider_symbol=symbol,
            tf=tf,
            fmt="json",
            captured_at=captured_at,
            request={"url": req.url},
            via="urllib",
            licence=provider.licence,
            price_adjustment=provider.price_adjustment,
        )
        self.captures.append(cap)
        self.log(f"{req.kind}: {len(body):>8} bytes -> {cap.path.relative_to(self.root)}")
        self.sleep(provider.min_interval_s)
        return body

    def bars(
        self, provider_name: str, symbol: str, tf: str, *, start: datetime | None, end: datetime
    ) -> int:
        provider = providers.get(provider_name)
        plan = provider.bar_requests(symbol, tf, start=start, end=end)
        for req in plan:
            self._store(provider, req, symbol, tf)
        return len(plan)

    def trades(self, provider_name: str, symbol: str, *, pages: int) -> int:
        provider = providers.get(provider_name)
        provider.supports("trades")
        cursor: str | None = None
        done = 0
        for _ in range(pages):
            body = self._store(provider, provider.trade_request(symbol, cursor), symbol, None)
            done += 1
            cursor = provider.next_trade_cursor(body)
            if cursor is None:
                break
        return done


def plan(
    provider_name: str, symbol: str, tf: str, *, start: datetime | None, end: datetime
) -> list[providers.Request]:
    """The bar requests that `Fetcher.bars` would make (for dry runs)."""
    return providers.get(provider_name).bar_requests(symbol, tf, start=start, end=end)


def ensure_instrument(
    root: Path,
    *,
    provider: str,
    symbol: str,
    venue: str,
    instrument: str,
    asset_class: Literal["crypto", "equity"],
    listed_at: datetime | None,
    quote: str | None = None,
    clock: Clock = _now,
) -> InstrumentRecord:
    """Return the instrument record, creating it the first time (needs `listed_at`)."""
    existing = latest_instruments(root).get((venue, instrument))
    if existing is not None:
        rec = existing[1]
        if rec.provider_symbols.get(provider) not in (None, symbol):
            raise ValueError(
                f"{venue}:{instrument} already maps {provider} to "
                f"{rec.provider_symbols[provider]!r}, not {symbol!r}"
            )
        if provider in rec.provider_symbols:
            return rec
        rec = rec.model_copy(
            update={
                "provider_symbols": rec.provider_symbols | {provider: symbol},
                "known_at": clock(),
            }
        )
        write_instrument(root, rec)
        return rec
    if listed_at is None:
        raise ValueError(f"first download of {venue}:{instrument}: pass the listing date")
    if asset_class == "crypto":
        base, quote = instrument.split("-")
    else:
        if quote is None:
            raise ValueError(f"equity {instrument}: pass its quote currency (e.g. USD)")
        base = instrument.split(".")[0]
    rec = InstrumentRecord(
        venue=venue,
        instrument=instrument,
        asset_class=asset_class,
        base=base,
        quote=quote,
        provider_symbols={provider: symbol},
        listed_at=listed_at,
        known_at=clock(),
    )
    write_instrument(root, rec)
    return rec
