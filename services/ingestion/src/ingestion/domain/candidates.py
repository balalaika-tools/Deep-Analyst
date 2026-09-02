"""Validation of model candidates: spans, types, endpoints, and rule precedence.

Every outcome is counted so the accept/reject ratio is visible per run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from evidence_model import (
    LLM_ENTITY_TYPES,
    LLM_PREDICATES,
    EntityDraft,
    EntityType,
    ExtractionMethod,
    Predicate,
    RelationshipDraft,
    RelationshipStatus,
    SourceRef,
    TextSpanLocator,
    endpoint_types_allowed,
    label_slug,
)

from ingestion.domain.chunking import Chunk
from ingestion.domain.identifiers import find_identifiers, identifier_key

ACCEPTED = "accepted"
REJECTED_SPAN = "rejected_span"
REJECTED_TYPE = "rejected_type"
REJECTED_ENDPOINT = "rejected_endpoint"
SUPERSEDED_BY_RULE = "superseded_by_rule"


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    entity_type: str
    text: str
    char_start: int | None
    char_end: int | None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationshipCandidate:
    predicate: str
    subject_type: str
    subject_text: str
    object_type: str
    object_text: str
    quote: str
    char_start: int | None
    char_end: int | None


@dataclass(slots=True)
class ChunkContext:
    """What validation may know about one chunk: its text, record, and rule entities."""

    case_id: str
    record_id: str
    chunk: Chunk
    rule_entities: list[EntityDraft]
    text_field: str = "text"


@dataclass(slots=True)
class Validation[T]:
    accepted: list[T] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)


def _span_ref(context: ChunkContext, start: int, end: int, quote: str) -> SourceRef:
    offset = context.chunk.char_start
    return SourceRef(
        record_id=context.record_id,
        locator=TextSpanLocator(
            field=context.text_field, char_start=offset + start, char_end=offset + end, quote=quote
        ),
    )


def _exact_span(
    chunk: Chunk, start: int | None, end: int | None, quote: str
) -> tuple[int, int] | None:
    """Ground a model string in source text, deriving offsets when necessary."""
    if not quote:
        return None
    if start is not None and end is not None:
        if 0 <= start < end <= len(chunk.text) and chunk.text[start:end] == quote:
            return start, end
    derived_start = chunk.text.find(quote)
    if derived_start < 0:
        return None
    return derived_start, derived_start + len(quote)


def _rule_keys(context: ChunkContext) -> set[str]:
    return {entity.normalized_key for entity in context.rule_entities if entity.normalized_key}


def _covered_by_rule(text: str, rule_keys: set[str]) -> bool:
    """True when the model's text is, or contains, an identifier a rule already owns."""
    return any(span.normalized_key in rule_keys for span in find_identifiers(text))


def validate_entity_candidates(
    context: ChunkContext, candidates: Iterable[EntityCandidate]
) -> Validation[EntityDraft]:
    result: Validation[EntityDraft] = Validation()
    rule_keys = _rule_keys(context)
    by_identity: dict[str, EntityDraft] = {}
    for candidate in candidates:
        span = _exact_span(context.chunk, candidate.char_start, candidate.char_end, candidate.text)
        if span is None:
            result.counts[REJECTED_SPAN] += 1
            continue
        char_start, char_end = span
        if _covered_by_rule(candidate.text, rule_keys):
            result.counts[SUPERSEDED_BY_RULE] += 1
            continue
        try:
            entity_type = EntityType(candidate.entity_type)
        except ValueError:
            result.counts[REJECTED_TYPE] += 1
            continue
        if entity_type not in LLM_ENTITY_TYPES:
            result.counts[REJECTED_TYPE] += 1
            continue
        draft = EntityDraft(
            case_id=context.case_id,
            entity_type=entity_type,
            label=candidate.text,
            scope_record_id=context.record_id,
            source_refs=[_span_ref(context, char_start, char_end, candidate.text)],
        )
        existing = by_identity.get(draft.entity_id)
        by_identity[draft.entity_id] = existing.with_refs(draft.source_refs) if existing else draft
        result.counts[ACCEPTED] += 1
    result.accepted = list(by_identity.values())
    return result


def alias_map(
    candidates: Sequence[EntityCandidate], accepted: Sequence[EntityDraft]
) -> dict[str, tuple[str, ...]]:
    """Aliases per accepted entity, keyed by entity id.

    Candidates join to entities on (type, text), not text alone: one chunk may accept the
    same string under two types, and those are two entities. Aliases from repeated
    candidates for one entity are unioned, in first-seen order.
    """
    entity_ids = {(entity.entity_type.value, entity.label): entity.entity_id for entity in accepted}
    collected: dict[str, list[str]] = {}
    for candidate in candidates:
        entity_id = entity_ids.get((candidate.entity_type, candidate.text))
        if entity_id is None:
            continue
        known = collected.setdefault(entity_id, [])
        known.extend(alias for alias in candidate.aliases if alias not in known)
    return {entity_id: tuple(aliases) for entity_id, aliases in collected.items() if aliases}


class EntityIndex:
    """Resolves candidate endpoint text to entities known for one chunk."""

    def __init__(
        self,
        context: ChunkContext,
        model_entities: Iterable[EntityDraft],
        aliases: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self._by_key: dict[str, EntityDraft] = {}
        self._by_text: dict[tuple[EntityType, str], EntityDraft] = {}
        for entity in context.rule_entities:
            if entity.normalized_key:
                self._by_key[entity.normalized_key] = entity
        for entity in model_entities:
            self._by_text[(entity.entity_type, label_slug(entity.label))] = entity
            for alias in (aliases or {}).get(entity.entity_id, ()):
                self._by_text.setdefault((entity.entity_type, label_slug(alias)), entity)

    def resolve(self, entity_type: EntityType, text: str) -> EntityDraft | None:
        key = identifier_key(entity_type, text)
        if key is not None:
            return self._by_key.get(key)
        return self._by_text.get((entity_type, label_slug(text)))


def validate_relationship_candidates(
    context: ChunkContext,
    candidates: Iterable[RelationshipCandidate],
    index: EntityIndex,
) -> Validation[RelationshipDraft]:
    """Order matters: span, then ontology types, then endpoint resolution."""
    result: Validation[RelationshipDraft] = Validation()
    seen: set[str] = set()
    for candidate in candidates:
        span = _exact_span(context.chunk, candidate.char_start, candidate.char_end, candidate.quote)
        if span is None:
            result.counts[REJECTED_SPAN] += 1
            continue
        char_start, char_end = span
        try:
            predicate = Predicate(candidate.predicate)
            subject_type = EntityType(candidate.subject_type)
            object_type = EntityType(candidate.object_type)
        except ValueError:
            result.counts[REJECTED_TYPE] += 1
            continue
        if predicate not in LLM_PREDICATES or not endpoint_types_allowed(
            predicate, subject_type, object_type
        ):
            result.counts[REJECTED_TYPE] += 1
            continue
        subject = index.resolve(subject_type, candidate.subject_text)
        obj = index.resolve(object_type, candidate.object_text)
        if subject is None or obj is None or subject.entity_id == obj.entity_id:
            result.counts[REJECTED_ENDPOINT] += 1
            continue
        draft = RelationshipDraft(
            case_id=context.case_id,
            subject=subject.endpoint(),
            predicate=predicate,
            object=obj.endpoint(),
            status=RelationshipStatus.PROPOSED,
            method=ExtractionMethod.LLM,
            source_record_id=context.record_id,
            source_refs=[_span_ref(context, char_start, char_end, candidate.quote)],
        )
        if draft.relationship_id in seen:
            continue
        seen.add(draft.relationship_id)
        result.accepted.append(draft)
        result.counts[ACCEPTED] += 1
    return result
