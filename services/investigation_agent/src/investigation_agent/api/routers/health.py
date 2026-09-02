"""Dependency-free liveness and bounded dependency readiness endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse

from investigation_agent.api.dependencies import (
    ReadinessProbe,
    get_readiness_probe,
    get_readiness_timeout_s,
)

router = APIRouter(tags=["service"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Prove only that the process and event loop can answer."""

    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def ready(
    probe: Annotated[ReadinessProbe, Depends(get_readiness_probe)],
    timeout_s: Annotated[float, Depends(get_readiness_timeout_s)],
) -> HealthResponse | JSONResponse:
    try:
        async with asyncio.timeout(timeout_s):
            result = await probe()
    except Exception:
        result = None
    if result is None or not result.ready:
        body = HealthResponse(status="not_ready")
        return JSONResponse(body.model_dump(), status_code=503)
    return HealthResponse(status="ready")


__all__ = ["HealthResponse", "health", "ready", "router"]
