import pytest

from firm import __version__
from firm.control.cli import main


def test_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"firm {__version__}"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_verify_end_to_end_exit_codes(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    store, state = tmp_path / "data", tmp_path / "state"
    assert main(["data", "synthetic", "--root", str(store)]) == 0
    assert (
        main(
            [
                "data",
                "build-dataset",
                "--root",
                str(store),
                "--provider",
                "kraken",
                "--symbol",
                "XBTUSD",
                "--tf",
                "1d",
            ]
        )
        == 0
    )
    version = next(w for w in capsys.readouterr().out.split() if w.startswith("ds-"))
    common = [
        "--dataset",
        version,
        "--venue",
        "kraken",
        "--instrument",
        "BTC-USD",
        "--tf",
        "1d",
        "--market",
        "crypto",
        "--config",
        str(repo / "config"),
        "--root",
        str(store),
        "--state-root",
        str(state),
    ]
    ex = repo / "examples" / "strategies"
    assert (
        main(["verify", "--strategy", f"{ex}/ma_crossover.py:MovingAverageCrossover", *common]) == 0
    )
    assert main(["verify", "--strategy", f"{ex}/lookahead_ma.py:LookaheadCrossover", *common]) == 1
    assert main(["verify", "--strategy", f"{ex}/ma_crossover.py", *common]) == 3
    assert len(list((state / "reports").glob("v-*.json"))) == 2


def test_synthetic_refuses_a_store_with_real_captures(tmp_path) -> None:
    from datetime import UTC, datetime

    from firm.data.store import write_capture

    write_capture(
        tmp_path,
        b"{}",
        provider="kraken",
        kind="bars",
        provider_symbol="XBTUSD",
        tf="1d",
        fmt="json",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        request={},
        via="test",
        licence="test",
    )
    assert main(["data", "synthetic", "--root", str(tmp_path)]) == 3


def test_build_dataset_across_venues_and_kinds(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    from firm.data.dataset import open_dataset
    from firm.data.synthetic import seed_store, seed_trades

    seed_store(tmp_path, venue="kraken", provider_symbol="XBTUSD", n=200)
    seed_store(tmp_path, venue="coinbase", provider_symbol="BTC-USD", n=150, seed=3)
    seed_trades(tmp_path, venue="kraken", provider_symbol="XBTUSD", n=400)
    assert (
        main(
            [
                "data",
                "build-dataset",
                "--root",
                str(tmp_path),
                "--bars",
                "kraken:XBTUSD:1d",
                "--bars",
                "coinbase:BTC-USD:1d",
                "--trades",
                "kraken:XBTUSD",
            ]
        )
        == 0
    )
    version = next(w for w in capsys.readouterr().out.split() if w.startswith("ds-"))
    ds = open_dataset(tmp_path, version)
    assert {(f.kind, f.venue) for f in ds.manifest.files} == {
        ("bars", "kraken"),
        ("bars", "coinbase"),
        ("trades", "kraken"),
    }


@pytest.mark.parametrize(
    "bad",
    [["--bars", "kraken:XBTUSD"], ["--trades", "kraken"], ["--provider", "kraken"], []],
)
def test_build_dataset_rejects_bad_specs(tmp_path, bad: list[str]) -> None:
    assert main(["data", "build-dataset", "--root", str(tmp_path), *bad]) == 3


def test_paper_run_refusal_has_its_own_exit_code(tmp_path, capsys) -> None:
    """A refused deployment exits 4, not the generic error 3: the systemd unit
    restarts on errors (a network blip at boot) but never on a refusal."""
    from firm.control.cli import EXIT_REFUSED

    rc = main(
        [
            "paper", "run",
            "--strategy", "examples/strategies/ma_crossover.py:MovingAverageCrossover",
            "--verdict", "v-no-such-report",
            "--market", "crypto", "--venue", "coinbase",
            "--instrument", "BTC/USD", "--tf", "1d",
            "--root", str(tmp_path / "data"),
            "--state-root", str(tmp_path / "state"),
        ]
    )  # fmt: skip
    assert rc == EXIT_REFUSED == 4
    assert "refused" in capsys.readouterr().err
