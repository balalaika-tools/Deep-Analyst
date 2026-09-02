from ingestion.genai.entity_extraction.prompts import (
    PROMPT_VERSION as ENTITY_PROMPT_VERSION,
)
from ingestion.genai.entity_extraction.prompts import SYSTEM_PROMPT as ENTITY_SYSTEM_PROMPT
from ingestion.genai.entity_extraction.schemas import EntityCandidateOut, EntityExtraction
from ingestion.genai.relationship_extraction.prompts import (
    PROMPT_VERSION as RELATIONSHIP_PROMPT_VERSION,
)
from ingestion.genai.relationship_extraction.prompts import (
    SYSTEM_PROMPT as RELATIONSHIP_SYSTEM_PROMPT,
)
from ingestion.genai.relationship_extraction.schemas import RelationshipCandidateOut
from pydantic import BaseModel


def _field_description(model: type[BaseModel], field_name: str) -> str:
    description = model.model_fields[field_name].description
    assert description is not None
    return description


def test_entity_contract_does_not_force_named_vessels_into_an_allowed_type() -> None:
    """Fails if the N-D1 vessel/person classification boundary disappears."""
    type_description = _field_description(EntityCandidateOut, "entity_type")
    collection_description = _field_description(EntityExtraction, "entities")

    assert ENTITY_PROMPT_VERSION == "entity-extraction@3"
    assert "vessel BLUE HORIZON" in ENTITY_SYSTEM_PROMPT
    assert "PERSON requires a human referent" in ENTITY_SYSTEM_PROMPT
    assert "vessel" in type_description
    assert "vessels" in collection_description


def test_relationship_contract_treats_a_name_phone_signature_as_uses_evidence() -> None:
    """Fails if the eM2 signature attribution or its exact-quote rule disappears."""
    predicate_description = _field_description(RelationshipCandidateOut, "predicate")
    quote_description = _field_description(RelationshipCandidateOut, "quote")

    assert RELATIONSHIP_PROMPT_VERSION == "relationship-extraction@3"
    assert "Dana Lee\n+30 690 000 0000" in RELATIONSHIP_SYSTEM_PROMPT
    assert "states that the person USES that phone" in RELATIONSHIP_SYSTEM_PROMPT
    assert "signature or contact block" in predicate_description
    assert "preserve the newline exactly" in quote_description
