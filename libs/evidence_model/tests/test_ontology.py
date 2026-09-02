import pytest
from evidence_model import (
    ALLOWED_ENDPOINTS,
    EntityType,
    ExtractionMethod,
    OntologyViolation,
    Predicate,
    RelationshipStatus,
    check_endpoint_types,
    check_status_method,
    endpoint_types_allowed,
)


def test_every_predicate_declares_at_least_one_endpoint_pair() -> None:
    assert set(ALLOWED_ENDPOINTS) == set(Predicate)
    assert all(ALLOWED_ENDPOINTS[predicate] for predicate in Predicate)


def test_held_by_from_a_phone_is_rejected() -> None:
    with pytest.raises(OntologyViolation, match="HELD_BY does not allow PHONE->PERSON"):
        check_endpoint_types(Predicate.HELD_BY, EntityType.PHONE, EntityType.PERSON)


def test_uses_from_person_to_device_is_accepted() -> None:
    check_endpoint_types(Predicate.USES, EntityType.PERSON, EntityType.DEVICE)
    assert endpoint_types_allowed(Predicate.USES, EntityType.PERSON, EntityType.PHONE)


def test_communicated_with_requires_the_same_endpoint_type() -> None:
    assert endpoint_types_allowed(
        Predicate.COMMUNICATED_WITH, EntityType.EMAIL_ADDRESS, EntityType.EMAIL_ADDRESS
    )
    assert not endpoint_types_allowed(
        Predicate.COMMUNICATED_WITH, EntityType.PHONE, EntityType.EMAIL_ADDRESS
    )


def test_model_edges_cannot_be_confirmed() -> None:
    with pytest.raises(OntologyViolation, match="method llm must have status proposed"):
        check_status_method(RelationshipStatus.CONFIRMED, ExtractionMethod.LLM)


def test_confirmed_requires_deterministic_and_proposed_accepts_both_methods() -> None:
    check_status_method(RelationshipStatus.CONFIRMED, ExtractionMethod.DETERMINISTIC)
    check_status_method(RelationshipStatus.PROPOSED, ExtractionMethod.DETERMINISTIC)
    check_status_method(RelationshipStatus.PROPOSED, ExtractionMethod.LLM)
