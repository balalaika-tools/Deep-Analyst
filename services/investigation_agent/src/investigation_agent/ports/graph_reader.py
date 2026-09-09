"""Application-facing graph evidence reader contract."""

from __future__ import annotations

from typing import Protocol

from investigation_agent.domain.connections import ConnectionFilters, GraphEdge, GraphNode


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


__all__ = ["GraphReader"]
