"""LangGraph checkpointer construction kept on the writer-only pool."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from investigation_agent.db.pools import AgentPool
from investigation_agent.domain.investigation_state import (
    IncompatibleStateError,
    InvestigationState,
    parse_state,
)
from investigation_agent.ports.checkpoints import (
    CHECKPOINT_APP,
    CheckpointStoreError,
    StoredThreadState,
)


def create_checkpointer(writer_pool: AgentPool) -> AsyncPostgresSaver:
    """Construct the runtime saver without performing request-time setup or DDL."""

    return AsyncPostgresSaver(writer_pool)


class PostgresCheckpointStore:
    """Application-facing checkpoint operations with provider errors contained."""

    def __init__(self, saver: AsyncPostgresSaver) -> None:
        self._saver = saver

    def scan_threads(self, *, limit: int) -> AsyncIterator[StoredThreadState]:
        async def records() -> AsyncIterator[StoredThreadState]:
            try:
                async for record in self._saver.alist(
                    None,
                    filter={"app": CHECKPOINT_APP},
                    limit=limit,
                ):
                    state = _state_from_checkpoint(record.checkpoint)
                    thread_id = _metadata_string(record.metadata, "public_thread_id")
                    if state is None or state.turn is None or thread_id is None:
                        continue
                    yield StoredThreadState(
                        thread_id=thread_id,
                        checkpoint_at=_checkpoint_timestamp(record.checkpoint, state=state),
                        state=state,
                    )
            except Exception as error:
                raise CheckpointStoreError from error

        return records()

    async def adelete_thread(self, thread_id: str) -> None:
        try:
            await self._saver.adelete_thread(thread_id)
        except Exception as error:
            raise CheckpointStoreError from error


def _state_from_checkpoint(checkpoint: Mapping[str, object]) -> InvestigationState | None:
    values = checkpoint.get("channel_values")
    if not isinstance(values, Mapping) or not values:
        return None
    try:
        return parse_state(values)
    except (ValueError, TypeError, IncompatibleStateError):
        return None


def _metadata_string(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _checkpoint_timestamp(
    checkpoint: Mapping[str, object], *, state: InvestigationState
) -> datetime:
    raw = checkpoint.get("ts")
    if isinstance(raw, str):
        try:
            timestamp = datetime.fromisoformat(raw)
            if timestamp.tzinfo is not None:
                return timestamp
        except ValueError:
            pass
    turn = state.turn
    return max(
        (message.created_at for message in state.history.messages),
        default=turn.opened_at if turn is not None else datetime.min.replace(tzinfo=UTC),
    )


__all__ = ["PostgresCheckpointStore", "create_checkpointer"]
