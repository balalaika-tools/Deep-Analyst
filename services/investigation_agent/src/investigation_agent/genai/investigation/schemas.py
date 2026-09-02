"""Private finalization contracts and the framework state schema of the main agent."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, NotRequired

from langchain.agents.middleware import AgentState
from pydantic import BaseModel, ConfigDict, Field, model_validator

from investigation_agent.domain.history import Citation
from investigation_agent.domain.investigation_state import EvidenceIndex, UsageCounters


class StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClaimKind(StrEnum):
    VERIFIED = "verified"
    PROPOSED = "proposed"
    HYPOTHESIS = "hypothesis"
    LIMITATION = "limitation"


class AnswerClaim(StrictModel):
    claim_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")]
    text: Annotated[str, Field(min_length=1, max_length=4_000)]
    kind: ClaimKind
    material: bool = True
    evidence_ids: Annotated[tuple[str, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def _material_claim_has_support(self) -> AnswerClaim:
        if self.material and self.kind is not ClaimKind.LIMITATION and not self.evidence_ids:
            raise ValueError("a material factual claim requires evidence IDs")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence IDs must be unique")
        return self


class AnswerDraft(StrictModel):
    """The main agent's private final answer; released only after grounding verification."""

    answer: Annotated[str, Field(min_length=1, max_length=128_000)]
    claims: Annotated[tuple[AnswerClaim, ...], Field(max_length=128)] = ()

    @model_validator(mode="after")
    def _claim_ids_are_unique(self) -> AnswerDraft:
        identifiers = [claim.claim_id for claim in self.claims]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("claim IDs must be unique")
        return self


class EntailmentVerdict(StrictModel):
    claim_id: Annotated[str, Field(min_length=1, max_length=64)]
    supported: bool
    safe_reason_code: Literal[
        "entailed",
        "not_entailed",
        "contradicted",
        "insufficient_context",
    ]


class GroundingVerdict(StrictModel):
    claims: Annotated[tuple[EntailmentVerdict, ...], Field(max_length=128)]


class VerifiedAnswer(StrictModel):
    answer: Annotated[str, Field(min_length=1, max_length=128_000)]
    citations: Annotated[tuple[Citation, ...], Field(max_length=256)]


class InvestigationAgentState(AgentState[AnswerDraft]):
    """Framework state plus the application sections stored as plain JSON.

    ``evidence`` and ``usage`` carry reducers so several tool calls in one step can each report
    their outcome; every other section has exactly one writer per step.
    """

    control: NotRequired[dict[str, Any]]
    turn: NotRequired[dict[str, Any] | None]
    evidence: NotRequired[Annotated[dict[str, Any], merge_evidence_indexes]]
    projection: NotRequired[dict[str, Any]]
    history: NotRequired[dict[str, Any]]
    usage: NotRequired[Annotated[dict[str, Any], add_usage]]


def merge_evidence_indexes(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """Union two index snapshots; existing cards win and new cards are re-sequenced."""

    if not left:
        return dict(right or {})
    if not right:
        return dict(left)
    base = EvidenceIndex.model_validate(left)
    incoming = EvidenceIndex.model_validate(right)
    cards = dict(base.cards)
    next_sequence = max(base.next_sequence, incoming.next_sequence)
    for card in sorted(incoming.cards.values(), key=lambda item: (item.sequence, item.evidence_id)):
        if card.evidence_id in cards:
            continue
        cards[card.evidence_id] = card.model_copy(update={"sequence": next_sequence})
        next_sequence += 1
    merged = EvidenceIndex(
        cards=dict(sorted(cards.items())),
        next_sequence=next_sequence,
        dropped_cards=max(base.dropped_cards, incoming.dropped_cards),
        coverage_notice=incoming.coverage_notice or base.coverage_notice,
    )
    return merged.model_dump(mode="json")


def add_usage(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Usage updates are deltas; the channel keeps cumulative thread totals."""

    base = UsageCounters.model_validate(left or {})
    delta = UsageCounters.model_validate(right or {})
    values = base.model_dump()
    for name, amount in delta.model_dump().items():
        values[name] += amount
    return UsageCounters.model_validate(values).model_dump(mode="json")


__all__ = [
    "AnswerClaim",
    "AnswerDraft",
    "ClaimKind",
    "EntailmentVerdict",
    "GroundingVerdict",
    "InvestigationAgentState",
    "VerifiedAnswer",
    "add_usage",
    "merge_evidence_indexes",
]
