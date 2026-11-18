"""Jurisdiction and market profiles, and their strict loader.

Country, markets, venues, data vendors and costs live in YAML under
`config/`, never in code (golden rule 5). The loader refuses unknown keys
and refuses `live_trading: true`: v1 is paper only (firm rule 7).

Three kinds of profile:

- `config/jurisdictions/<name>.yaml`: where the owner lives (base currency,
  allowed venues and their regulatory status, tax, live_trading switch).
- `config/markets/<name>.yaml`: what is traded (calendar, data, costs,
  broker). Agents may read these (research needs costs and calendars).
- `config/risk/<name>.yaml`: limits and caps. Agents may neither read nor
  write risk or jurisdiction profiles (firm rule 3).
- `config/fees/<name>.yaml`: fee schedules named by a market's `costs.commission`.
- `config/models.yaml` (`config/models/<name>.yaml` later): model per role,
  Q&A approach and egress policy. Hosts and keys stay in `.env`.
- `config/verify/<name>.yaml`: verifier thresholds. No agent may write
  them: a lower bar would let an agent pass its own strategy.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from firm.core.base import CurrencyCode, FirmModel, Id

V1_LIVE_TRADING_ALLOWED = False
"""v1 refuses live trading outright. Turning it on later needs a code change
reviewed by the owner *and* the config switch *and* a manual UI approval."""

Bps = Annotated[float, Field(ge=0)]


class ConfigError(ValueError):
    """A profile is missing, malformed, or not allowed."""


# --------------------------------------------------------------------------- jurisdiction


class TaxConfig(FirmModel):
    report: Id
    dividend_withholding: Id


class NotificationsConfig(FirmModel):
    recipients: tuple[Literal["owner"], ...] = Field(min_length=1)


class VenueRegulatoryStatus(StrEnum):
    """Generic across jurisdictions; the regulator is named separately."""

    AUTHORISED = "authorised"  # full authorisation / licence
    REGISTERED = "registered"  # registration only (e.g. a crypto AML register)
    PENDING = "pending"  # applied, not yet decided
    NOT_PERMITTED = "not_permitted"


class VenueStatus(FirmModel):
    """A venue's standing in this jurisdiction, as checked on `as_of`."""

    regulator: Id
    status: VenueRegulatoryStatus
    as_of: date
    review_by: date | None = None  # e.g. a known regime change
    note: str = ""

    @model_validator(mode="after")
    def _dates_ordered(self) -> Self:
        if self.review_by is not None and self.review_by < self.as_of:
            raise ValueError("review_by is before as_of")
        return self


class JurisdictionProfile(FirmModel):
    jurisdiction: Id
    base_currency: CurrencyCode
    allowed_venues: tuple[Id, ...] = Field(min_length=1)
    venue_status: dict[Id, VenueStatus]
    leverage_max: float = Field(ge=0.0, le=1.0)  # no leverage in v1
    tax: TaxConfig
    notifications: NotificationsConfig
    live_trading: bool

    @model_validator(mode="after")
    def _allowed_venues_have_status(self) -> Self:
        for venue in self.allowed_venues:
            st = self.venue_status.get(venue)
            if st is None:
                raise ValueError(f"allowed venue {venue!r} has no venue_status entry")
            if st.status is VenueRegulatoryStatus.NOT_PERMITTED:
                raise ValueError(f"venue {venue!r} is not_permitted but listed as allowed")
        return self

    @field_validator("live_trading")
    @classmethod
    def _paper_only(cls, v: bool) -> bool:
        if v and not V1_LIVE_TRADING_ALLOWED:
            raise ValueError("live_trading: true is refused in v1 (paper only)")
        return v


# --------------------------------------------------------------------------- market


class UniverseConfig(FirmModel):
    source: Id
    include_delisted: Literal[True]  # delisted instruments stay in the universe forever


class DataConfig(FirmModel):
    history: Id | tuple[Id, ...]
    realtime: Id | tuple[Id, ...]
    stale_after_seconds: int = Field(gt=0)  # no trade for this long: the feed is stale


class CostsConfig(FirmModel):
    # a fee schedule in config/fees/: one name for all venues, or venue -> name
    commission: Id | dict[Id, Id]
    spread_model: Literal["quoted"]
    spread_bps: Bps  # full spread assumed when no quote data (bar backtests)
    slippage_bps: Bps
    fx_conversion_bps: Bps

    def fee_schedule_for(self, venue: str) -> str:
        if isinstance(self.commission, str):
            return self.commission
        try:
            return self.commission[venue]
        except KeyError:
            raise ValueError(f"no fee schedule for venue {venue!r}") from None


class FxConfig(FirmModel):
    """Where the rate from this market's currency to the base currency comes from.

    A live market price (e.g. Kraken's GBP/USD), never a typed-in number; a rate
    older than `max_age_seconds` is unusable and risk rejects what it can't value."""

    provider: Id  # a stream provider (firm.data.streams)
    symbol: Id  # as the stream spells it, e.g. GBP/USD
    base: CurrencyCode  # the pair's base: price = units of quote per 1 base
    quote: CurrencyCode
    max_age_seconds: int = Field(gt=0)


class ExecutionConfig(FirmModel):
    """How the execution desk turns target exposure into orders on this market."""

    participation: Annotated[Decimal, Field(gt=0, le=1)]  # paper fills: share of event volume
    rebalance_threshold_pct: Annotated[Decimal, Field(ge=0, le=100)]  # skip smaller changes
    heartbeat_timeout_seconds: int = Field(gt=0)  # no risk heartbeat this long: stop ordering
    fx: tuple[FxConfig, ...] = Field(min_length=1)  # pairs chained to GBP (e.g. USDT/USD, GBP/USD)


class BrokerConfig(FirmModel):
    paper: Literal["simulator"]
    live: Id | tuple[Id, ...]  # inactive while live_trading is false

    def live_venues(self) -> tuple[str, ...]:
        return (self.live,) if isinstance(self.live, str) else self.live


class MarketProfile(FirmModel):
    market: Id
    calendar: Id  # an exchange-calendar code (e.g. XNYS) or "24x7"
    currency: CurrencyCode
    universe: UniverseConfig
    data: DataConfig
    costs: CostsConfig
    execution: ExecutionConfig
    broker: BrokerConfig
    # Equities
    corporate_actions: tuple[Literal["splits", "dividends"], ...] | None = None
    # Crypto
    funding_rates: bool | None = None

    @model_validator(mode="after")
    def _shape_matches_calendar(self) -> Self:
        if self.calendar == "24x7":
            if self.corporate_actions is not None:
                raise ValueError("a 24x7 (crypto) market has no corporate_actions")
            if self.funding_rates is None:
                raise ValueError("a 24x7 (crypto) market needs funding_rates")
        else:
            if self.corporate_actions is None:
                raise ValueError("an exchange-calendar market needs corporate_actions")
            if self.funding_rates is not None:
                raise ValueError("funding_rates is crypto-only")
        return self


# --------------------------------------------------------------------------- risk


Pct = Annotated[float, Field(gt=0, le=100)]


class GlobalRiskLimits(FirmModel):
    """Firm-wide limits, as % of current equity (drawdown: % below peak equity)."""

    max_total_exposure_pct: Pct
    max_order_notional_pct: Pct
    daily_loss_limit_pct: Pct  # from equity at 00:00 UTC; halts new buys until next day
    max_drawdown_pct: Pct  # trips the global kill switch


class MarketRiskLimits(FirmModel):
    """Per-market limits, as % of current equity."""

    max_position_pct: Pct  # per instrument
    max_market_exposure_pct: Pct
    max_drawdown_pct: Pct  # market P&L below its peak, % of equity; trips the market kill switch
    exposure_cap_per_exchange_pct: Pct | None = None  # crypto: max at one exchange
    paper_allocation_pct: Pct  # share of paper_capital_gbp this market starts with

    @model_validator(mode="after")
    def _nested(self) -> Self:
        if self.max_position_pct > self.max_market_exposure_pct:
            raise ValueError("max_position_pct can't exceed max_market_exposure_pct")
        return self


class RiskProfile(FirmModel):
    """Capital and limits. Read-only to every agent-facing interface (firm rule 3)."""

    risk_profile: Id
    paper_capital_gbp: Annotated[Decimal, Field(gt=0)]
    limits: GlobalRiskLimits
    markets: dict[Id, MarketRiskLimits]

    @model_validator(mode="after")
    def _within_total(self) -> Self:
        for name, m in self.markets.items():
            if m.max_market_exposure_pct > self.limits.max_total_exposure_pct:
                raise ValueError(f"{name}: market exposure above the total exposure limit")
        if sum(m.paper_allocation_pct for m in self.markets.values()) > 100:
            raise ValueError("paper allocations add up to more than 100% of capital")
        return self


# --------------------------------------------------------------------------- fees


class FeeSchedule(FirmModel):
    """Flat percentage fees for one venue tier, with where the numbers come from.

    Volume tiers and per-share schedules come later (milestone 6)."""

    fee_schedule: Id
    venue: Id
    tier: Id
    taker_bps: Bps
    maker_bps: Bps
    source: Annotated[str, Field(min_length=1)]  # URL or document the numbers come from
    as_of: date  # when the numbers were checked
    note: str = ""


# --------------------------------------------------------------------------- verifier


class LookaheadThresholds(FirmModel):
    truncation_points: int = Field(ge=1)
    tolerance: float = Field(ge=0)


class OosThresholds(FirmModel):
    fraction: float = Field(gt=0, lt=1)  # last share of the window held back
    min_sharpe: float
    min_ratio_to_in_sample: float = Field(ge=0)


class SensitivityThresholds(FirmModel):
    relative_steps: tuple[float, ...] = Field(min_length=1)
    min_ratio_to_base: float = Field(ge=0)

    @field_validator("relative_steps")
    @classmethod
    def _no_zero_step(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        if any(step == 0 or step <= -1 for step in v):
            raise ValueError("relative_steps must be non-zero and > -1")
        return v


class CostThresholds(FirmModel):
    min_net_sharpe: float
    stress_multiplier: float = Field(gt=1)
    stress_min_net_sharpe: float


class TrialThresholds(FirmModel):
    method: Literal["deflated_sharpe"]
    min_dsr: float = Field(gt=0, lt=1)


class DataQualityConfig(FirmModel):
    """When a bar is too implausible to judge a strategy on (`config/data/`)."""

    quality: Id
    rule_version: int = Field(ge=1)
    max_price_ratio: float = Field(gt=1)  # vs the last accepted close, up or down

    @property
    def rule_id(self) -> str:
        return f"{self.quality}-v{self.rule_version}-ratio{self.max_price_ratio:g}"


class NotifyConfig(FirmModel):
    """How alerts reach the owner (`config/notify.yaml`). Owner-editable."""

    notify: Id
    throttle_seconds: int = Field(ge=0)  # the same alert at most once per this
    attempts: int = Field(ge=1)  # delivery attempts per notification
    retry_seconds: float = Field(ge=0)
    drain_seconds: float = Field(gt=0)  # at shutdown, time left to send what's queued
    announce_start_stop: bool  # tell the owner when a run starts or stops


class SizingConfig(FirmModel):
    """How much of equity a target of 1 holds in the verifier's backtest. It
    matches the risk profile's per-instrument cap, so a verdict's returns and
    drawdowns are what paper can actually earn (owner, 2026-11-18)."""

    position_pct: float = Field(gt=0, le=100)


class VerifierConfig(FirmModel):
    verifier: Id
    min_bars: int = Field(ge=10)
    sizing: SizingConfig
    lookahead: LookaheadThresholds
    oos: OosThresholds
    sensitivity: SensitivityThresholds
    costs: CostThresholds
    trials: TrialThresholds


# --------------------------------------------------------------------------- models


class ModelRef(FirmModel):
    provider: Literal["ollama"]  # cloud providers only through Certo's ladder (later)
    model: Id


class AskConfig(FirmModel):
    approach: Literal["tools"]
    text_search: Literal["fulltext", "embeddings"]
    embeddings: ModelRef | None = None

    @model_validator(mode="after")
    def _embeddings_need_model(self) -> Self:
        if self.text_search == "embeddings" and self.embeddings is None:
            raise ValueError("text_search: embeddings needs an embeddings model")
        return self


class EgressConfig(FirmModel):
    cloud_spend_budget_gbp: float = Field(ge=0)
    private_to_cloud: Literal["never", "per_run_opt_in"]
    log_every_cloud_call: Literal[True]


class ModelsConfig(FirmModel):
    """Which model serves each role. No model name may appear in code (golden rule 5)."""

    models: Id
    roles: dict[Literal["research", "fast_worker", "judge", "ask"], ModelRef]
    ask: AskConfig
    egress: EgressConfig

    @model_validator(mode="after")
    def _all_roles(self) -> Self:
        missing = {"research", "fast_worker", "judge", "ask"} - set(self.roles)
        if missing:
            raise ValueError(f"missing model roles: {sorted(missing)}")
        return self


# --------------------------------------------------------------------------- loader


class FirmConfig(FirmModel):
    jurisdiction: JurisdictionProfile
    markets: dict[str, MarketProfile]
    risk: RiskProfile

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        allowed = set(self.jurisdiction.allowed_venues)
        for name, m in self.markets.items():
            if isinstance(m.costs.commission, dict):
                missing = set(m.broker.live_venues()) - set(m.costs.commission)
                if missing:
                    raise ValueError(f"market {name}: no fee schedule for {sorted(missing)}")
            bad = set(m.broker.live_venues()) - allowed
            if bad:
                raise ValueError(f"market {name}: venues {sorted(bad)} not allowed by jurisdiction")
            limits = self.risk.markets.get(name)
            if limits is None:
                raise ValueError(f"market {name}: no limits in risk profile")
            if self.risk.limits.max_total_exposure_pct > 100 * self.jurisdiction.leverage_max:
                raise ValueError("total exposure above what the jurisdiction's leverage allows")
            if m.calendar == "24x7" and limits.exposure_cap_per_exchange_pct is None:
                raise ValueError(f"market {name}: crypto needs exposure_cap_per_exchange_pct")
        return self


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing profile: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def _validate[M: FirmModel](model: type[M], path: Path) -> M:
    try:
        return model.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_jurisdiction(path: Path) -> JurisdictionProfile:
    return _validate(JurisdictionProfile, path)


def load_market(path: Path) -> MarketProfile:
    profile = _validate(MarketProfile, path)
    if profile.market != path.stem:
        raise ConfigError(f"{path}: market {profile.market!r} must match the file name")
    return profile


def load_risk(path: Path) -> RiskProfile:
    profile = _validate(RiskProfile, path)
    if profile.risk_profile != path.stem:
        raise ConfigError(f"{path}: risk_profile {profile.risk_profile!r} must match the file name")
    return profile


def load_fee_schedule(root: Path, name: str) -> FeeSchedule:
    path = root / "fees" / f"{name}.yaml"
    fees = _validate(FeeSchedule, path)
    if fees.fee_schedule != name:
        raise ConfigError(f"{path}: fee_schedule {fees.fee_schedule!r} must match the file name")
    return fees


def load_verifier_config(root: Path, name: str = "default") -> VerifierConfig:
    path = root / "verify" / f"{name}.yaml"
    cfg = _validate(VerifierConfig, path)
    if cfg.verifier != name:
        raise ConfigError(f"{path}: verifier {cfg.verifier!r} must match the file name")
    return cfg


def load_data_quality(root: Path, name: str = "default") -> DataQualityConfig:
    path = root / "data" / f"{name}.yaml"
    if name == "default":
        path = root / "data" / "quality.yaml"
    cfg = _validate(DataQualityConfig, path)
    if cfg.quality != name:
        raise ConfigError(f"{path}: quality {cfg.quality!r} must be {name!r}")
    return cfg


def load_notify(root: Path, name: str = "default") -> NotifyConfig:
    path = root / "notify.yaml" if name == "default" else root / f"notify-{name}.yaml"
    cfg = _validate(NotifyConfig, path)
    if cfg.notify != name:
        raise ConfigError(f"{path}: notify {cfg.notify!r} must be {name!r}")
    return cfg


def load_models_config(root: Path) -> ModelsConfig:
    return _validate(ModelsConfig, root / "models.yaml")


def load_config(
    root: Path, jurisdiction: str, markets: list[str], risk: str = "default"
) -> FirmConfig:
    """Load one jurisdiction, the named markets and one risk profile from a `config/` root."""
    j = load_jurisdiction(root / "jurisdictions" / f"{jurisdiction}.yaml")
    ms = {m: load_market(root / "markets" / f"{m}.yaml") for m in markets}
    r = load_risk(root / "risk" / f"{risk}.yaml")
    try:
        return FirmConfig(jurisdiction=j, markets=ms, risk=r)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
