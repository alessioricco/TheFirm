"""Shared base types for every typed object in the firm.

All domain objects, bus payloads and config profiles are frozen pydantic
models that refuse unknown fields: an object that crosses a boundary is
exactly what its schema says, and nothing can mutate it afterwards.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class FirmModel(BaseModel):
    """Frozen, strict base model. Unknown fields are an error, not ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


Id = Annotated[str, StringConstraints(min_length=1, max_length=128, strip_whitespace=True)]
"""A non-empty identifier. Empty strings are refused so a missing ID can't hide as ''."""

Reason = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
"""A non-empty human-readable reason. Every decision in the firm carries one."""

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""Lower-case hex SHA-256 digest."""

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3,5}$")]
"""ISO-4217 code or a crypto ticker (e.g. GBP, USD, BTC, USDT)."""

Probability = Annotated[float, Field(ge=0.0, le=1.0)]
