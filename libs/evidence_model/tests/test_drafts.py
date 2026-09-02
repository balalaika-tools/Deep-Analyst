import pytest
from evidence_model import (
    EndpointRef,
    EntityDraft,
    EntityType,
    ExtractionMethod,
    FieldLocator,
    OntologyViolation,
    Predicate,
    RelationshipDraft,
    RelationshipStatus,
    SourceRef,
)
from pydantic import ValidationError

CASE = "case_trg_001"
REF = SourceRef(record_id="cdr:c01", locator=FieldLocator(field="calling_msisdn"))


def phone(key: str) -> EntityDraft:
    return EntityDraft(
        case_id=CASE,
        entity_type=EntityType.PHONE,
        label=f"+{key}",
        normalized_key=key,
        source_refs=[REF],
    )


def test_keyed_entity_id_is_the_normalized_value_and_is_reusable() -> None:
    assert phone("306971234567").entity_id == "PHONE:306971234567"
    assert phone("306971234567").entity_id == phone("306971234567").entity_id


def test_actor_mentions_from_different_records_stay_distinct() -> None:
    def person(record_id: str) -> EntityDraft:
        return EntityDraft(
            case_id=CASE,
            entity_type=EntityType.PERSON,
            label="Alexandros Mavridis",
            scope_record_id=record_id,
            source_refs=[SourceRef(record_id=record_id, locator=FieldLocator(field="x"))],
        )

    assert person("bank:acct_pa").entity_id != person("docs:R-01").entity_id
    assert person("docs:R-01").entity_id == "PERSON:docs:R-01:alexandros-mavridis"


def test_keyed_type_without_key_and_actor_with_key_are_both_rejected() -> None:
    with pytest.raises((OntologyViolation, ValidationError), match="requires a normalized key"):
        EntityDraft(case_id=CASE, entity_type=EntityType.PHONE, label="x", source_refs=[REF])
    with pytest.raises((OntologyViolation, ValidationError), match="must not carry"):
        EntityDraft(
            case_id=CASE,
            entity_type=EntityType.PERSON,
            label="x",
            normalized_key="x",
            scope_record_id="r",
            source_refs=[REF],
        )


def test_entity_without_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EntityDraft(
            case_id=CASE,
            entity_type=EntityType.PHONE,
            label="x",
            normalized_key="1",
            source_refs=[],
        )


def test_with_refs_merges_without_duplicates() -> None:
    other = SourceRef(record_id="docs:R-01", locator=FieldLocator(field="text"))
    merged = phone("1").with_refs([REF, other, other])
    assert merged.source_refs == [REF, other]


def _relationship(**overrides: object) -> RelationshipDraft:
    values: dict[str, object] = {
        "case_id": CASE,
        "subject": EndpointRef(entity_id="PHONE:1", entity_type=EntityType.PHONE),
        "predicate": Predicate.COMMUNICATED_WITH,
        "object": EndpointRef(entity_id="PHONE:2", entity_type=EntityType.PHONE),
        "status": RelationshipStatus.CONFIRMED,
        "method": ExtractionMethod.DETERMINISTIC,
        "source_record_id": "cdr:c01",
        "source_refs": [REF],
    }
    values.update(overrides)
    return RelationshipDraft.model_validate(values)


def test_relationship_id_is_deterministic_per_source_record() -> None:
    assert _relationship().relationship_id == _relationship().relationship_id
    assert (
        _relationship().relationship_id != _relationship(source_record_id="cdr:c02").relationship_id
    )


def test_relationship_with_disallowed_endpoints_is_rejected() -> None:
    with pytest.raises((OntologyViolation, ValidationError), match="HELD_BY does not allow"):
        _relationship(predicate=Predicate.HELD_BY)


def test_llm_relationship_cannot_be_confirmed() -> None:
    with pytest.raises((OntologyViolation, ValidationError), match="must have status proposed"):
        _relationship(method=ExtractionMethod.LLM)


def test_relationship_without_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _relationship(source_refs=[])
