"""Durable checkpoint sections for an investigation thread.

Every section is a frozen Pydantic model that hooks read from and write to the LangGraph state
as plain JSON so the checkpoint serializer never has to reconstruct application classes. The
framework ``messages`` list is not modelled here; it holds only the current turn's working
conversation and is cleared by the turn-close hook.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from evidence_model import SourceRef
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, model_validator

from investigation_agent.domain.history import Citation, HistoryState, TurnStatus
from investigation_agent.domain.tool_outcome import (
    BudgetConsumption,
    EvidenceField,
    EvidenceItem,
    ToolOutcome,
    canonical_fingerprint,
)

STATE_SCHEMA_VERSION = 2
EVIDENCE_INDEX_BOUNDED_NOTICE = "evidence_index_bounded"

type ExhaustedLimit = Literal["model_calls", "tool_calls", "elapsed", "recursion"]
type AnswerKind = Literal["grounded", "refusal", "closure"]
type EvidenceKind = Literal["chunk", "row", "entity", "relationship", "finding"]
type EvidentiaryStatus = Literal["verified", "confirmed", "proposed"]


class IncompatibleStateError(RuntimeError):
    code = "incompatible_state"


class OutcomeRejectedError(ValueError):
    code = "invalid_tool_outcome"


class ControlState(BaseModel):
    """Trusted binding and policy values; no model-facing schema may update this section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: Annotated[str, Field(min_length=1, max_length=128)]
    state_schema_version: PositiveInt = STATE_SCHEMA_VERSION
    policy_version: Annotated[str, Field(min_length=1, max_length=64)]


class TurnState(BaseModel):
    """Current-turn bookkeeping written only by the intake, grounding, and close hooks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: Annotated[str, Field(min_length=1, max_length=128)]
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    request_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    user_message_id: Annotated[str, Field(min_length=1, max_length=128)]
    utterance: Annotated[str, Field(min_length=1, max_length=64_000)]
    opened_at: datetime
    status: TurnStatus = TurnStatus.RUNNING
    safe_failure_code: Annotated[str | None, Field(max_length=64)] = None
    repair_count: NonNegativeInt = 0
    exhausted_limit: ExhaustedLimit | None = None
    prior_trace_carrier: tuple[tuple[str, str], ...] = ()
    guardrail_status: Annotated[str | None, Field(max_length=64)] = None
    answer_kind: AnswerKind | None = None
    pending_answer: Annotated[str | None, Field(max_length=128_000)] = None
    pending_citations: Annotated[tuple[Citation, ...], Field(max_length=256)] = ()
    assistant_message_id: Annotated[str | None, Field(max_length=128)] = None
    verification_violations: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    intake_complete: bool = False

    @model_validator(mode="after")
    def _timestamp_is_aware(self) -> TurnState:
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware")
        return self


class EvidenceCard(BaseModel):
    """Bounded, source-preserving card; full evidence stays recoverable by reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: Annotated[str, Field(min_length=1, max_length=256)]
    kind: EvidenceKind
    case_id: Annotated[str, Field(min_length=1, max_length=128)]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=32)]
    evidentiary_status: EvidentiaryStatus
    tool: Literal["search_evidence", "query_records", "find_connections"]
    display: Annotated[str | None, Field(max_length=4_000)] = None
    fields: Annotated[tuple[EvidenceField, ...], Field(max_length=64)] = ()
    suspicious_content: bool = False
    guard_status: Literal["unchecked", "allowed", "flagged"] = "unchecked"
    first_seen_turn_id: Annotated[str, Field(min_length=1, max_length=128)]
    sequence: PositiveInt


class EvidenceIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cards: dict[str, EvidenceCard] = Field(default_factory=dict)
    next_sequence: PositiveInt = 1
    dropped_cards: NonNegativeInt = 0
    coverage_notice: Annotated[str | None, Field(max_length=64)] = None

    @model_validator(mode="after")
    def _keys_match_cards(self) -> EvidenceIndex:
        for key, card in self.cards.items():
            if key != card.evidence_id:
                raise ValueError("evidence index keys must equal card identifiers")
        return self

    def ordered(self) -> tuple[EvidenceCard, ...]:
        return tuple(
            sorted(self.cards.values(), key=lambda card: (card.sequence, card.evidence_id))
        )


class ReferentBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phrase: Annotated[str, Field(min_length=1, max_length=256)]
    target_id: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    confidence: Literal["resolved", "ambiguous", "unresolved"]


class ProjectedFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: Annotated[str, Field(min_length=1, max_length=128)]
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    evidence_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    active: bool = True


class QualifiedHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: Annotated[str, Field(min_length=1, max_length=128)]
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    evidence_ids: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    qualification: Literal["proposed", "uncertain"] = "uncertain"


class WorkingProjection(BaseModel):
    """One complete replaceable semantic projection refreshed at turn close."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_turn_id: Annotated[str | None, Field(max_length=128)] = None
    user_goal: Annotated[str, Field(max_length=2_000)] = ""
    dialogue_summary: Annotated[str, Field(max_length=4_000)] = ""
    referent_bindings: Annotated[tuple[ReferentBinding, ...], Field(max_length=64)] = ()
    focus_evidence_ids: Annotated[tuple[str, ...], Field(max_length=128)] = ()
    focus_entity_ids: Annotated[tuple[str, ...], Field(max_length=128)] = ()
    active_findings: Annotated[tuple[ProjectedFinding, ...], Field(max_length=64)] = ()
    hypotheses: Annotated[tuple[QualifiedHypothesis, ...], Field(max_length=64)] = ()
    open_questions: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    next_steps: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    projection_stale: bool = False

    def referenced_evidence_ids(self) -> frozenset[str]:
        ids = set(self.focus_evidence_ids)
        ids.update(item for finding in self.active_findings for item in finding.evidence_ids)
        ids.update(item for hypothesis in self.hypotheses for item in hypothesis.evidence_ids)
        ids.update(item.target_id for item in self.referent_bindings if item.target_id)
        return frozenset(ids)


class UsageCounters(BaseModel):
    """Cumulative thread accounting; hard limits are enforced by middleware, not here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_calls: NonNegativeInt = 0
    closure_model_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    physical_attempts: NonNegativeInt = 0
    rows: NonNegativeInt = 0
    bytes: NonNegativeInt = 0
    paths: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0

    def consume(self, measured: BudgetConsumption) -> UsageCounters:
        values = self.model_dump()
        for name, amount in measured.model_dump().items():
            if name in values:
                values[name] += amount
        return UsageCounters.model_validate(values)


class InvestigationState(BaseModel):
    """Validated view of the application-owned checkpoint sections."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control: ControlState
    turn: TurnState | None = None
    evidence: EvidenceIndex = Field(default_factory=EvidenceIndex)
    projection: WorkingProjection = Field(default_factory=WorkingProjection)
    history: HistoryState = Field(default_factory=HistoryState)
    usage: UsageCounters = Field(default_factory=UsageCounters)

    def as_update(self) -> dict[str, Any]:
        """Serialize every section as JSON for a LangGraph state update."""

        return state_update(
            control=self.control,
            turn=self.turn,
            evidence=self.evidence,
            projection=self.projection,
            history=self.history,
            usage=self.usage,
        )


SECTION_NAMES: tuple[str, ...] = ("control", "turn", "evidence", "projection", "history", "usage")


def state_update(**sections: BaseModel | None) -> dict[str, Any]:
    """Serialize hook-owned sections as plain JSON; ``None`` clears the turn."""

    unknown = set(sections) - set(SECTION_NAMES)
    if unknown:
        raise ValueError(f"unknown state sections: {sorted(unknown)}")
    return {
        name: value.model_dump(mode="json") if value is not None else None
        for name, value in sections.items()
    }


def parse_state(values: Mapping[str, Any] | None) -> InvestigationState | None:
    """Validate checkpoint channel values; an absent control section means no thread."""

    if not values:
        return None
    if isinstance(values, InvestigationState):
        return values
    control = values.get("control")
    if not control:
        return None
    state = InvestigationState.model_validate(
        {name: values.get(name) for name in SECTION_NAMES if values.get(name) is not None}
    )
    ensure_compatible_state(state)
    return state


def ensure_compatible_state(state: InvestigationState) -> None:
    if state.control.state_schema_version != STATE_SCHEMA_VERSION:
        raise IncompatibleStateError(
            f"unsupported investigation state schema {state.control.state_schema_version}"
        )


def new_turn_state(
    *,
    turn_id: str,
    request_id: str,
    message_id: str,
    utterance: str,
    case_id: str,
    opened_at: datetime,
) -> TurnState:
    request_fingerprint = canonical_fingerprint(
        {"version": 1, "request_id": request_id, "case_id": case_id, "message": utterance}
    )
    return TurnState(
        turn_id=turn_id,
        request_id=request_id,
        user_message_id=message_id,
        utterance=utterance,
        request_fingerprint=request_fingerprint,
        opened_at=opened_at,
    )


def evidence_card_from_item(
    item: EvidenceItem,
    *,
    tool: Literal["search_evidence", "query_records", "find_connections"],
    turn_id: str,
    sequence: int,
    max_display_chars: int = 4_000,
) -> EvidenceCard:
    display = item.content[:max_display_chars] if item.content else None
    return EvidenceCard(
        evidence_id=item.evidence_id,
        kind=item.kind,
        case_id=item.case_id,
        content_hash=item.content_hash,
        source_refs=item.source_refs,
        evidentiary_status=item.evidentiary_status,
        tool=tool,
        display=display,
        fields=item.fields,
        suspicious_content=item.suspicious_content,
        guard_status=item.guard_status,
        first_seen_turn_id=turn_id,
        sequence=sequence,
    )


def upsert_evidence(
    index: EvidenceIndex,
    outcome: ToolOutcome,
    *,
    case_id: str,
    turn_id: str,
    max_cards: int,
    protected_ids: Iterable[str] = (),
) -> EvidenceIndex:
    """Upsert cards by stable key without status promotion or provenance overwrite.

    A duplicate delivery is a no-op. When the bound is exceeded, the oldest cards not referenced
    by the projection or the current turn are dropped and a coverage notice is recorded.
    """

    if max_cards < 1:
        raise ValueError("evidence index bound must be positive")
    if outcome.case_id != case_id:
        raise OutcomeRejectedError("tool outcome case does not match immutable control scope")
    cards = dict(index.cards)
    next_sequence = index.next_sequence
    for item in sorted(outcome.evidence, key=lambda entry: entry.evidence_id):
        if item.case_id != case_id:
            raise OutcomeRejectedError(f"evidence {item.evidence_id!r} crossed case scope")
        if any(not ref.record_id for ref in item.source_refs):
            raise OutcomeRejectedError(f"evidence {item.evidence_id!r} has an empty reference")
        if item.evidence_id in cards:
            continue
        cards[item.evidence_id] = evidence_card_from_item(
            item, tool=outcome.tool, turn_id=turn_id, sequence=next_sequence
        )
        next_sequence += 1

    dropped = index.dropped_cards
    notice = index.coverage_notice
    if len(cards) > max_cards:
        protected = set(protected_ids) | {
            card.evidence_id for card in cards.values() if card.first_seen_turn_id == turn_id
        }
        removable = sorted(
            (card for card in cards.values() if card.evidence_id not in protected),
            key=lambda card: (card.sequence, card.evidence_id),
        )
        excess = len(cards) - max_cards
        for card in removable[:excess]:
            del cards[card.evidence_id]
            dropped += 1
        notice = EVIDENCE_INDEX_BOUNDED_NOTICE
    return EvidenceIndex(
        cards=dict(sorted(cards.items())),
        next_sequence=next_sequence,
        dropped_cards=dropped,
        coverage_notice=notice,
    )


__all__ = [
    "AnswerKind",
    "ControlState",
    "EVIDENCE_INDEX_BOUNDED_NOTICE",
    "EvidenceCard",
    "EvidenceIndex",
    "ExhaustedLimit",
    "IncompatibleStateError",
    "InvestigationState",
    "OutcomeRejectedError",
    "ProjectedFinding",
    "QualifiedHypothesis",
    "ReferentBinding",
    "SECTION_NAMES",
    "STATE_SCHEMA_VERSION",
    "TurnState",
    "UsageCounters",
    "WorkingProjection",
    "ensure_compatible_state",
    "evidence_card_from_item",
    "new_turn_state",
    "parse_state",
    "state_update",
    "upsert_evidence",
]
