"""The bus message envelope.

Every message across a desk boundary is an `Envelope`: an ID (for
idempotent replay), its subject, the payload schema version, a timestamp,
the sending service, and a payload validated against the subject's schema.
An envelope whose sender may not publish its subject cannot be built
(pydantic raises `ValidationError` with "permission denied").
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import AwareDatetime, Field, model_validator

from firm.core.base import FirmModel, Id
from firm.core.messages.subjects import PAYLOADS, SCHEMA_VERSIONS, Service, Subject, may_publish


class Envelope(FirmModel):
    message_id: Id
    subject: Subject
    schema_version: int = Field(ge=1)
    timestamp: AwareDatetime
    sender: Service
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not may_publish(self.sender, self.subject):
            raise ValueError(f"permission denied: {self.sender} may not publish {self.subject}")
        if self.schema_version != SCHEMA_VERSIONS[self.subject]:
            raise ValueError(
                f"{self.subject} schema_version {self.schema_version} != "
                f"{SCHEMA_VERSIONS[self.subject]}"
            )
        PAYLOADS[self.subject].model_validate(self.payload)
        return self

    def typed_payload(self) -> FirmModel:
        return PAYLOADS[self.subject].model_validate(self.payload)


def wrap(
    *,
    message_id: str,
    sender: Service,
    subject: Subject,
    payload: FirmModel,
    timestamp: AwareDatetime,
) -> Envelope:
    """Build an envelope from a typed payload, refusing the wrong payload type."""
    expected = PAYLOADS[subject]
    if type(payload) is not expected:
        raise TypeError(f"{subject} carries {expected.__name__}, got {type(payload).__name__}")
    return Envelope(
        message_id=message_id,
        subject=subject,
        schema_version=SCHEMA_VERSIONS[subject],
        timestamp=timestamp,
        sender=sender,
        payload=payload.model_dump(mode="json"),
    )
