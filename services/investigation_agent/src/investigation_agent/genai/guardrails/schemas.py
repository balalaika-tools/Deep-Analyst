"""Structured contracts returned by no-tool input and evidence guard models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt


class InputGuardrailStatus(StrEnum):
    ALLOWED = "allowed"
    PROMPT_INJECTION = "prompt_injection"
    OFF_TOPIC = "off_topic"
    INDETERMINATE = "indeterminate"


class InputGuardrailVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    status: InputGuardrailStatus
    reason_code: Annotated[str, Field(min_length=1, max_length=64)]


class EvidenceGuardItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: Annotated[str, Field(min_length=1, max_length=256)]
    suspicious: bool
    reason_code: Annotated[str, Field(min_length=1, max_length=64)]


class EvidenceGuardrailVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    items: Annotated[tuple[EvidenceGuardItem, ...], Field(max_length=128)]


class NormalizedEvidence(BaseModel):
    """Model-facing wrapper; exact application evidence remains unchanged elsewhere."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    content_hash: str
    rendered: Annotated[str, Field(max_length=32_768)]
    suspicious: bool
    guard_status: str


class GuardedEvidenceBatch(BaseModel):
    """One guarded batch plus the physical model work used to classify it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[NormalizedEvidence, ...]
    model_calls: NonNegativeInt
    physical_attempts: NonNegativeInt


__all__ = [
    "EvidenceGuardItem",
    "EvidenceGuardrailVerdict",
    "GuardedEvidenceBatch",
    "InputGuardrailStatus",
    "InputGuardrailVerdict",
    "NormalizedEvidence",
]
