from pathlib import Path

import pytest
import yaml

from firm.core.config import (
    ConfigError,
    load_config,
    load_fee_schedule,
    load_jurisdiction,
    load_market,
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
    risk = {"risk_profile": "default", "markets": {"crypto": {}}}
    with pytest.raises(ConfigError, match="exposure_cap"):
        load_config(_tree(tmp_path, risk=risk), "uk", ["crypto"])


def test_every_market_needs_risk_limits(tmp_path: Path) -> None:
    risk = {"risk_profile": "default", "markets": {}}
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
    assert load_fee_schedule(REPO_CONFIG, "exchange_taker").taker_bps > 0
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
            _write(tmp_path, "fees/other.yaml", _raw("fees/exchange_taker.yaml")).parent.parent,
            "other",
        )
