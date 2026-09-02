"""Checkpoint-backed conversation history and thread deletion endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from investigation_agent.api.dependencies import get_delete_thread, get_read_history
from investigation_agent.application.delete_thread import DeleteThread
from investigation_agent.application.read_history import MessagePage, ReadHistory, ThreadPage

router = APIRouter(prefix="/v1/threads", tags=["threads"])
_THREAD_ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]


@router.get("", response_model=ThreadPage)
async def list_threads(
    reader: Annotated[ReadHistory, Depends(get_read_history)],
    cursor: Annotated[str | None, Query(max_length=2_048)] = None,
    page_size: Annotated[int | None, Query(ge=1)] = None,
) -> ThreadPage:
    return await reader.list_threads(cursor=cursor, page_size=page_size)


@router.get("/{thread_id}/messages", response_model=MessagePage)
async def read_messages(
    thread_id: _THREAD_ID,
    reader: Annotated[ReadHistory, Depends(get_read_history)],
    cursor: Annotated[str | None, Query(max_length=2_048)] = None,
    page_size: Annotated[int | None, Query(ge=1)] = None,
) -> MessagePage:
    return await reader.read_messages(thread_id=thread_id, cursor=cursor, page_size=page_size)


@router.delete("/{thread_id}", status_code=204, response_class=Response)
async def delete_thread(
    thread_id: _THREAD_ID,
    deleter: Annotated[DeleteThread, Depends(get_delete_thread)],
) -> Response:
    await deleter.delete(thread_id)
    return Response(status_code=204)


__all__ = ["delete_thread", "list_threads", "read_messages", "router"]
