from pathlib import Path

import pytest
import yaml

from firm.core.config import (
    ConfigError,
    load_config,
    load_fee_schedule,
    load_jurisdiction,
    load_market,
    load_models_config,
    load_risk,
    load_verifier_config,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _write(tmp: Path, rel: str, data: object) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data))
    return p


def _raw(rel: str) -> dict:
    return yaml.safe_load((REPO_CONFIG / rel).read_text())


# --- the shipped profiles


def test_repo_profiles_load() -> None:
    cfg = load_config(REPO_CONFIG, "uk", ["crypto", "us_equities"])
    assert cfg.jurisdiction.base_currency == "GBP"
    assert cfg.jurisdiction.live_trading is False
    assert cfg.markets["crypto"].calendar == "24x7"
    assert cfg.markets["us_equities"].calendar == "XNYS"
    assert cfg.markets["crypto"].currency == "USD"
    assert cfg.risk.markets["crypto"].exposure_cap_per_exchange_pct is not None
    assert set(cfg.jurisdiction.venue_status) >= set(cfg.jurisdiction.allowed_venues)


# --- live trading refused (firm rule 7)


def test_live_trading_true_is_refused(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml") | {"live_trading": True}
    with pytest.raises(ConfigError, match="live_trading"):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


def test_live_trading_must_be_present(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml")
    del data["live_trading"]
    with pytest.raises(ConfigError):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


def test_leverage_above_one_is_refused(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml") | {"leverage_max": 2.0}
    with pytest.raises(ConfigError):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


def test_notifications_go_to_owner_only(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml") | {"notifications": {"recipients": ["owner", "bob"]}}
    with pytest.raises(ConfigError):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


# --- unknown keys refused


@pytest.mark.parametrize(
    ("rel", "mutate"),
    [
        ("jurisdictions/uk.yaml", lambda d: d | {"surprise": 1}),
        ("jurisdictions/uk.yaml", lambda d: d | {"tax": d["tax"] | {"surprise": 1}}),
        ("markets/crypto.yaml", lambda d: d | {"surprise": 1}),
        ("markets/crypto.yaml", lambda d: d | {"costs": d["costs"] | {"surprise": 1}}),
        ("markets/us_equities.yaml", lambda d: d | {"broker": d["broker"] | {"x": 1}}),
    ],
)
def test_unknown_keys_are_refused(tmp_path: Path, rel: str, mutate) -> None:
    path = _write(tmp_path, rel, mutate(_raw(rel)))
    loader = load_jurisdiction if rel.startswith("jurisdictions") else load_market
    with pytest.raises(ConfigError):
        loader(path)


# --- market shape


def test_crypto_market_cannot_have_corporate_actions(tmp_path: Path) -> None:
    data = _raw("markets/crypto.yaml") | {"corporate_actions": ["splits"]}
    with pytest.raises(ConfigError):
        load_market(_write(tmp_path, "markets/crypto.yaml", data))


def test_equity_market_cannot_have_funding_rates(tmp_path: Path) -> None:
    data = _raw("markets/us_equities.yaml") | {"funding_rates": True}
    with pytest.raises(ConfigError):
        load_market(_write(tmp_path, "markets/us_equities.yaml", data))


def test_delisted_must_be_included(tmp_path: Path) -> None:
    data = _raw("markets/crypto.yaml")
    data["universe"]["include_delisted"] = False
    with pytest.raises(ConfigError):
        load_market(_write(tmp_path, "markets/crypto.yaml", data))


def test_market_name_must_match_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file name"):
        load_market(_write(tmp_path, "markets/other.yaml", _raw("markets/crypto.yaml")))


def _tree(tmp: Path, *, j=None, crypto=None, risk=None) -> Path:
    _write(tmp, "jurisdictions/uk.yaml", j or _raw("jurisdictions/uk.yaml"))
    _write(tmp, "markets/crypto.yaml", crypto or _raw("markets/crypto.yaml"))
    _write(tmp, "risk/default.yaml", risk or _raw("risk/default.yaml"))
    return tmp


def test_market_venues_must_be_allowed_by_jurisdiction(tmp_path: Path) -> None:
    j = _raw("jurisdictions/uk.yaml") | {"allowed_venues": ["ibkr"]}
    with pytest.raises(ConfigError, match="not allowed"):
        load_config(_tree(tmp_path, j=j), "uk", ["crypto"])


# --- venue regulatory status


def test_allowed_venue_needs_status(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml")
    del data["venue_status"]["kraken"]
    with pytest.raises(ConfigError, match="no venue_status"):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


def test_not_permitted_venue_cannot_be_allowed(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml")
    data["venue_status"]["kraken"]["status"] = "not_permitted"
    with pytest.raises(ConfigError, match="not_permitted"):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


def test_unknown_venue_status_value_refused(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml")
    data["venue_status"]["kraken"]["status"] = "probably_fine"
    with pytest.raises(ConfigError):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


def test_review_by_cannot_precede_as_of(tmp_path: Path) -> None:
    data = _raw("jurisdictions/uk.yaml")
    data["venue_status"]["kraken"]["review_by"] = "2020-01-01"
    with pytest.raises(ConfigError, match="review_by"):
        load_jurisdiction(_write(tmp_path, "jurisdictions/uk.yaml", data))


# --- risk profile (separate from market profiles, firm rule 3)


def test_exposure_cap_not_allowed_in_market_profile(tmp_path: Path) -> None:
    data = _raw("markets/crypto.yaml") | {"exposure_cap_per_exchange_pct": 50}
    with pytest.raises(ConfigError):
        load_market(_write(tmp_path, "markets/crypto.yaml", data))


def test_crypto_market_needs_exposure_cap_in_risk_profile(tmp_path: Path) -> None:
    risk = _raw("risk/default.yaml")
    risk["markets"]["crypto"] = {
        k: v for k, v in risk["markets"]["crypto"].items() if k != "exposure_cap_per_exchange_pct"
    }
    with pytest.raises(ConfigError, match="exposure_cap"):
        load_config(_tree(tmp_path, risk=risk), "uk", ["crypto"])


def test_every_market_needs_risk_limits(tmp_path: Path) -> None:
    risk = _raw("risk/default.yaml") | {"markets": {}}
    with pytest.raises(ConfigError, match="no limits"):
        load_config(_tree(tmp_path, risk=risk), "uk", ["crypto"])


def test_risk_profile_refuses_unknown_keys(tmp_path: Path) -> None:
    risk = _raw("risk/default.yaml") | {"surprise": 1}
    with pytest.raises(ConfigError):
        load_risk(_write(tmp_path, "risk/default.yaml", risk))


def test_risk_profile_name_must_match_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file name"):
        load_risk(_write(tmp_path, "risk/other.yaml", _raw("risk/default.yaml")))


def test_missing_profile(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_jurisdiction(tmp_path / "nope.yaml")


# --- fee schedules and verifier thresholds


def test_repo_fee_schedule_and_verifier_config_load() -> None:
    assert load_fee_schedule(REPO_CONFIG, "kraken_pro_tier1").taker_bps > 0
    cfg = load_verifier_config(REPO_CONFIG)
    assert cfg.trials.method == "deflated_sharpe" and 0 < cfg.oos.fraction < 1


def test_verifier_config_refuses_unknown_keys_and_zero_steps(tmp_path: Path) -> None:
    good = _raw("verify/default.yaml")
    with pytest.raises(ConfigError):
        load_verifier_config(_write(tmp_path, "verify/default.yaml", good | {"skip_oos": True}))
    bad = good | {"sensitivity": good["sensitivity"] | {"relative_steps": [0.0, 0.1]}}
    with pytest.raises(ConfigError):
        load_verifier_config(_write(tmp_path / "b", "verify/default.yaml", bad))


def test_fee_schedule_name_must_match_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file name"):
        load_fee_schedule(
            _write(tmp_path, "fees/other.yaml", _raw("fees/kraken_pro_tier1.yaml")).parent.parent,
            "other",
        )


# --- models (golden rule 5: model names come from config only)


def test_repo_models_config_loads_with_zero_cloud_budget() -> None:
    cfg = load_models_config(REPO_CONFIG)
    assert set(cfg.roles) == {"research", "fast_worker", "judge", "ask"}
    assert cfg.egress.cloud_spend_budget_gbp == 0
    assert cfg.ask.approach == "tools"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d | {"surprise": 1},
        lambda d: d | {"roles": {k: v for k, v in d["roles"].items() if k != "judge"}},
        lambda d: d | {"egress": d["egress"] | {"log_every_cloud_call": False}},
        lambda d: d | {"egress": d["egress"] | {"cloud_spend_budget_gbp": -1}},
        lambda d: d | {"ask": d["ask"] | {"text_search": "embeddings"}},
    ],
)
def test_models_config_refuses_bad_values(tmp_path: Path, mutate) -> None:
    with pytest.raises(ConfigError):
        load_models_config(_write(tmp_path, "models.yaml", mutate(_raw("models.yaml"))).parent)


def test_no_model_names_in_source() -> None:
    names = {r.model for r in load_models_config(REPO_CONFIG).roles.values()}
    src = Path(__file__).resolve().parents[2] / "src"
    for path in src.rglob("*.py"):
        text = path.read_text()
        for name in names:
            assert name not in text, f"{path}: hard-coded model name {name!r}"


# --- per-venue fee schedules (real published fees)


def test_crypto_market_maps_each_venue_to_its_fee_schedule() -> None:
    from firm.core.config import load_fee_schedule

    m = load_config(REPO_CONFIG, "uk", ["crypto"]).markets["crypto"]
    kraken = load_fee_schedule(REPO_CONFIG, m.costs.fee_schedule_for("kraken"))
    coinbase = load_fee_schedule(REPO_CONFIG, m.costs.fee_schedule_for("coinbase"))
    assert (kraken.maker_bps, kraken.taker_bps) == (40, 80)
    assert (coinbase.maker_bps, coinbase.taker_bps) == (40, 60)
    assert kraken.source and kraken.as_of and coinbase.source and coinbase.as_of


def test_fee_schedule_needs_source_and_date(tmp_path: Path) -> None:
    from firm.core.config import load_fee_schedule

    data = _raw("fees/kraken_pro_tier1.yaml")
    del data["source"]
    _write(tmp_path, "fees/kraken_pro_tier1.yaml", data)
    with pytest.raises(ConfigError):
        load_fee_schedule(tmp_path, "kraken_pro_tier1")


def test_unknown_venue_has_no_fee_schedule() -> None:
    m = load_config(REPO_CONFIG, "uk", ["crypto"]).markets["crypto"]
    with pytest.raises(ValueError, match="no fee schedule"):
        m.costs.fee_schedule_for("bitstamp")  # not configured


def test_every_live_venue_needs_a_fee_schedule(tmp_path: Path) -> None:
    crypto = _raw("markets/crypto.yaml")
    crypto["costs"]["commission"] = {"kraken": "kraken_pro_tier1"}  # coinbase missing
    with pytest.raises(ConfigError, match="fee schedule"):
        load_config(_tree(tmp_path, crypto=crypto), "uk", ["crypto"])


# --- data sources in profiles vs the provider registry


def test_profile_history_sources_are_registered_providers() -> None:
    """Every history source a profile names is a registered provider, except the
    ones explicitly planned (no provider module yet)."""
    from firm.data import providers

    planned = {"eodhd"}
    cfg = load_config(REPO_CONFIG, "uk", ["crypto", "us_equities"])
    for name, market in cfg.markets.items():
        hist = market.data.history
        sources = {hist} if isinstance(hist, str) else set(hist)
        unknown = sources - set(providers.available()) - planned
        assert not unknown, f"{name}: unknown history sources {sorted(unknown)}"


def test_binance_is_lab_only_in_the_uk_profile() -> None:
    j = load_jurisdiction(REPO_CONFIG / "jurisdictions" / "uk.yaml")
    assert "binance" not in j.allowed_venues
    assert j.venue_status["binance"].status == "not_permitted"


# --- risk profile values (paper capital and limits are configuration)


def test_risk_profile_carries_capital_and_limits() -> None:
    r = load_risk(REPO_CONFIG / "risk" / "default.yaml")
    assert r.paper_capital_gbp > 0
    assert r.limits.max_total_exposure_pct == 100
    assert r.markets["crypto"].exposure_cap_per_exchange_pct is not None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d | {"paper_capital_gbp": 0},
        lambda d: d | {"limits": d["limits"] | {"daily_loss_limit_pct": 0}},
        lambda d: d | {"limits": d["limits"] | {"max_drawdown_pct": 120}},
        lambda d: (
            d
            | {
                "markets": d["markets"]
                | {"crypto": d["markets"]["crypto"] | {"max_position_pct": 70}}
            }
        ),  # position above market exposure
        lambda d: d | {"limits": d["limits"] | {"max_total_exposure_pct": 50}},  # below markets
    ],
)
def test_risk_profile_refuses_bad_limits(tmp_path: Path, mutate) -> None:
    with pytest.raises(ConfigError):
        load_risk(_write(tmp_path, "risk/default.yaml", mutate(_raw("risk/default.yaml"))))
