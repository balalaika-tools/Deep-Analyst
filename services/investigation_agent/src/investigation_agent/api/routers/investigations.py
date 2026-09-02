"""Unauthenticated, case-bound investigation invocation transport."""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sse_starlette import EventSourceResponse

from investigation_agent.api.dependencies import (
    get_invoke_turn,
    get_sse_chunk_chars,
    get_sse_heartbeat_s,
)
from investigation_agent.api.sse import heartbeat, stream_prepared_turn
from investigation_agent.application.invoke_turn import InvokeRequest, InvokeTurn

router = APIRouter(prefix="/v1/agent", tags=["investigations"])


@router.post("/invoke", response_class=EventSourceResponse)
async def invoke(
    body: InvokeRequest,
    request: Request,
    service: Annotated[InvokeTurn, Depends(get_invoke_turn)],
    chunk_chars: Annotated[int, Depends(get_sse_chunk_chars)],
    heartbeat_s: Annotated[float, Depends(get_sse_heartbeat_s)],
) -> EventSourceResponse:
    prepared = await service.prepare(body)
    return EventSourceResponse(
        stream_prepared_turn(
            prepared, chunk_chars=chunk_chars, disconnected=request.is_disconnected
        ),
        ping=max(1, math.ceil(heartbeat_s)),
        ping_message_factory=heartbeat,
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


__all__ = ["invoke", "router"]
