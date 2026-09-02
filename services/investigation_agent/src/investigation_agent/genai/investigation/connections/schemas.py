"""Input, output, evidence, and reader contracts for graph traversal."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol

from evidence_model import EntityType, Predicate, RelationshipStatus, SourceRef
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConnectionFilters(StrictModel):
    statuses: Annotated[tuple[RelationshipStatus, ...], Field(min_length=1, max_length=2)] = (
        RelationshipStatus.CONFIRMED,
    )
    predicates: Annotated[tuple[Predicate, ...], Field(max_length=8)] = ()
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None

    @model_validator(mode="after")
    def _validate_filters(self) -> ConnectionFilters:
        if len(self.statuses) != len(set(self.statuses)):
            raise ValueError("relationship statuses must be unique")
        if len(self.predicates) != len(set(self.predicates)):
            raise ValueError("relationship predicates must be unique")
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
        Field(min_length=1, max_length=32),
    ]
    filters: ConnectionFilters = Field(default_factory=ConnectionFilters)
    max_depth: Annotated[int, Field(ge=1, le=1_000)] = 2
    max_paths: Annotated[int, Field(ge=1, le=100_000)] = 25
    max_nodes: Annotated[int, Field(ge=1, le=100_000)] = 100
    max_edges: Annotated[int, Field(ge=1, le=100_000)] = 200
    max_rows: Annotated[int, Field(ge=1, le=1_000_000)] = 1_000

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
    source_refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=1)]
    kind: Literal["entity", "relationship"]
    content: Annotated[str, Field(min_length=1, max_length=4_000)]
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


class GraphReader(Protocol):
    async def load_graph_entities(
        self,
        *,
        entity_ids: frozenset[str],
        row_limit: int,
        deadline: float,
    ) -> tuple[GraphNode, ...]: ...

    async def load_graph_edges(
        self,
        *,
        frontier_entity_ids: frozenset[str],
        filters: ConnectionFilters,
        row_limit: int,
        deadline: float,
    ) -> tuple[GraphEdge, ...]: ...


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
    "GraphReader",
    "ResolvedSourceRef",
]
