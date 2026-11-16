"""Shared fixture: a synthetic store + dataset version (session-scoped)."""

from pathlib import Path

import pytest

from firm.data.dataset import build_dataset
from firm.data.normalize import normalize_bars
from firm.data.synthetic import seed_store


@pytest.fixture(scope="session")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("store")
    seed_store(root, n=2000, seed=7)
    files = normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    return root, build_dataset(root, files).dataset_version
