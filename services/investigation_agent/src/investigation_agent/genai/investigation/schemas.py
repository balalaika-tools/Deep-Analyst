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
    """Merge branch snapshots while preserving the evidence index's declared capacity."""

    if not left:
        return dict(right or {})
    if not right:
        return dict(left)
    base = EvidenceIndex.model_validate(left)
    incoming = EvidenceIndex.model_validate(right)
    cards, next_sequence = _merge_cards(base, incoming)

    capacity = _merged_capacity(base, incoming)
    if capacity is not None and len(cards) > capacity:
        latest_turn = _latest_turn_id(base, incoming)
        eviction_order = sorted(
            cards.values(),
            key=lambda card: (
                card.first_seen_turn_id == latest_turn,
                card.sequence,
                card.evidence_id,
            ),
        )
        for card in eviction_order[: len(cards) - capacity]:
            del cards[card.evidence_id]

    total_seen = max(
        next_sequence - 1,
        len(base.cards) + base.dropped_cards,
        len(incoming.cards) + incoming.dropped_cards,
    )
    dropped_cards = max(base.dropped_cards, incoming.dropped_cards, total_seen - len(cards))
    merged = EvidenceIndex(
        cards=dict(sorted(cards.items())),
        next_sequence=next_sequence,
        dropped_cards=dropped_cards,
        coverage_notice=(
            incoming.coverage_notice
            or base.coverage_notice
            or ("evidence_index_bounded" if dropped_cards else None)
        ),
        max_cards=capacity,
    )
    return merged.model_dump(mode="json")


def _latest_turn_id(*indexes: EvidenceIndex) -> str | None:
    """The turn that added the newest card; only its cards are protected from eviction."""

    newest = max(
        (card for index in indexes for card in index.cards.values()),
        key=lambda card: (card.sequence, card.evidence_id),
        default=None,
    )
    return newest.first_seen_turn_id if newest is not None else None


def _merge_cards(*indexes: EvidenceIndex) -> tuple[dict[str, Any], int]:
    """Union cards and deterministically resolve sequence collisions from parallel branches."""

    by_id: dict[str, Any] = {}
    for card in (card for index in indexes for card in index.cards.values()):
        existing = by_id.get(card.evidence_id)
        if existing is None or card.sequence < existing.sequence:
            by_id[card.evidence_id] = card

    cards: dict[str, Any] = {}
    used_sequences: set[int] = set()
    for card in sorted(by_id.values(), key=lambda item: (item.sequence, item.evidence_id)):
        sequence = card.sequence
        while sequence in used_sequences:
            sequence += 1
        if sequence != card.sequence:
            card = card.model_copy(update={"sequence": sequence})
        cards[card.evidence_id] = card
        used_sequences.add(sequence)
    next_sequence = max(
        *(index.next_sequence for index in indexes),
        max(used_sequences, default=0) + 1,
    )
    return cards, next_sequence


def _merged_capacity(*indexes: EvidenceIndex) -> int | None:
    capacities = [index.max_cards for index in indexes if index.max_cards is not None]
    return min(capacities) if capacities else None


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
