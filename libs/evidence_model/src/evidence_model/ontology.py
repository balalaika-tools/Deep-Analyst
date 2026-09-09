"""The small ontology: entity types, predicates, endpoint rules, and status semantics.

The store rejects any relationship that does not satisfy these rules. Keeping the
rules here, beside the schema, lets ingestion and the future agent agree on them
without importing each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    DEVICE = "DEVICE"
    FINANCIAL_ACCOUNT = "FINANCIAL_ACCOUNT"
    VESSEL = "VESSEL"
    PHONE = "PHONE"
    EMAIL_ADDRESS = "EMAIL_ADDRESS"
    TRANSACTION = "TRANSACTION"
    LOCATION = "LOCATION"
    INVOICE_REF = "INVOICE_REF"


class Predicate(StrEnum):
    USES = "USES"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    DIRECTOR_OF = "DIRECTOR_OF"
    KIN_OF = "KIN_OF"
    HELD_BY = "HELD_BY"
    COMMUNICATED_WITH = "COMMUNICATED_WITH"
    TRANSFERRED_TO = "TRANSFERRED_TO"
    REFERENCES = "REFERENCES"


class RelationshipStatus(StrEnum):
    CONFIRMED = "confirmed"
    PROPOSED = "proposed"


class ExtractionMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"


type EndpointPair = tuple[EntityType, EntityType]

_ACTORS: frozenset[EntityType] = frozenset({EntityType.PERSON, EntityType.ORGANIZATION})
_COMMUNICATION_ENDPOINTS: frozenset[EntityType] = frozenset(
    {EntityType.PHONE, EntityType.EMAIL_ADDRESS}
)

ALLOWED_ENDPOINTS: Mapping[Predicate, frozenset[EndpointPair]] = {
    Predicate.USES: frozenset(
        {(EntityType.PERSON, EntityType.PHONE), (EntityType.PERSON, EntityType.DEVICE)}
    ),
    Predicate.ASSOCIATED_WITH: frozenset({(EntityType.PERSON, EntityType.ORGANIZATION)}),
    Predicate.DIRECTOR_OF: frozenset({(EntityType.PERSON, EntityType.ORGANIZATION)}),
    Predicate.KIN_OF: frozenset({(EntityType.PERSON, EntityType.PERSON)}),
    Predicate.HELD_BY: frozenset({(EntityType.FINANCIAL_ACCOUNT, actor) for actor in _ACTORS}),
    # Same compatible type on both sides: phone with phone, email with email.
    Predicate.COMMUNICATED_WITH: frozenset(
        {(endpoint, endpoint) for endpoint in _COMMUNICATION_ENDPOINTS}
    ),
    Predicate.TRANSFERRED_TO: frozenset(
        {(EntityType.FINANCIAL_ACCOUNT, EntityType.FINANCIAL_ACCOUNT)}
    ),
    Predicate.REFERENCES: frozenset({(EntityType.TRANSACTION, EntityType.INVOICE_REF)}),
}

PREDICATE_DESCRIPTIONS: Mapping[Predicate, str] = {
    Predicate.USES: "a person uses a phone or device",
    Predicate.ASSOCIATED_WITH: "a person has a general association with an organization",
    Predicate.DIRECTOR_OF: "a person is a director of an organization",
    Predicate.KIN_OF: "two people have a family relationship",
    Predicate.HELD_BY: "a financial account is held by a person or organization",
    Predicate.COMMUNICATED_WITH: "two phone numbers or two email addresses communicated",
    Predicate.TRANSFERRED_TO: "one financial account transferred value to another account",
    Predicate.REFERENCES: "a transaction references an invoice identifier",
}

RELATIONSHIP_STATUS_DESCRIPTIONS: Mapping[RelationshipStatus, str] = {
    RelationshipStatus.CONFIRMED: "derived by deterministic extraction rules",
    RelationshipStatus.PROPOSED: "a hypothesis that may have been proposed by a model",
}

# What a model may propose. Everything else comes from deterministic rules only.
LLM_ENTITY_TYPES: frozenset[EntityType] = frozenset(
    {EntityType.PERSON, EntityType.ORGANIZATION, EntityType.LOCATION}
)
LLM_PREDICATES: frozenset[Predicate] = frozenset(
    {Predicate.USES, Predicate.ASSOCIATED_WITH, Predicate.DIRECTOR_OF, Predicate.KIN_OF}
)

# Types whose exact normalized value identifies the entity and may reuse one row.
KEYED_ENTITY_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.PHONE,
        EntityType.EMAIL_ADDRESS,
        EntityType.FINANCIAL_ACCOUNT,
        EntityType.DEVICE,
        EntityType.TRANSACTION,
        EntityType.INVOICE_REF,
        EntityType.VESSEL,
    }
)


class OntologyViolation(ValueError):
    """A relationship or entity breaks a rule of the ontology."""


def endpoint_types_allowed(
    predicate: Predicate, subject_type: EntityType, object_type: EntityType
) -> bool:
    return (subject_type, object_type) in ALLOWED_ENDPOINTS[predicate]


def check_endpoint_types(
    predicate: Predicate, subject_type: EntityType, object_type: EntityType
) -> None:
    if not endpoint_types_allowed(predicate, subject_type, object_type):
        allowed = ", ".join(
            f"{s.value}->{o.value}" for s, o in sorted(ALLOWED_ENDPOINTS[predicate])
        )
        raise OntologyViolation(
            f"{predicate.value} does not allow {subject_type.value}->{object_type.value}; "
            f"allowed: {allowed}"
        )


def check_status_method(status: RelationshipStatus, method: ExtractionMethod) -> None:
    """A confirmed edge must come from a rule; a model edge is always proposed."""
    if method is ExtractionMethod.LLM and status is not RelationshipStatus.PROPOSED:
        raise OntologyViolation("a relationship with method llm must have status proposed")
    if status is RelationshipStatus.CONFIRMED and method is not ExtractionMethod.DETERMINISTIC:
        raise OntologyViolation("a confirmed relationship must have method deterministic")
