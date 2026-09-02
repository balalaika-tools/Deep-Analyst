"""Structured no-tool contract for the turn-close projection replacement."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from investigation_agent.domain.investigation_state import EvidenceCard, WorkingProjection

type TurnOutcome = Literal["completed", "refused", "failed"]


class ProjectionEvidenceCard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    kind: str
    content_hash: str
    evidentiary_status: str
    rendered_untrusted_content: Annotated[str | None, Field(max_length=8_000)] = None


class ProjectionInput(BaseModel):
    """Everything the compactor may see at turn close; history and raw payloads are absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_turn_id: Annotated[str, Field(min_length=1, max_length=128)]
    utterance: Annotated[str, Field(min_length=1, max_length=64_000)]
    predecessor: WorkingProjection
    evidence_added: Annotated[tuple[ProjectionEvidenceCard, ...], Field(max_length=256)] = ()
    outcome: TurnOutcome
    answer: Annotated[str | None, Field(max_length=128_000)] = None
    failure_code: Annotated[str | None, Field(max_length=64)] = None
    coverage_incomplete: bool = False


def build_projection_input(
    *,
    source_turn_id: str,
    utterance: str,
    predecessor: WorkingProjection,
    evidence_added: tuple[EvidenceCard, ...] = (),
    outcome: TurnOutcome,
    answer: str | None = None,
    failure_code: str | None = None,
    coverage_incomplete: bool = False,
) -> ProjectionInput:
    return ProjectionInput(
        source_turn_id=source_turn_id,
        utterance=utterance,
        predecessor=predecessor,
        evidence_added=tuple(_projection_card(item) for item in evidence_added),
        outcome=outcome,
        answer=answer,
        failure_code=failure_code,
        coverage_incomplete=coverage_incomplete,
    )


def _projection_card(item: EvidenceCard) -> ProjectionEvidenceCard:
    fields = "\n".join(f"{field.name}: {field.value}" for field in item.fields)
    visible = "\n".join(part for part in (item.display or "", fields) if part)
    label = "suspicious-untrusted-evidence" if item.suspicious_content else "untrusted-evidence"
    rendered = f"<{label}>\n{visible}\n</{label}>" if visible else None
    return ProjectionEvidenceCard(
        evidence_id=item.evidence_id,
        kind=item.kind,
        content_hash=item.content_hash,
        evidentiary_status=item.evidentiary_status,
        rendered_untrusted_content=rendered,
    )


__all__ = ["ProjectionEvidenceCard", "ProjectionInput", "TurnOutcome", "build_projection_input"]
