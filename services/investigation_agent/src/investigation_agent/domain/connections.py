"""Business contracts for bounded, source-preserving graph traversal."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from evidence_model import (
    ALLOWED_ENDPOINTS,
    PREDICATE_DESCRIPTIONS,
    RELATIONSHIP_STATUS_DESCRIPTIONS,
    EntityType,
    Predicate,
    RelationshipStatus,
    SourceRef,
)
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _predicate_filter_description() -> str:
    definitions = []
    for predicate in Predicate:
        endpoints = ", ".join(
            f"{subject.value}->{object_.value}"
            for subject, object_ in sorted(ALLOWED_ENDPOINTS[predicate])
        )
        definitions.append(
            f"{predicate.value}: {PREDICATE_DESCRIPTIONS[predicate]} ({endpoints})"
        )
    return (
        "Relationship predicates to traverse; empty means all predicates. Stored predicates use "
        "subject->object direction, although traversal can follow an edge in either direction. "
        "Ontology: "
        + "; ".join(definitions)
        + "."
    )


_STATUS_FILTER_DESCRIPTION = (
    "Relationship confidence classes to include. Default to confirmed for factual answers. "
    + "; ".join(
        f"{status.value}: {RELATIONSHIP_STATUS_DESCRIPTIONS[status]}"
        for status in RelationshipStatus
    )
    + ". Include proposed only when the analyst's question calls for hypotheses, and label them "
    "explicitly."
)
_PREDICATE_FILTER_DESCRIPTION = _predicate_filter_description()


class StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConnectionFilters(StrictModel):
    statuses: Annotated[
        tuple[RelationshipStatus, ...],
        Field(min_length=1, max_length=2, description=_STATUS_FILTER_DESCRIPTION),
    ] = (
        RelationshipStatus.CONFIRMED,
    )
    predicates: Annotated[
        tuple[Predicate, ...], Field(max_length=8, description=_PREDICATE_FILTER_DESCRIPTION)
    ] = ()
    target_entity_types: Annotated[
        tuple[EntityType, ...],
        Field(
            max_length=10,
            description=(
                "Keep paths whose terminal non-seed node has one of these entity types. "
                "Intermediate nodes of other types remain traversable; empty keeps all paths. "
                "Only types present in the predicate endpoint ontology can match a relationship "
                "path."
            ),
        ),
    ] = ()
    occurred_from: datetime | None = Field(
        default=None,
        description=(
            "Include relationships occurring at or after this timezone-aware timestamp. "
            "Relationships without occurred_at are excluded when set."
        ),
    )
    occurred_to: datetime | None = Field(
        default=None,
        description=(
            "Include relationships occurring at or before this timezone-aware timestamp. "
            "Relationships without occurred_at are excluded when set."
        ),
    )

    @model_validator(mode="after")
    def _validate_filters(self) -> ConnectionFilters:
        if len(self.statuses) != len(set(self.statuses)):
            raise ValueError("relationship statuses must be unique")
        if len(self.predicates) != len(set(self.predicates)):
            raise ValueError("relationship predicates must be unique")
        if len(self.target_entity_types) != len(set(self.target_entity_types)):
            raise ValueError("target entity types must be unique")
        if self.occurred_from and self.occurred_from.tzinfo is None:
            raise ValueError("occurred_from must be timezone-aware")
        if self.occurred_to and self.occurred_to.tzinfo is None:
            raise ValueError("occurred_to must be timezone-aware")
        if self.occurred_from and self.occurred_to and self.occurred_from > self.occurred_to:
            raise ValueError("occurred_from cannot follow occurred_to")
        return self


class FindConnectionsInput(StrictModel):
    """Model-authored graph request over the global evidence graph."""

    schema_version: Literal[1] = 1
    seed_entity_ids: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...],
        Field(
            min_length=1,
            max_length=32,
            description=(
                "Exact globally stable entity IDs discovered in retrieved evidence or structured "
                "records. Supply IDs, never entity names, labels, or invented identifiers."
            ),
        ),
    ]
    filters: ConnectionFilters = Field(default_factory=ConnectionFilters)
    max_depth: Annotated[
        int, Field(ge=1, le=1_000, description="Maximum relationship hops from each seed.")
    ] = 2
    max_paths: Annotated[
        int, Field(ge=1, le=100_000, description="Maximum returned cycle-free paths.")
    ] = 25
    max_nodes: Annotated[
        int, Field(ge=1, le=100_000, description="Maximum distinct returned graph nodes.")
    ] = 100
    max_edges: Annotated[
        int, Field(ge=1, le=100_000, description="Maximum distinct returned graph edges.")
    ] = 200
    max_rows: Annotated[
        int,
        Field(
            ge=1,
            le=1_000_000,
            description="Maximum database rows examined; the server may apply a lower bound.",
        ),
    ] = 1_000

    @model_validator(mode="after")
    def _unique_seeds(self) -> FindConnectionsInput:
        if len(self.seed_entity_ids) != len(set(self.seed_entity_ids)):
            raise ValueError("seed entity identifiers must be unique")
        return self


class GraphLimits(StrictModel):
    max_depth: Annotated[int, Field(ge=1, le=32)]
    max_paths: Annotated[int, Field(ge=1, le=10_000)]
    max_nodes: Annotated[int, Field(ge=1, le=100_000)]
    max_edges: Annotated[int, Field(ge=1, le=100_000)]
    max_rows: Annotated[int, Field(ge=1, le=1_000_000)]


class ResolvedSourceRef(StrictModel):
    content_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    source_ref: SourceRef


class GraphNode(StrictModel):
    entity_id: Annotated[str, Field(min_length=1, max_length=256)]
    entity_type: EntityType
    label: Annotated[str, Field(min_length=1, max_length=2_000)]
    sources: Annotated[tuple[ResolvedSourceRef, ...], Field(min_length=1, max_length=32)]


class GraphEdge(StrictModel):
    relationship_id: Annotated[str, Field(min_length=1, max_length=256)]
    subject_entity_id: Annotated[str, Field(min_length=1, max_length=256)]
    predicate: Predicate
    object_entity_id: Annotated[str, Field(min_length=1, max_length=256)]
    status: RelationshipStatus
    occurred_at: datetime | None = None
    sources: Annotated[tuple[ResolvedSourceRef, ...], Field(min_length=1, max_length=32)]


class ConnectionPath(StrictModel):
    node_ids: Annotated[tuple[str, ...], Field(min_length=2, max_length=33)]
    edge_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def _valid_path_shape(self) -> ConnectionPath:
        if len(self.node_ids) != len(self.edge_ids) + 1:
            raise ValueError("a path must contain exactly one more node than edge")
        if len(self.node_ids) != len(set(self.node_ids)):
            raise ValueError("cyclic paths are not supported")
        return self


class GraphEvidence(StrictModel):
    evidence_id: Annotated[str, Field(min_length=1, max_length=512)]
    content_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    source_refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=32)]
    kind: Literal["entity", "relationship", "finding"]
    content: Annotated[str, Field(min_length=1, max_length=32_000)]
    evidentiary_status: Literal["verified", "confirmed", "proposed"]
    provenance: Literal["graph"] = "graph"


class GraphConsumption(StrictModel):
    model_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 1
    rows: NonNegativeInt = 0
    bytes: NonNegativeInt = 0
    semantic_attempts: NonNegativeInt = 1
    physical_attempts: NonNegativeInt = 0
    paths: NonNegativeInt = 0


class FindConnectionsOutcome(StrictModel):
    schema_version: Literal[1] = 1
    call_id: Annotated[str, Field(min_length=1, max_length=128)]
    intent_fingerprint: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    status: Literal["connections_found", "no_support"]
    semantic_attempts: Literal[1] = 1
    physical_attempts: Annotated[int, Field(ge=0, le=100)]
    effective_limits: GraphLimits
    paths: Annotated[tuple[ConnectionPath, ...], Field(max_length=10_000)] = ()
    nodes: Annotated[tuple[GraphNode, ...], Field(max_length=100_000)] = ()
    edges: Annotated[tuple[GraphEdge, ...], Field(max_length=100_000)] = ()
    evidence: Annotated[tuple[GraphEvidence, ...], Field(max_length=100_000)] = ()
    warnings: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=16)
    ] = ()
    consumption: GraphConsumption = Field(default_factory=GraphConsumption)


__all__ = [
    "ConnectionFilters",
    "ConnectionPath",
    "FindConnectionsInput",
    "FindConnectionsOutcome",
    "GraphConsumption",
    "GraphEdge",
    "GraphEvidence",
    "GraphLimits",
    "GraphNode",
    "ResolvedSourceRef",
]
