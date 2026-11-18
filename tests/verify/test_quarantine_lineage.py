"""A candidate whose only difference from the opened one is quarantined bars.

The hidden period was opened (before the data-quality check existed) on a
dataset holding a corrupt bar. The same code and parameters on the cleaned
dataset, over the same window, see exactly the opened bars minus the bars the
quality rule quarantines: nothing new about the hidden period can be learnt,
so the vault links the two (an `alias` with its reason) and replays. Any other
difference (another changed bar, a longer window) is still refused (firm rule 4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from firm.core.config import load_data_quality
from firm.core.domain import CheckStatus, TrialRecord
from firm.data.dataset import build_dataset, open_dataset
from firm.data.normalize import normalize_bars
from firm.data.store import write_capture
from firm.data.synthetic import kraken_ohlc_payload, seed_store, synthetic_bars
from firm.verify.runner import FAR_FUTURE, _h, verify_strategy_file
from firm.verify.state import OosVault, TrialRegistry, oos_scope
from firm.verify.strategy import load_strategy
from firm.verify.verifier import bars_fingerprint, candidate_key
from verify_helpers import EXAMPLES, NOW, REPO

START = datetime(2019, 1, 1, tzinfo=UTC)
CORRUPT = 100  # in-sample, far from the hidden period


def _store(tmp_path: Path, *, also_change: int | None = None) -> tuple[Path, str, str]:
    """A store with one corrupt bar: (root, dirty dataset, clean dataset).
    `also_change` alters one more (plausible) bar in a second capture."""
    root = tmp_path / "store"
    seed_store(root, n=600)
    bars = synthetic_bars(601, seed=7, start=START, tf="1d")
    b = bars[CORRUPT]
    bars[CORRUPT] = type(b)(
        open_time=b.open_time,
        open=b.open * 0.0001,
        high=b.high,
        low=b.open * 0.0001,
        close=b.close,
        volume=b.volume,
    )
    write_capture(
        root,
        kraken_ohlc_payload(bars),
        provider="kraken",
        kind="bars",
        provider_symbol="XBTUSD",
        tf="1d",
        fmt="json",
        captured_at=datetime(2021, 1, 1, tzinfo=UTC),
        request={"t": "corrupt"},
        via="test",
        licence="synthetic",
        capture_method="synthetic",
    )
    dirty = build_dataset(
        root, normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    ).dataset_version
    if also_change is not None:
        c = bars[also_change]
        bars[also_change] = type(c)(
            open_time=c.open_time,
            open=c.open,
            high=c.high * 1.01,
            low=c.low,
            close=c.close,
            volume=c.volume,
        )
        write_capture(
            root,
            kraken_ohlc_payload(bars),
            provider="kraken",
            kind="bars",
            provider_symbol="XBTUSD",
            tf="1d",
            fmt="json",
            captured_at=datetime(2021, 1, 2, tzinfo=UTC),
            request={"t": "revised"},
            via="test",
            licence="synthetic",
            capture_method="synthetic",
        )
    clean = build_dataset(
        root,
        normalize_bars(
            root,
            provider="kraken",
            provider_symbol="XBTUSD",
            tf="1d",
            quality=load_data_quality(REPO / "config"),
        ),
    ).dataset_version
    return root, dirty, clean


def _open_on_dirty(root: Path, dirty: str, state: Path, *, as_of: datetime = FAR_FUTURE) -> None:
    """What the verifier did before the data-quality check: open the hidden period
    on the dirty bars and seal it (under an older verifier config)."""
    loaded = load_strategy(EXAMPLES / "ma_crossover.py", "MovingAverageCrossover")
    params = dict(loaded.strategy.default_params)
    bars = open_dataset(root, dirty).bars("kraken", "BTC-USD", "1d", as_of=as_of)
    key = candidate_key(loaded.code_sha256, params, bars_fingerprint(bars))
    scope = oos_scope(loaded.strategy.name, ("BTC-USD",), "1d")
    window = [bars["open_time"].iloc[0].isoformat(), bars["close_time"].iloc[-1].isoformat()]
    trial = (
        "t-" + _h([loaded.strategy.name, loaded.code_sha256, params, bars_fingerprint(bars)])[:16]
    )
    TrialRegistry(state).record(
        TrialRecord(
            trial_id=trial,
            experiment_id="manual",
            strategy_id=loaded.strategy.name,
            parameters=params,
            metrics={},
            dataset_version=dirty,
            recorded_at=NOW,
        )
    )
    vault = OosVault(state)
    opening = vault.request(
        strategy_id=loaded.strategy.name,
        scope=scope,
        candidate_key=key,
        dataset_version=dirty,
        window=window,
        at=NOW,
        config_key="older",
    )
    assert opening.access == "first_opening"
    vault.seal(
        strategy_id=loaded.strategy.name,
        scope=scope,
        candidate_key=key,
        result_sha256="0" * 64,
        at=NOW,
        config_key="older",
    )


def _verify(root: Path, dataset: str, state: Path, *, as_of: datetime | None = None):
    return verify_strategy_file(
        strategy_path=EXAMPLES / "ma_crossover.py",
        class_name="MovingAverageCrossover",
        params=None,
        data_root=root,
        dataset_version=dataset,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        config_root=REPO / "config",
        jurisdiction="uk",
        market="crypto",
        state=state,
        now=NOW,
        as_of=as_of,
    )


def _oos(result):
    return next(c for c in result.verdict.checks if c.name == "hidden_out_of_sample")


def test_the_cleaned_dataset_replays_the_opened_candidate(tmp_path: Path) -> None:
    root, dirty, clean = _store(tmp_path)
    state = tmp_path / "state"
    _open_on_dirty(root, dirty, state)

    trials = TrialRegistry(state).count()
    r = _verify(root, clean, state)
    assert TrialRegistry(state).count() == trials  # the same experiment: not a new trial
    assert r.verdict.checks[0].status is CheckStatus.PASSED  # no implausible bar left
    oos = _oos(r)
    assert "already opened" not in oos.detail
    assert oos.status in (CheckStatus.PASSED, CheckStatus.FAILED)  # judged, not refused
    events = OosVault(state).events()
    alias = [e for e in events if e["event"] == "alias"]
    assert len(alias) == 1 and "quarantined" in alias[0]["reason"]
    assert [e["event"] for e in events][-2:] == ["replay", "sealed"]  # new config: sealed too

    again = _verify(root, clean, state)  # now a plain replay of the linked candidate
    assert _oos(again).status is oos.status
    assert len([e for e in OosVault(state).events() if e["event"] == "alias"]) == 1


def test_another_changed_bar_is_still_refused(tmp_path: Path) -> None:
    root, dirty, clean = _store(tmp_path, also_change=200)
    state = tmp_path / "state"
    _open_on_dirty(root, dirty, state)

    trials = TrialRegistry(state).count()
    oos = _oos(_verify(root, clean, state))
    assert oos.status is CheckStatus.FAILED and "already opened" in oos.detail
    assert not [e for e in OosVault(state).events() if e["event"] == "alias"]
    assert TrialRegistry(state).count() == trials + 1  # a different candidate: a new trial


def test_a_longer_window_needs_as_of_to_match_the_opening(tmp_path: Path) -> None:
    root, dirty, clean = _store(tmp_path)
    state = tmp_path / "state"
    end = (
        open_dataset(root, dirty)
        .bars("kraken", "BTC-USD", "1d", as_of=FAR_FUTURE)["close_time"]
        .iloc[-10]
    )
    _open_on_dirty(root, dirty, state, as_of=end)

    longer = _oos(_verify(root, clean, state))  # 9 bars the opening never saw
    assert longer.status is CheckStatus.FAILED and "already opened" in longer.detail

    same = _oos(_verify(root, clean, state, as_of=end))
    assert "already opened" not in same.detail


def test_an_opening_whose_dataset_is_gone_is_simply_refused(tmp_path: Path) -> None:
    root, dirty, clean = _store(tmp_path)
    state = tmp_path / "state"
    _open_on_dirty(root, dirty, state)
    (root / "datasets" / f"{dirty}.json").unlink()  # can't prove what the opening saw

    oos = _oos(_verify(root, clean, state))
    assert oos.status is CheckStatus.FAILED and "already opened" in oos.detail


def test_alias_chains_resolve_to_their_end_and_count_once(tmp_path: Path) -> None:
    reg = TrialRegistry(tmp_path)
    for t in ("t-legacy", "t-content"):
        reg.record(
            TrialRecord(
                trial_id=t,
                experiment_id="manual",
                strategy_id="s",
                parameters={},
                metrics={},
                dataset_version="ds-1",
                recorded_at=NOW,
            )
        )
    reg.alias("t-legacy", "t-content")
    assert reg.count() == 1
    reg.alias("t-legacy", "t-clean")  # extended from the chain's end: t-content -> t-clean
    reg.alias("t-content", "t-clean")  # idempotent
    assert reg._aliases() == {"t-legacy": "t-content", "t-content": "t-clean"}
    assert reg.ids() == {"t-clean"}


def test_an_opening_already_linked_once_is_linked_again_from_its_chain_end(
    tmp_path: Path,
) -> None:
    root, dirty, clean = _store(tmp_path)
    state = tmp_path / "state"
    _open_on_dirty(root, dirty, state)
    vault = OosVault(state)
    opened = next(e for e in vault.events() if e["event"] == "first_opening")["candidate_key"]
    # as the real vault: the opening's old-style key was linked to its content key
    vault._log(
        {
            "event": "alias",
            "strategy_id": "ma_crossover",
            "scope": "x",
            "from": "legacy",
            "to": opened,
            "at": NOW.isoformat(),
        }
    )

    oos = _oos(_verify(root, clean, state))
    assert "already opened" not in oos.detail
    froms = [e["from"] for e in OosVault(state).events() if e["event"] == "alias"]
    assert froms == ["legacy", opened]  # each key linked once
