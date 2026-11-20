"""Build a self-contained demo for the dashboard: synthetic data only.

    uv run python scripts/dashboard_demo.py                # writes ./data-demo-dashboard/
    uv run firm dashboard serve --root data-demo-dashboard/data \\
        --state-root data-demo-dashboard/state
    uv run firm dashboard url --state-root data-demo-dashboard/state

It writes a synthetic data store (Kraken BTC-USD daily bars, made up), a
real verification of the example strategy on it, a paper-run history in the
ledger (an account, fills, feeds, a heartbeat that was lost and came back),
research dossiers in every stage, and senior-quant proposals, so every panel has something to show.
Nothing here is real data, a real result or a real trade; the folder is
git-ignored (`data-*/`). Delete it when you're done.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from firm.control.paper import OPENED
from firm.core.config import load_research_config
from firm.core.domain import (
    Fill,
    LedgerEventType,
    OrderIntent,
    OrderProposal,
    OrderType,
    RiskAction,
    RiskDecision,
    Side,
)
from firm.data.dataset import build_dataset, open_dataset
from firm.data.normalize import normalize_bars
from firm.data.synthetic import seed_store
from firm.ledger.sink import LedgerSink, ledger_path
from firm.ledger.store import Ledger
from firm.research.dossier import DossierStore, FloorBriefs
from firm.research.store import save_strategy
from firm.verify.runner import verify_strategy_file

REPO = Path(__file__).resolve().parents[1]
STRATEGY = REPO / "examples" / "strategies" / "ma_crossover.py"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=7)
        return self.now


def build(out: Path) -> tuple[Path, Path]:
    data, state = out / "data", out / "state"
    now = datetime.now(UTC)

    # 1. a synthetic store, a dataset version, a real verification on it
    seed_store(data, n=1500, seed=11)
    files = normalize_bars(data, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    version = build_dataset(data, files).dataset_version
    led = Ledger(ledger_path(state))
    sink = LedgerSink(led)
    result = verify_strategy_file(
        strategy_path=STRATEGY,
        class_name="MovingAverageCrossover",
        params=None,
        data_root=data,
        dataset_version=version,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        config_root=REPO / "config",
        jurisdiction="uk",
        market="crypto",
        state=state,
        now=now,
        sink=sink,
    )
    verdict = result.verdict.verdict_id
    (state / "reports").mkdir(parents=True, exist_ok=True)
    (state / "reports" / f"{verdict}.json").write_bytes(result.report)

    # 2. a paper-run history over the last bars of that dataset
    bars = open_dataset(data, version).bars("kraken", "BTC-USD", "1d", as_of=now).tail(160)
    t0 = bars["close_time"].iloc[0].to_pydatetime()
    ms = LedgerEventType.MARKET_STATUS
    led.append(
        ms,
        "paper account opened",
        {
            "kind": OPENED,
            "market": "crypto",
            "venue": "kraken",
            "balances": {"USD": "6000"},
            "at": t0.isoformat(),
        },
    )
    led.append(
        ms,
        f"crypto started: verdict {verdict}",
        {
            "kind": "started",
            "market": "crypto",
            "venue": "kraken",
            "verdict_id": verdict,
            "at": t0.isoformat(),
        },
    )
    n = 0

    def order(side: Side, qty: str, i: int) -> None:
        nonlocal n
        n += 1
        at = bars["close_time"].iloc[i].to_pydatetime()
        price = Decimal(str(round(float(bars["open"].iloc[min(i + 1, len(bars) - 1)]), 2)))
        q = Decimal(qty)
        common = {
            "verdict_id": verdict,
            "market": "crypto",
            "venue": "kraken",
            "instrument": "BTC-USD",
            "side": side,
            "order_type": OrderType.MARKET,
            "quantity": q,
        }
        p = OrderProposal(
            proposal_id=f"p-demo-{n}",
            strategy_id="ma_crossover",
            reference_price=price,
            quote_currency="USD",
            created_at=at,
            **common,
        )
        led.append(
            LedgerEventType.ORDER_PROPOSAL,
            f"{side.value} {qty} BTC-USD: new target",
            p.model_dump(mode="json"),
            event_id=f"proposal:p-demo-{n}",
        )
        d = RiskDecision(
            decision_id=f"r-demo-{n}",
            proposal_id=p.proposal_id,
            verdict_id=verdict,
            action=RiskAction.APPROVE,
            reason="within limits",
            approved_quantity=q,
            decided_at=at,
        )
        led.append(
            LedgerEventType.RISK_DECISION,
            d.reason,
            d.model_dump(mode="json"),
            event_id=f"r:demo-{n}",
        )
        intent = OrderIntent(
            intent_id=f"i-demo-{n}",
            client_order_id=f"c-demo-{n}",
            proposal_id=p.proposal_id,
            risk_decision_id=d.decision_id,
            strategy_id="ma_crossover",
            created_at=at + timedelta(seconds=1),
            **common,
        )
        led.append(
            LedgerEventType.ORDER_INTENT,
            f"order {side.value} {qty} BTC-USD",
            intent.model_dump(mode="json"),
            event_id=f"i:demo-{n}",
        )
        f = Fill(
            fill_id=f"f-demo-{n}",
            client_order_id=intent.client_order_id,
            intent_id=intent.intent_id,
            verdict_id=verdict,
            risk_decision_id=d.decision_id,
            venue="kraken",
            instrument="BTC-USD",
            side=side,
            quantity=q,
            price=price,
            fee=(q * price * Decimal("0.0026")).quantize(Decimal("0.01")),
            fee_currency="USD",
            filled_at=at + timedelta(minutes=1),
        )
        led.append(
            LedgerEventType.FILL,
            f"fill {side.value} {qty} BTC-USD @ {price}",
            f.model_dump(mode="json"),
            event_id=f"fill:demo-{n}",
        )

    qty = str((Decimal("1200") / Decimal(str(bars["close"].iloc[20]))).quantize(Decimal("0.0001")))
    for buy, sell in ((20, 48), (70, 95), (118, 140)):
        order(Side.BUY, qty, buy)
        order(Side.SELL, qty, sell)
    order(Side.BUY, qty, 150)  # still open

    last = bars["close_time"].iloc[-1].to_pydatetime()
    fs = LedgerEventType.FEED_STATUS
    for provider, symbol, status in (
        ("kraken_ws", "GBP/USD", "connected"),
        ("kraken_ws", "USDT/USD", "connected"),
        ("kraken_ws", "BTC/USD", "fresh"),
    ):
        led.append(
            fs,
            f"{provider} {symbol} {status}",
            {
                "provider": provider,
                "symbol": symbol,
                "status": status,
                "detail": "",
                "at": last.isoformat(),
            },
        )
    led.append(
        ms,
        "risk heartbeat lost for over 10s: no new orders",
        {
            "kind": "heartbeat_lost",
            "market": "crypto",
            "at": (last - timedelta(hours=3)).isoformat(),
        },
    )
    led.append(
        ms,
        "risk heartbeat back",
        {
            "kind": "heartbeat_restored",
            "market": "crypto",
            "at": (last - timedelta(hours=3, minutes=-1)).isoformat(),
        },
    )

    # 3. the research floor: dossiers in every stage
    budget = load_research_config(REPO / "config").budgets
    store = DossierStore(data, sink=sink, clock=Clock(now - timedelta(days=3)))

    def open_(i: str, brief: str, tfs: list[str]) -> str:
        store.open(
            i, brief=brief, market="crypto", instruments=["BTC-USD"], timeframes=tfs, budget=budget
        )
        return i

    def trials(i: str, k: int) -> None:
        for j in range(k):
            store.add_trial(i, f"t-{i[-6:]}{j:010x}"[:18])

    d = open_(
        "d-demo-momentum",
        "Momentum after breakouts: does a close above the 20-bar high predict the next two weeks?",
        ["1d", "4h"],
    )
    store.write_hypothesis(
        d,
        "Breakouts above a 20-bar high are followed by positive drift for 5 to 10 bars.",
        "Slow-moving capital and trend followers (published time-series momentum literature).",
    )
    store.add_code(d, save_strategy(data, d, STRATEGY.read_bytes(), stem="breakout"))
    trials(d, 64)
    store.note(d, "Costs eat most of the edge on 4h; daily looks more robust.")

    d = open_("d-demo-meanrev", "Mean reversion after large daily drops in BTC.", ["1d"])
    store.write_hypothesis(
        d, "A daily drop beyond 2.5 sigma reverts within 3 bars.", "Liquidity-driven overshoots."
    )
    trials(d, 41)
    store.record_submission(d, "c-demo-meanrev")
    store.record_verdict(d, "v-demo-meanrev-pass", "pass")

    d = open_(
        "d-demo-vol",
        "Volatility regimes: stay flat when realised volatility is in its top decile.",
        ["1d"],
    )
    store.write_hypothesis(
        d,
        "High-volatility regimes have worse risk-adjusted returns for trend rules.",
        "Volatility clustering; published volatility-managed portfolios.",
    )
    trials(d, 12)
    store.pause(d, "owner: wait for the momentum dossier")

    d = open_(
        "d-demo-carry", "Funding-rate carry on perpetuals (needs funding data first).", ["1h"]
    )

    d = open_("d-demo-seasonal", "Day-of-week effects in BTC.", ["1d"])
    store.write_hypothesis(
        d, "Weekend returns differ from weekday returns.", "Lower weekend liquidity."
    )
    trials(d, 30)
    store.record_submission(d, "c-demo-seasonal")
    store.record_verdict(d, "v-demo-seasonal-fail", "fail")

    d = open_("d-demo-pairs", "Pairs: BTC against ETH spread reversion.", ["1h"])
    store.close(d, "owner: needs a second instrument in the store")

    for i, tokens in (
        ("d-demo-momentum", 182_000),
        ("d-demo-meanrev", 96_500),
        ("d-demo-vol", 21_000),
    ):
        led.append(
            LedgerEventType.MODEL_CALL,
            f"model call: research for {i} (private)",
            {
                "outcome": "ok",
                "role": "research",
                "provider": "ollama",
                "model": "demo-model",
                "local": True,
                "tag": "private",
                "dossier_id": i,
                "input_tokens": tokens * 3 // 4,
                "output_tokens": tokens // 4,
                "cost_gbp": 0.0,
                "at": now.isoformat(),
            },
        )
    FloorBriefs(data).set(
        "Prefer daily and 4h bars; stay uncorrelated with the running MA crossover."
    )
    _demo_proposals(data)
    return data, state


def _demo_proposals(data: Path) -> None:
    """Made-up senior-quant proposals (demo text, example.org sources)."""
    from firm.research.proposals import BriefProposal, ProposalStore

    store = ProposalStore(data)

    def idea(title: str, family: str, tf: str, trades: int, **text: str) -> str:
        p, _ = store.add(
            BriefProposal.model_validate(
                {
                    "title": title,
                    "method_family": family,
                    "market": "crypto",
                    "instruments": ["BTC-USD"],
                    "timeframes": [tf],
                    "hypothesis": text["hypothesis"],
                    "why_edge": text["why"],
                    "rules": text["rules"],
                    "data_needed": f"{tf} bars, three years or more",
                    "falsified_if": "Net Sharpe under 0.5 after the venue's costs, "
                    "or the edge disappears when parameters move by 20%.",
                    "expected_trades_per_month": trades,
                    "cost_notes": "Demo proposal: few trades a month, so taker fees stay small.",
                    "sources": [
                        {
                            "title": "Demo source (not a real paper)",
                            "url": "https://example.org/demo-paper",
                            "accessed": "2026-11-20",
                        }
                    ],
                }
            )
        )
        return p.proposal_id

    idea(
        "Demo: volatility-scaled trend on BTC 4h",
        "momentum",
        "4h",
        6,
        hypothesis="Demo: the sign of the 30-day return predicts the next week's sign.",
        why="Demo: slow-moving flows and under-reaction to news.",
        rules="Demo: target 1 when the 30-day return is positive and volatility is "
        "below its one-year median; else 0. Parameters: lookback 30 (20-60).",
    )
    idea(
        "Demo: weekend gap reversal on BTC 1d",
        "mean_reversion",
        "1d",
        3,
        hypothesis="Demo: large weekend moves partly reverse on Monday.",
        why="Demo: thin weekend liquidity overshoots.",
        rules="Demo: buy at Sunday's close after a weekend fall over 2 standard "
        "deviations; sell at Tuesday's close.",
    )
    rejected = idea(
        "Demo: halving-cycle seasonality",
        "seasonality",
        "1w",
        1,
        hypothesis="Demo: returns follow the four-year halving cycle.",
        why="Demo: supply schedule narratives.",
        rules="Demo: hold from 6 months before to 12 months after each halving.",
    )
    store.reject(rejected, "only three cycles of history: can't be verified honestly")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out", default="data-demo-dashboard", help="folder to write (git-ignored data-* name)"
    )
    ap.add_argument("--force", action="store_true", help="replace the folder if it exists")
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        if not args.force:
            print(f"{out} exists: use --force to replace it", file=sys.stderr)
            return 3
        shutil.rmtree(out)
    data, state = build(out)
    print(json.dumps({"data": str(data), "state": str(state)}))
    print(f"\nnow run:\n  uv run firm dashboard serve --root {data} --state-root {state}")
    print(f"  uv run firm dashboard url --state-root {state}     # open the printed link")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
