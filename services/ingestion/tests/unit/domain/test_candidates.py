from evidence_model import EntityType, ExtractionMethod, RelationshipStatus
from ingestion.domain.candidates import (
    ACCEPTED,
    REJECTED_ENDPOINT,
    REJECTED_SPAN,
    REJECTED_TYPE,
    SUPERSEDED_BY_RULE,
    ChunkContext,
    EntityCandidate,
    EntityIndex,
    RelationshipCandidate,
    alias_map,
    validate_entity_candidates,
    validate_relationship_candidates,
)
from ingestion.domain.chunking import Chunk
from ingestion.domain.edges import identifier_entities
from ingestion.domain.identifiers import find_identifiers

TEXT = (
    "A. Mavridis / Alexandros Mavridis, also known as Alex, was seen at Flisvos Marina. "
    "He uses telephone +30 697 123 4567. There is a possible, unconfirmed association "
    "with Meridian Consulting Ltd."
)


def _context() -> ChunkContext:
    rule_entities = identifier_entities("docs:R-01", find_identifiers(TEXT))
    return ChunkContext(
        record_id="docs:R-01",
        chunk=Chunk(0, len(TEXT), TEXT),
        rule_entities=rule_entities,
    )


def _entity(entity_type: str, text: str, **overrides: object) -> EntityCandidate:
    start = TEXT.index(text)
    values: dict[str, object] = {
        "entity_type": entity_type,
        "text": text,
        "char_start": start,
        "char_end": start + len(text),
    }
    values.update(overrides)
    return EntityCandidate(**values)  # type: ignore[arg-type]


def test_entity_candidates_are_validated_span_then_type_then_rule_precedence() -> None:
    context = _context()
    candidates = [
        _entity("PERSON", "Alexandros Mavridis"),
        _entity("ORGANIZATION", "Meridian Consulting Ltd"),
        _entity("PERSON", "Alexandros Mavridis", char_start=0),
        _entity("VESSEL", "Flisvos Marina"),
        _entity("PHONE", "+30 697 123 4567"),
    ]

    result = validate_entity_candidates(context, candidates)

    assert result.counts == {ACCEPTED: 3, REJECTED_TYPE: 1, SUPERSEDED_BY_RULE: 1}
    assert [e.entity_id for e in result.accepted] == [
        "PERSON:docs:R-01:alexandros-mavridis",
        "ORGANIZATION:docs:R-01:meridian-consulting-ltd",
    ]
    assert all(ref.locator.matches(TEXT) for e in result.accepted for ref in e.source_refs)  # type: ignore[union-attr]


def test_entity_span_is_derived_when_the_model_does_not_return_offsets() -> None:
    context = _context()
    candidate = EntityCandidate("PERSON", "Alexandros Mavridis", None, None)

    result = validate_entity_candidates(context, [candidate])

    (entity,) = result.accepted
    locator = entity.source_refs[0].locator
    assert result.counts == {ACCEPTED: 1}
    assert locator.char_start == TEXT.index(candidate.text)  # type: ignore[union-attr]
    assert locator.matches(TEXT)  # type: ignore[union-attr]


def test_the_same_mention_twice_in_one_chunk_merges_its_evidence() -> None:
    context = _context()
    first = _entity("PERSON", "Alexandros Mavridis")
    second_start = TEXT.rindex("Mavridis")
    second = EntityCandidate("PERSON", "Mavridis", second_start, second_start + len("Mavridis"))
    (person,) = [
        e
        for e in validate_entity_candidates(context, [first, second]).accepted
        if e.label == "Alexandros Mavridis"
    ]
    assert len(person.source_refs) == 1
    both = validate_entity_candidates(context, [first, first]).accepted
    assert len(both) == 1 and len(both[0].source_refs) == 1


def test_aliases_follow_the_type_of_the_candidate_that_carried_them() -> None:
    context = _context()
    candidates = [
        _entity("LOCATION", "Flisvos Marina"),
        _entity("ORGANIZATION", "Flisvos Marina", aliases=("Flisvos Marina S.A.",)),
    ]
    accepted = validate_entity_candidates(context, candidates).accepted

    aliases = alias_map(candidates, accepted)

    assert aliases == {"ORGANIZATION:docs:R-01:flisvos-marina": ("Flisvos Marina S.A.",)}
    resolved = EntityIndex(context, accepted, aliases).resolve(
        EntityType.ORGANIZATION, "Flisvos Marina S.A."
    )
    assert resolved is not None and resolved.entity_id == aliases.popitem()[0]


def test_aliases_of_repeated_candidates_for_one_entity_are_unioned() -> None:
    context = _context()
    candidates = [
        _entity("PERSON", "Alexandros Mavridis", aliases=("Alex",)),
        _entity("PERSON", "Alexandros Mavridis", char_start=0, aliases=("A. Mavridis", "Alex")),
    ]
    accepted = validate_entity_candidates(context, candidates).accepted

    aliases = alias_map(candidates, accepted)

    assert aliases == {"PERSON:docs:R-01:alexandros-mavridis": ("Alex", "A. Mavridis")}


def _relationship(**overrides: object) -> RelationshipCandidate:
    quote = "He uses telephone +30 697 123 4567."
    start = TEXT.index(quote)
    values: dict[str, object] = {
        "predicate": "USES",
        "subject_type": "PERSON",
        "subject_text": "Alexandros Mavridis",
        "object_type": "PHONE",
        "object_text": "+30 697 123 4567",
        "quote": quote,
        "char_start": start,
        "char_end": start + len(quote),
    }
    values.update(overrides)
    return RelationshipCandidate(**values)  # type: ignore[arg-type]


def _index(context: ChunkContext) -> EntityIndex:
    entities = validate_entity_candidates(
        context,
        [
            _entity("PERSON", "Alexandros Mavridis"),
            _entity("ORGANIZATION", "Meridian Consulting Ltd"),
        ],
    ).accepted
    aliases = {entities[0].entity_id: ("Alex", "A. Mavridis")}
    return EntityIndex(context, entities, aliases)


def test_identifier_endpoint_resolves_to_the_rule_entity_and_edge_is_proposed() -> None:
    context = _context()
    result = validate_relationship_candidates(context, [_relationship()], _index(context))

    (edge,) = result.accepted
    assert result.counts == {ACCEPTED: 1}
    assert edge.object.entity_id == "PHONE:306971234567"
    assert edge.subject.entity_id == "PERSON:docs:R-01:alexandros-mavridis"
    assert edge.status is RelationshipStatus.PROPOSED and edge.method is ExtractionMethod.LLM
    assert edge.source_refs[0].locator.matches(TEXT)  # type: ignore[union-attr]


def test_rejections_are_counted_by_outcome() -> None:
    context = _context()
    index = _index(context)
    candidates = [
        _relationship(quote="He uses telephone +30 697 123 4568."),
        _relationship(quote=""),
        _relationship(predicate="DIRECTOR_OF", object_type="PHONE"),
        _relationship(predicate="HELD_BY", subject_type="FINANCIAL_ACCOUNT", object_type="PERSON"),
        _relationship(object_type="PHONE", object_text="+30 699 000 0000"),
        _relationship(
            predicate="ASSOCIATED_WITH", object_type="ORGANIZATION", object_text="Unknown Corp"
        ),
        _relationship(
            predicate="ASSOCIATED_WITH",
            object_type="ORGANIZATION",
            object_text="Meridian Consulting Ltd",
            subject_text="Alex",
        ),
    ]

    result = validate_relationship_candidates(context, candidates, index)

    assert result.counts == {REJECTED_SPAN: 2, REJECTED_TYPE: 2, REJECTED_ENDPOINT: 2, ACCEPTED: 1}
    assert result.accepted[0].object.entity_type is EntityType.ORGANIZATION


def test_duplicate_relationship_candidates_collapse() -> None:
    context = _context()
    result = validate_relationship_candidates(
        context, [_relationship(), _relationship()], _index(context)
    )
    assert len(result.accepted) == 1


def test_relationship_quote_span_is_derived_when_offsets_are_missing() -> None:
    context = _context()
    candidate = _relationship(char_start=None, char_end=None)

    result = validate_relationship_candidates(context, [candidate], _index(context))

    (relationship,) = result.accepted
    locator = relationship.source_refs[0].locator
    assert result.counts == {ACCEPTED: 1}
    assert locator.char_start == TEXT.index(candidate.quote)  # type: ignore[union-attr]
    assert locator.matches(TEXT)  # type: ignore[union-attr]
