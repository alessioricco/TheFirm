"""Helpers for verifier tests (importable; fixtures live in tests/verify/conftest.py)."""

from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "strategies"
NOW = datetime(2026, 11, 16, tzinfo=UTC)


def run_verify(dataset: tuple[Path, str], state: Path, file: str, cls: str, params=None):
    from firm.verify.runner import verify_strategy_file

    root, version = dataset
    return verify_strategy_file(
        strategy_path=EXAMPLES / file,
        class_name=cls,
        params=params,
        data_root=root,
        dataset_version=version,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        config_root=REPO / "config",
        jurisdiction="uk",
        market="crypto",
        state=state,
        now=NOW,
    )
