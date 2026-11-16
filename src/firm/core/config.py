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
- `config/verify/<name>.yaml`: verifier thresholds. No agent may write
  them: a lower bar would let an agent pass its own strategy.
"""

from __future__ import annotations

from datetime import date
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


class CostsConfig(FirmModel):
    commission: Id  # name of a fee schedule in config/fees/
    spread_model: Literal["quoted"]
    spread_bps: Bps  # full spread assumed when no quote data (bar backtests)
    slippage_bps: Bps
    fx_conversion_bps: Bps


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


class MarketRiskLimits(FirmModel):
    """Per-market limits. Position, exposure and drawdown limits land in milestone 2."""

    exposure_cap_per_exchange_pct: float | None = Field(default=None, gt=0, le=100)


class RiskProfile(FirmModel):
    """Read-only to every agent-facing interface (firm rule 3)."""

    risk_profile: Id
    markets: dict[Id, MarketRiskLimits]


# --------------------------------------------------------------------------- fees


class FeeSchedule(FirmModel):
    """Flat percentage fees. Tiered and per-share schedules come with milestone 6."""

    fee_schedule: Id
    taker_bps: Bps
    maker_bps: Bps


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


class VerifierConfig(FirmModel):
    verifier: Id
    min_bars: int = Field(ge=10)
    lookahead: LookaheadThresholds
    oos: OosThresholds
    sensitivity: SensitivityThresholds
    costs: CostThresholds
    trials: TrialThresholds


# --------------------------------------------------------------------------- loader


class FirmConfig(FirmModel):
    jurisdiction: JurisdictionProfile
    markets: dict[str, MarketProfile]
    risk: RiskProfile

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        allowed = set(self.jurisdiction.allowed_venues)
        for name, m in self.markets.items():
            bad = set(m.broker.live_venues()) - allowed
            if bad:
                raise ValueError(f"market {name}: venues {sorted(bad)} not allowed by jurisdiction")
            limits = self.risk.markets.get(name)
            if limits is None:
                raise ValueError(f"market {name}: no limits in risk profile")
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
