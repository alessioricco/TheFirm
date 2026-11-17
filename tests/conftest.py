"""Suite-wide rules. Tests never touch a network, an exchange or a model:
any socket connection attempted during a test fails it."""

import socket

import pytest


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkBlocked("tests must not use the network (CLAUDE.md)")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# --------------------------------------------------------------------------- shared data


@pytest.fixture(scope="session")
def dataset(tmp_path_factory: pytest.TempPathFactory):
    """A synthetic store + dataset version (Kraken BTC-USD 1d, 2,000 bars), shared."""
    from firm.data.dataset import build_dataset
    from firm.data.normalize import normalize_bars
    from firm.data.synthetic import seed_store

    root = tmp_path_factory.mktemp("store")
    seed_store(root, n=2000, seed=7)
    files = normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    return root, build_dataset(root, files).dataset_version
