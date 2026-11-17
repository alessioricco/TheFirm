"""Stream provider registry (names like `kraken_ws`, as in market profiles)."""

from __future__ import annotations

from firm.data.streams.base import StreamProvider

_REGISTRY: dict[str, StreamProvider] = {}


def register(provider: StreamProvider) -> StreamProvider:
    if provider.name in _REGISTRY:
        raise ValueError(f"stream provider {provider.name!r} is already registered")
    _REGISTRY[provider.name] = provider
    return provider


def get(name: str) -> StreamProvider:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown stream provider {name!r}; available: {available()}") from None


def available() -> list[str]:
    return sorted(_REGISTRY)
