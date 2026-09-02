"""Validated write-side shapes.

Table rows do not validate on construction, so every entity and relationship enters
the store through one of these models. Construction enforces the ontology, the
status/method rule, and the provenance requirement, and derives the deterministic
identifiers that make writes idempotent.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evidence_model.ontology import (
    KEYED_ENTITY_TYPES,
    EntityType,
    ExtractionMethod,
    OntologyViolation,
    Predicate,
    RelationshipStatus,
    check_endpoint_types,
    check_status_method,
)
from evidence_model.provenance import SourceRef, require_source_refs

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def label_slug(label: str) -> str:
    return _SLUG_PATTERN.sub("-", label.casefold()).strip("-")


class EntityDraft(BaseModel):
    """An entity as produced by a rule or accepted from a model.

    A keyed entity (phone, IBAN, ...) is identified by its normalized value and may be
    reused across sources. An actor mention (person, organization, location) is scoped
    to the record that mentions it so that names alone never merge two people.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    entity_type: EntityType
    label: str
    normalized_key: str | None = None
    scope_record_id: str | None = None
    source_refs: list[SourceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def _identity_is_well_formed(self) -> EntityDraft:
        keyed = self.entity_type in KEYED_ENTITY_TYPES
        if keyed and not self.normalized_key:
            raise OntologyViolation(f"{self.entity_type.value} requires a normalized key")
        if not keyed and self.normalized_key:
            raise OntologyViolation(f"{self.entity_type.value} must not carry a normalized key")
        if not keyed and not self.scope_record_id:
            raise OntologyViolation(f"{self.entity_type.value} requires a scope record")
        require_source_refs(self.source_refs)
        return self

    @property
    def entity_id(self) -> str:
        if self.normalized_key:
            return f"{self.entity_type.value}:{self.normalized_key}"
        return f"{self.entity_type.value}:{self.scope_record_id}:{label_slug(self.label)}"

    def endpoint(self) -> EndpointRef:
        return EndpointRef(entity_id=self.entity_id, entity_type=self.entity_type)

    def with_refs(self, refs: list[SourceRef]) -> EntityDraft:
        """The same entity with additional evidence, duplicates removed."""
        merged = list(self.source_refs)
        for ref in refs:
            if ref not in merged:
                merged.append(ref)
        return self.model_copy(update={"source_refs": merged})


class EndpointRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_id: str
    entity_type: EntityType


class RelationshipDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    subject: EndpointRef
    predicate: Predicate
    object: EndpointRef
    status: RelationshipStatus
    method: ExtractionMethod
    source_record_id: str
    occurred_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_refs: list[SourceRef] = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _rules_hold(self) -> RelationshipDraft:
        check_endpoint_types(self.predicate, self.subject.entity_type, self.object.entity_type)
        check_status_method(self.status, self.method)
        require_source_refs(self.source_refs)
        return self

    @property
    def relationship_id(self) -> str:
        key = "|".join(
            (
                self.case_id,
                self.subject.entity_id,
                self.predicate.value,
                self.object.entity_id,
                self.source_record_id,
            )
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
