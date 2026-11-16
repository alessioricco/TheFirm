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
    version = capsys.readouterr().out.strip().splitlines()[-1].split()[0]
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
