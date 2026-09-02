"""Deterministic, bounded graph traversal implementation."""

from __future__ import annotations

import hashlib
import json

from investigation_agent.genai.investigation.connections.schemas import (
    ConnectionFilters,
    ConnectionPath,
    FindConnectionsInput,
    FindConnectionsOutcome,
    GraphConsumption,
    GraphEdge,
    GraphEvidence,
    GraphLimits,
    GraphNode,
    GraphReader,
)


class FindConnections:
    def __init__(self, *, reader: GraphReader, server_limits: GraphLimits) -> None:
        self._reader = reader
        self._server_limits = server_limits

    async def run(
        self,
        *,
        call_id: str,
        case_id: str,
        request: FindConnectionsInput,
        deadline: float,
    ) -> FindConnectionsOutcome:
        limits = _effective_limits(request, self._server_limits)
        fingerprint = _digest(request.model_dump(mode="json"))
        warnings = _cap_warnings(request, limits)
        rows_remaining = limits.max_rows
        physical_attempts = 1
        seed_nodes = await self._reader.load_graph_entities(
            case_id=case_id,
            entity_ids=frozenset(request.seed_entity_ids),
            row_limit=min(rows_remaining, limits.max_nodes),
            deadline=deadline,
        )
        seed_nodes = tuple(sorted(seed_nodes, key=lambda item: item.entity_id))[
            : min(rows_remaining, limits.max_nodes)
        ]
        rows_remaining -= len(seed_nodes)
        nodes = {
            item.entity_id: item for item in seed_nodes if _node_supported(item, case_id=case_id)
        }
        active: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
            ((seed,), ()) for seed in sorted(nodes)
        ]
        paths: list[ConnectionPath] = []
        edges: dict[str, GraphEdge] = {}

        for _depth in range(limits.max_depth):
            if _at_limit(active, paths, edges, rows_remaining=rows_remaining, limits=limits):
                break
            hop_edges = await self._load_edges(
                case_id=case_id,
                active=active,
                filters=request.filters,
                row_limit=min(rows_remaining, limits.max_edges - len(edges)),
                deadline=deadline,
            )
            physical_attempts += 1
            rows_remaining -= len(hop_edges)
            loaded = await self._load_missing_nodes(
                case_id=case_id,
                hop_edges=hop_edges,
                known_nodes=nodes,
                row_limit=min(rows_remaining, limits.max_nodes - len(nodes)),
                deadline=deadline,
            )
            if loaded:
                physical_attempts += 1
                rows_remaining -= len(loaded)
                nodes.update(
                    (item.entity_id, item)
                    for item in loaded
                    if _node_supported(item, case_id=case_id)
                )
            active = _extend_paths(
                active,
                hop_edges,
                nodes=nodes,
                edges=edges,
                paths=paths,
                case_id=case_id,
                limits=limits,
            )

        stable_paths, supported_nodes, supported_edges = _supported_graph(paths, nodes, edges)
        if rows_remaining <= 0:
            warnings.append("graph_row_limit_reached")
        evidence = _graph_evidence(supported_nodes, supported_edges)
        return FindConnectionsOutcome(
            call_id=call_id,
            intent_fingerprint=fingerprint,
            status="connections_found" if stable_paths else "no_support",
            physical_attempts=physical_attempts,
            effective_limits=limits,
            paths=stable_paths,
            nodes=supported_nodes,
            edges=supported_edges,
            evidence=evidence,
            warnings=tuple(dict.fromkeys(warnings)),
            consumption=GraphConsumption(
                rows=limits.max_rows - rows_remaining,
                bytes=sum(len(item.content.encode("utf-8")) for item in evidence),
                physical_attempts=physical_attempts,
                paths=len(stable_paths),
            ),
        )

    async def _load_edges(
        self,
        *,
        case_id: str,
        active: list[tuple[tuple[str, ...], tuple[str, ...]]],
        filters: ConnectionFilters,
        row_limit: int,
        deadline: float,
    ) -> tuple[GraphEdge, ...]:
        loaded = await self._reader.load_graph_edges(
            case_id=case_id,
            frontier_entity_ids=frozenset(node_ids[-1] for node_ids, _edge_ids in active),
            filters=filters,
            row_limit=row_limit,
            deadline=deadline,
        )
        return tuple(
            edge
            for edge in sorted(loaded, key=lambda item: item.relationship_id)
            if _edge_supported(edge, case_id=case_id, filters=filters)
        )[:row_limit]

    async def _load_missing_nodes(
        self,
        *,
        case_id: str,
        hop_edges: tuple[GraphEdge, ...],
        known_nodes: dict[str, GraphNode],
        row_limit: int,
        deadline: float,
    ) -> tuple[GraphNode, ...]:
        endpoint_ids = frozenset(
            endpoint
            for edge in hop_edges
            for endpoint in (edge.subject_entity_id, edge.object_entity_id)
            if endpoint not in known_nodes
        )
        if not endpoint_ids or row_limit <= 0:
            return ()
        loaded = await self._reader.load_graph_entities(
            case_id=case_id,
            entity_ids=endpoint_ids,
            row_limit=row_limit,
            deadline=deadline,
        )
        return tuple(sorted(loaded, key=lambda item: item.entity_id))[:row_limit]


def _at_limit(
    active: list[tuple[tuple[str, ...], tuple[str, ...]]],
    paths: list[ConnectionPath],
    edges: dict[str, GraphEdge],
    *,
    rows_remaining: int,
    limits: GraphLimits,
) -> bool:
    return (
        not active
        or rows_remaining <= 0
        or len(paths) >= limits.max_paths
        or len(edges) >= limits.max_edges
    )


def _extend_paths(
    active: list[tuple[tuple[str, ...], tuple[str, ...]]],
    hop_edges: tuple[GraphEdge, ...],
    *,
    nodes: dict[str, GraphNode],
    edges: dict[str, GraphEdge],
    paths: list[ConnectionPath],
    case_id: str,
    limits: GraphLimits,
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    next_active: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for node_ids, edge_ids in sorted(active):
        for edge in hop_edges:
            neighbor = _neighbor(edge, node_ids[-1])
            if neighbor is None or neighbor in node_ids or neighbor not in nodes:
                continue
            if edge.case_id != case_id or len(edges) >= limits.max_edges:
                continue
            candidate = ((*node_ids, neighbor), (*edge_ids, edge.relationship_id))
            edges[edge.relationship_id] = edge
            paths.append(ConnectionPath(node_ids=candidate[0], edge_ids=candidate[1]))
            next_active.append(candidate)
            if len(paths) >= limits.max_paths:
                break
        if len(paths) >= limits.max_paths:
            break
    return sorted(set(next_active))


def _supported_graph(
    paths: list[ConnectionPath], nodes: dict[str, GraphNode], edges: dict[str, GraphEdge]
) -> tuple[tuple[ConnectionPath, ...], tuple[GraphNode, ...], tuple[GraphEdge, ...]]:
    node_ids = {node_id for path in paths for node_id in path.node_ids}
    edge_ids = {edge_id for path in paths for edge_id in path.edge_ids}
    return (
        tuple(sorted(paths, key=lambda path: (path.edge_ids, path.node_ids))),
        tuple(nodes[node_id] for node_id in sorted(node_ids)),
        tuple(edges[edge_id] for edge_id in sorted(edge_ids)),
    )


def _effective_limits(request: FindConnectionsInput, server: GraphLimits) -> GraphLimits:
    return GraphLimits(
        max_depth=min(request.max_depth, server.max_depth),
        max_paths=min(request.max_paths, server.max_paths),
        max_nodes=min(request.max_nodes, server.max_nodes),
        max_edges=min(request.max_edges, server.max_edges),
        max_rows=min(request.max_rows, server.max_rows),
    )


def _cap_warnings(request: FindConnectionsInput, effective: GraphLimits) -> list[str]:
    supplied = (
        request.max_depth,
        request.max_paths,
        request.max_nodes,
        request.max_edges,
        request.max_rows,
    )
    applied = (
        effective.max_depth,
        effective.max_paths,
        effective.max_nodes,
        effective.max_edges,
        effective.max_rows,
    )
    return ["graph_limits_capped"] if supplied != applied else []


def _neighbor(edge: GraphEdge, entity_id: str) -> str | None:
    if edge.subject_entity_id == entity_id:
        return edge.object_entity_id
    return edge.subject_entity_id if edge.object_entity_id == entity_id else None


def _node_supported(node: GraphNode, *, case_id: str) -> bool:
    return node.case_id == case_id and all(source.case_id == case_id for source in node.sources)


def _edge_supported(edge: GraphEdge, *, case_id: str, filters: ConnectionFilters) -> bool:
    if edge.case_id != case_id or any(source.case_id != case_id for source in edge.sources):
        return False
    if edge.status not in filters.statuses:
        return False
    if filters.predicates and edge.predicate not in filters.predicates:
        return False
    if filters.occurred_from is not None and (
        edge.occurred_at is None or edge.occurred_at < filters.occurred_from
    ):
        return False
    return not (
        filters.occurred_to is not None
        and (edge.occurred_at is None or edge.occurred_at > filters.occurred_to)
    )


def _graph_evidence(
    nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]
) -> tuple[GraphEvidence, ...]:
    evidence = [
        GraphEvidence(
            evidence_id=f"entity:{node.entity_id}:{source.source_ref.record_id}",
            case_id=node.case_id,
            content_hash=source.content_hash,
            source_refs=(source.source_ref,),
            kind="entity",
            content=f"{node.entity_type.value}: {node.label}",
            evidentiary_status="verified",
        )
        for node in nodes
        for source in node.sources
    ]
    evidence.extend(
        GraphEvidence(
            evidence_id=f"relationship:{edge.relationship_id}:{source.source_ref.record_id}",
            case_id=edge.case_id,
            content_hash=source.content_hash,
            source_refs=(source.source_ref,),
            kind="relationship",
            content=f"{edge.subject_entity_id} {edge.predicate.value} {edge.object_entity_id}",
            evidentiary_status=edge.status.value,
        )
        for edge in edges
        for source in edge.sources
    )
    return tuple(sorted(evidence, key=lambda item: item.evidence_id))


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = ["FindConnections"]
