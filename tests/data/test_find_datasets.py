"""Which dataset versions hold bars for an instrument and timeframe (researchers pick one)."""

from firm.data.dataset import find_datasets


def test_finds_the_synthetic_series(dataset) -> None:
    root, version = dataset
    found = find_datasets(root, "BTC-USD", "1d")
    assert (version, "kraken", 2000) in found
    assert find_datasets(root, "BTC-USD", "1h") == []
    assert find_datasets(root, "ETH-USD", "1d") == []
