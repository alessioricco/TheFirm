"""Brief proposals: strategy ideas from the senior quant, for the owner to approve.

The senior quant is a Claude Code subagent (`.claude/agents/senior-quant.md`)
outside the firm. It reads the web and the published literature and designs
strategies for the research floor to test. It has no tool that changes state
(firm rule 11): it returns proposals as text, the coding session saves them,
and this module checks them strictly. A proposal becomes work only when the
owner approves it, which opens a dossier with the proposal as its brief; from
there it is an idea like any other (hypothesis, trials, verdict).

What the senior quant may see of our results (owner, 2026-11-20, DECISIONS):
for each proposal it made, the dossier's stage, each verdict's outcome and the
names of the checks that failed. No metrics, parameters or code. Every export
of those outcomes is an OWNER_COMMAND in the ledger, tagged as a cloud egress
of private data (firm rule 8).

Storage: `<data root>/research/proposals/<id>.jsonl`, append-only events
(proposed, approved, rejected); a proposal is that log folded. Private.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AwareDatetime, Field, HttpUrl, ValidationError, field_validator

from firm.core.base import FirmModel
from firm.core.timeframes import tf_seconds

ZONE = Path("research") / "proposals"

# Method families the research floor works from (SPEC "Research floor").
FAMILIES = (
    "momentum",
    "mean_reversion",
    "pairs_stat_arb",
    "carry",
    "basis",
    "volatility_regime",
    "order_flow",
    "seasonality",
    "other",
)
# Families the paper simulator cannot test honestly (SPEC): refused.
OUT_OF_SCOPE = ("market_making", "high_frequency")


class ProposalError(ValueError):
    """A proposal that doesn't fit the schema, or a change its status doesn't allow."""


class Source(FirmModel):
    title: str = Field(min_length=3, max_length=300)
    url: HttpUrl
    accessed: date  # when the senior quant read it (web content is dated data)


class BriefProposal(FirmModel):
    """One strategy idea, written so a researcher can test it without asking."""

    title: str = Field(min_length=5, max_length=120)
    method_family: str
    market: str = Field(min_length=1, max_length=64)
    instruments: list[str] = Field(min_length=1, max_length=20)
    timeframes: list[str] = Field(min_length=1, max_length=6)
    hypothesis: str = Field(min_length=20, max_length=2000)
    why_edge: str = Field(min_length=20, max_length=2000)  # and who is on the other side
    rules: str = Field(min_length=20, max_length=4000)  # entries, exits, sizing, in words
    data_needed: str = Field(min_length=3, max_length=1000)
    falsified_if: str = Field(min_length=10, max_length=1000)
    expected_trades_per_month: int = Field(ge=1, le=10_000)
    cost_notes: str = Field(min_length=10, max_length=1000)
    sources: list[Source] = Field(min_length=1, max_length=20)

    @field_validator("method_family")
    @classmethod
    def _family(cls, v: str) -> str:
        if v in OUT_OF_SCOPE:
            raise ValueError(f"{v} is out of scope: the paper simulator can't test it honestly")
        if v not in FAMILIES:
            raise ValueError(f"unknown method family {v!r}; allowed: {', '.join(FAMILIES)}")
        return v

    @field_validator("timeframes")
    @classmethod
    def _timeframes(cls, v: list[str]) -> list[str]:
        for tf in v:
            tf_seconds(tf)  # raises on an unknown timeframe
        return v

    def sha256(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(body).hexdigest()

    def as_brief(self) -> str:
        """The dossier brief the researcher reads."""
        sources = "\n".join(f"- {s.title} ({s.url}, read {s.accessed})" for s in self.sources)
        return (
            f"{self.title} [{self.method_family}]\n\n"
            f"Hypothesis: {self.hypothesis}\n\n"
            f"Why the edge should exist: {self.why_edge}\n\n"
            f"Rules to test: {self.rules}\n\n"
            f"Data needed: {self.data_needed}\n\n"
            f"Falsified if: {self.falsified_if}\n\n"
            f"Expected trades per month: {self.expected_trades_per_month}. "
            f"Costs: {self.cost_notes}\n\n"
            f"Sources:\n{sources}"
        )


def parse_proposal(text: str, *, markets: set[str] | None = None) -> BriefProposal:
    """A proposal from YAML, strictly: unknown keys, a bad family, timeframe or
    market are errors."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ProposalError(f"not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise ProposalError("a proposal is a YAML mapping")
    try:
        p = BriefProposal.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(x) for x in err['loc']) or 'proposal'}: {err['msg']}"
            for err in e.errors()
        )
        raise ProposalError(problems) from None
    if markets is not None and p.market not in markets:
        raise ProposalError(f"unknown market {p.market!r}; profiles: {', '.join(sorted(markets))}")
    return p


Status = Literal["proposed", "approved", "rejected"]


class Proposal(FirmModel):
    proposal_id: str
    status: Status
    proposed_at: AwareDatetime
    proposal: BriefProposal
    sha256: str
    dossier_id: str | None = None
    rejected_reason: str | None = None


class ProposalStore:
    """Append-only proposal logs in the private store."""

    def __init__(self, root: Path, *, now=lambda: datetime.now(UTC)) -> None:  # type: ignore[no-untyped-def]
        self.dir = Path(root) / ZONE
        self._now = now

    def _path(self, proposal_id: str) -> Path:
        if not proposal_id.startswith("p-") or "/" in proposal_id or ".." in proposal_id:
            raise ProposalError(f"not a proposal ID: {proposal_id!r}")
        return self.dir / f"{proposal_id}.jsonl"

    def _append(self, proposal_id: str, event: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event | {"at": self._now().isoformat()}, sort_keys=True)
        with self._path(proposal_id).open("a") as f:
            f.write(line + "\n")

    def add(self, proposal: BriefProposal) -> tuple[Proposal, bool]:
        """Record a proposal; the same content twice is the same proposal (False)."""
        digest = proposal.sha256()
        for existing in self.list():
            if existing.sha256 == digest:
                return existing, False
        pid = f"p-{self._now():%Y%m%d}-{digest[:8]}"
        self._append(
            pid,
            {"event": "proposed", "sha256": digest, "proposal": proposal.model_dump(mode="json")},
        )
        return self.get(pid), True

    def approve(self, proposal_id: str, dossier_id: str) -> None:
        self._require_open(proposal_id)
        self._append(proposal_id, {"event": "approved", "dossier_id": dossier_id})

    def reject(self, proposal_id: str, reason: str) -> None:
        self._require_open(proposal_id)
        if not reason.strip():
            raise ProposalError("a rejection needs a reason")
        self._append(proposal_id, {"event": "rejected", "reason": reason.strip()})

    def _require_open(self, proposal_id: str) -> None:
        p = self.get(proposal_id)
        if p.status != "proposed":
            raise ProposalError(f"{proposal_id} is already {p.status}")

    def get(self, proposal_id: str) -> Proposal:
        path = self._path(proposal_id)
        if not path.exists():
            raise ProposalError(f"no proposal {proposal_id}")
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        first = events[0]
        data: dict[str, Any] = {
            "proposal_id": proposal_id,
            "status": "proposed",
            "proposed_at": first["at"],
            "proposal": first["proposal"],
            "sha256": first["sha256"],
        }
        for e in events[1:]:
            if e["event"] == "approved":
                data |= {"status": "approved", "dossier_id": e["dossier_id"]}
            elif e["event"] == "rejected":
                data |= {"status": "rejected", "rejected_reason": e["reason"]}
        return Proposal.model_validate(data)

    def list(self) -> list[Proposal]:
        if not self.dir.exists():
            return []
        out = [self.get(p.stem) for p in sorted(self.dir.glob("p-*.jsonl"))]
        return sorted(out, key=lambda p: (p.proposed_at, p.proposal_id))


def outcomes(
    store: ProposalStore,
    dossiers: Any,  # a DossierStore
    reports: Path,
) -> list[dict[str, Any]]:
    """What the senior quant may see of its approved proposals: the dossier's
    stage, each verdict's outcome and the names of the checks that failed or
    couldn't run. Nothing else (no metrics, parameters, code or notes)."""
    out: list[dict[str, Any]] = []
    for p in store.list():
        row: dict[str, Any] = {
            "proposal_id": p.proposal_id,
            "title": p.proposal.title,
            "status": p.status,
        }
        if p.status == "rejected":
            row["rejected_reason"] = p.rejected_reason
        if p.dossier_id:
            d = dossiers.get(p.dossier_id)
            row["stage"] = d.stage.value
            row["verdicts"] = [
                {"outcome": v.outcome, "checks_not_passed": _not_passed(reports, v.verdict_id)}
                for v in d.verdicts
            ]
        out.append(row)
    return out


def _not_passed(reports: Path, verdict_id: str) -> list[str]:
    path = reports / f"{verdict_id}.json"
    try:
        checks = json.loads(path.read_text())["checks"]
    except (OSError, ValueError, KeyError):
        return ["(report missing)"]
    return [c["name"] for c in checks if c.get("status") != "passed"]


def real_series(data_root: Path) -> list[tuple[str, str, str]]:
    """(instrument, timeframe, venue) of native bars in real (not synthetic)
    dataset versions. No versions, row counts or dates: what exists, not what
    we hold exactly."""
    from firm.data.dataset import DatasetManifest, capture_methods

    seen: set[tuple[str, str, str]] = set()
    for path in sorted((Path(data_root) / "datasets").glob("ds-*.json")):
        try:
            manifest = DatasetManifest.model_validate_json(path.read_text())
            if "synthetic" in capture_methods(Path(data_root), manifest.dataset_version):
                continue
        except Exception:
            continue
        for f in manifest.files:
            if f.kind == "bars" and f.tf and not f.derived_from:
                seen.add((f.instrument, f.tf, f.venue))
    return sorted(seen)


_FIRM_LIMITS = """\
What the firm can test now (design for this, or say what is missing):
- Spot only: a strategy outputs a target exposure in [0, 1] per bar for ONE
  instrument (long or flat; no shorting, no leverage, no derivatives).
- Bars only (OHLCV): it decides at a bar's close and fills at the next bar's
  open, paying the venue's taker fee, half the spread and slippage.
- A target of 1 holds {position_pct}% of equity.
- The verifier checks: lookahead truncation, cost realism (costs doubled too),
  parameter sensitivity, a Sharpe deflated by the firm's global trial count, and
  a hidden out-of-sample period opened once. Every backtest is a counted trial
  for the whole firm, so a few strong, well-argued ideas beat many weak ones.
- Not testable honestly: market making, high frequency, anything needing order
  book or tick history, funding rates, or several instruments at once."""


def context_pack(
    *,
    config_root: Path,
    data_root: Path,
    store: ProposalStore,
    dossiers: Any,
    reports: Path,
    with_outcomes: bool,
) -> tuple[str, int]:
    """The senior quant's whole view of the firm, as text: limits, markets and
    costs (public config), real series available, past proposals, and (only
    with the owner's opt-in) their outcomes. Returns (text, proposals with
    outcomes shown)."""
    from firm.core.config import load_market

    verify = yaml.safe_load((config_root / "verify" / "default.yaml").read_text())
    parts = [_FIRM_LIMITS.format(position_pct=verify["sizing"]["position_pct"]), "", "Markets:"]
    for path in sorted((config_root / "markets").glob("*.yaml")):
        m = load_market(path)
        venues = m.costs.commission
        fees = []
        for venue, schedule in venues.items() if isinstance(venues, dict) else [("all", venues)]:
            fee_path = config_root / "fees" / f"{schedule}.yaml"
            if fee_path.exists():
                taker = yaml.safe_load(fee_path.read_text()).get("taker_bps")
                fees.append(f"{venue} taker {taker} bps")
            else:
                fees.append(f"{venue}: fee schedule {schedule} not configured yet")
        parts.append(
            f"- {m.market}: calendar {m.calendar}, quoted in {m.currency}; "
            f"{'; '.join(fees)}; spread {m.costs.spread_bps} bps, "
            f"slippage {m.costs.slippage_bps} bps"
        )
    series = real_series(data_root)
    parts += ["", "Real data in the store (instrument, timeframe, venue):"]
    parts += [f"- {i} {tf} {v}" for i, tf, v in series] or ["- none yet"]
    proposals = store.list()
    parts += ["", "Past proposals (don't repeat them):"]
    parts += [
        f"- {p.proposal_id} [{p.status}] {p.proposal.method_family}: {p.proposal.title}"
        + (f" (rejected: {p.rejected_reason})" if p.rejected_reason else "")
        for p in proposals
    ] or ["- none yet"]
    shown = 0
    if with_outcomes:
        rows = [r for r in outcomes(store, dossiers, reports) if "stage" in r]
        shown = len(rows)
        parts += ["", "Outcomes of approved proposals (stage, verdicts, checks not passed):"]
        parts += [
            f"- {r['proposal_id']}: {r['stage']}; "
            + (
                "; ".join(
                    f"{v['outcome']} ({', '.join(v['checks_not_passed']) or 'all passed'})"
                    for v in r["verdicts"]
                )
                or "no verdict yet"
            )
            for r in rows
        ] or ["- none yet"]
    return "\n".join(parts), shown
