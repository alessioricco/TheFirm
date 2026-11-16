"""Provider registry: names -> provider instances.

Built-in providers register themselves when `firm.data.providers` is
imported. Names are what captures, configs and the CLI use (`provider=`).
"""

from __future__ import annotations

from firm.data.providers.base import Provider

_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    if provider.name in _REGISTRY:
        raise ValueError(f"provider {provider.name!r} is already registered")
    _REGISTRY[provider.name] = provider
    return provider


def get(name: str) -> Provider:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown provider {name!r}; available: {available()}") from None


def available() -> list[str]:
    return sorted(_REGISTRY)
