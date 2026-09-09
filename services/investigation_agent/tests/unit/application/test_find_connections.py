from __future__ import annotations

import asyncio

import pytest
from evidence_model import EntityType, FieldLocator, Predicate, RelationshipStatus, SourceRef
from investigation_agent.application.find_connections import FindConnections
from investigation_agent.domain.connections import (
    ConnectionFilters,
    FindConnectionsInput,
    GraphEdge,
    GraphLimits,
    GraphNode,
    ResolvedSourceRef,
)
from pydantic import ValidationError

HASH = "d" * 64


def source(name: str) -> ResolvedSourceRef:
    return ResolvedSourceRef(
        content_hash=HASH,
        source_ref=SourceRef(
            record_id=f"record:{name}",
            locator=FieldLocator(field="payload"),
        ),
    )


def node(name: str, entity_type: EntityType = EntityType.PERSON) -> GraphNode:
    return GraphNode(
        entity_id=name,
        entity_type=entity_type,
        label=f"Person {name}",
        sources=(source(name),),
    )


def edge(name: str, left: str, right: str, *, proposed: bool = False) -> GraphEdge:
    return GraphEdge(
        relationship_id=name,
        subject_entity_id=left,
        predicate=Predicate.KIN_OF,
        object_entity_id=right,
        status=(RelationshipStatus.PROPOSED if proposed else RelationshipStatus.CONFIRMED),
        sources=(source(name),),
    )


class _GraphReader:
    def __init__(self, *, reverse: bool = False, omit: frozenset[str] = frozenset()) -> None:
        self.reverse = reverse
        self.omit = omit
        self.nodes = {name: node(name) for name in ("a", "b", "c", "d")}
        self.nodes["c"] = node("c", EntityType.ORGANIZATION)
        self.edges = (
            edge("e-ab", "a", "b"),
            edge("e-bc", "b", "c", proposed=True),
            edge("e-ca", "c", "a"),
            edge("e-bd", "b", "d"),
        )

    async def load_graph_entities(
        self, *, entity_ids: frozenset[str], row_limit: int, deadline: float
    ) -> tuple[GraphNode, ...]:
        del deadline
        values = [
            item
            for name, item in self.nodes.items()
            if name in entity_ids and name not in self.omit
        ]
        values.sort(key=lambda item: item.entity_id, reverse=self.reverse)
        return tuple(values[:row_limit])

    async def load_graph_edges(
        self,
        *,
        frontier_entity_ids: frozenset[str],
        filters: ConnectionFilters,
        row_limit: int,
        deadline: float,
    ) -> tuple[GraphEdge, ...]:
        del deadline
        values = [
            item
            for item in self.edges
            if (
                item.subject_entity_id in frontier_entity_ids
                or item.object_entity_id in frontier_entity_ids
            )
            and item.status in filters.statuses
            and (not filters.predicates or item.predicate in filters.predicates)
        ]
        values.sort(key=lambda item: item.relationship_id, reverse=self.reverse)
        return tuple(values[:row_limit])


def request(**updates: object) -> FindConnectionsInput:
    values: dict[str, object] = {
        "seed_entity_ids": ("a",),
        "filters": ConnectionFilters(
            statuses=(RelationshipStatus.CONFIRMED, RelationshipStatus.PROPOSED)
        ),
        "max_depth": 3,
        "max_paths": 20,
        "max_nodes": 20,
        "max_edges": 20,
        "max_rows": 100,
    }
    values.update(updates)
    return FindConnectionsInput.model_validate(values)


@pytest.mark.asyncio
async def test_traversal_is_deterministic_cycle_free_and_preserves_proposed_status() -> None:
    limits = GraphLimits(max_depth=3, max_paths=20, max_nodes=20, max_edges=20, max_rows=100)
    deadline = asyncio.get_running_loop().time() + 5

    first = await FindConnections(reader=_GraphReader(), server_limits=limits).run(
        call_id="graph-1",
        request=request(),
        deadline=deadline,
    )
    reversed_result = await FindConnections(
        reader=_GraphReader(reverse=True), server_limits=limits
    ).run(
        call_id="graph-1",
        request=request(),
        deadline=deadline,
    )

    assert first == reversed_result
    assert all(len(path.node_ids) == len(set(path.node_ids)) for path in first.paths)
    assert any(item.status is RelationshipStatus.PROPOSED for item in first.edges)
    sourced_items = [item for item in first.evidence if item.kind != "finding"]
    path_findings = [item for item in first.evidence if item.kind == "finding"]
    assert all(item.content_hash == HASH for item in sourced_items)
    assert len(path_findings) == len(first.paths)
    assert any("a <-[KIN_OF; confirmed; id=e-ca]- c" in item.content for item in path_findings)
    assert any(item.evidentiary_status == "proposed" for item in path_findings)


@pytest.mark.asyncio
async def test_unresolvable_endpoint_removes_unsupported_paths_and_bounds_are_capped() -> None:
    limits = GraphLimits(max_depth=1, max_paths=1, max_nodes=2, max_edges=1, max_rows=4)

    outcome = await FindConnections(
        reader=_GraphReader(omit=frozenset({"b"})),
        server_limits=limits,
    ).run(
        call_id="graph-2",
        request=request(max_depth=100, max_paths=100, max_nodes=100, max_edges=100),
        deadline=asyncio.get_running_loop().time() + 5,
    )

    assert outcome.status == "no_support"
    assert outcome.paths == ()
    assert outcome.nodes == ()
    assert outcome.warnings == ("graph_limits_capped",)


@pytest.mark.asyncio
async def test_target_entity_type_keeps_matching_paths_and_their_intermediate_nodes() -> None:
    limits = GraphLimits(max_depth=3, max_paths=20, max_nodes=20, max_edges=20, max_rows=100)
    reader = _GraphReader()
    reader.edges = (
        edge("e-ab", "a", "b"),
        edge("e-bc", "b", "c", proposed=True),
    )

    outcome = await FindConnections(reader=reader, server_limits=limits).run(
        call_id="graph-target",
        request=request(
            filters=ConnectionFilters(
                statuses=(RelationshipStatus.CONFIRMED, RelationshipStatus.PROPOSED),
                target_entity_types=(EntityType.ORGANIZATION,),
            ),
            max_paths=1,
        ),
        deadline=asyncio.get_running_loop().time() + 5,
    )

    assert [path.node_ids for path in outcome.paths] == [("a", "b", "c")]
    assert {item.entity_id for item in outcome.nodes} == {"a", "b", "c"}


def test_tool_schema_explains_ontology_and_filter_semantics() -> None:
    schema = FindConnectionsInput.model_json_schema()
    filters = schema["$defs"]["ConnectionFilters"]["properties"]

    assert "PERSON->ORGANIZATION" in filters["predicates"]["description"]
    assert "traversal can follow an edge in either direction" in filters["predicates"][
        "description"
    ]
    assert "hypothesis" in filters["statuses"]["description"]
    assert "terminal non-seed node" in filters["target_entity_types"]["description"]
    seed_description = schema["properties"]["seed_entity_ids"]["description"]
    assert "normalized query_records fields" in seed_description
    assert "never entity names" in seed_description
    assert "search_evidence text" in seed_description


def test_removed_scope_field_is_rejected() -> None:
    removed_field = "case" + "_id"
    with pytest.raises(ValidationError):
        FindConnectionsInput.model_validate({**request().model_dump(), removed_field: "legacy"})
