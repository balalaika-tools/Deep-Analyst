"""LangGraph checkpointer construction kept on the writer-only pool."""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from investigation_agent.adapters.postgres.pools import AgentPool


def create_checkpointer(writer_pool: AgentPool) -> AsyncPostgresSaver:
    """Construct the runtime saver without performing request-time setup or DDL."""

    return AsyncPostgresSaver(writer_pool)


__all__ = ["create_checkpointer"]
