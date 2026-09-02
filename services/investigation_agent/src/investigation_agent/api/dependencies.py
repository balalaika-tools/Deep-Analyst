"""Already-composed runtime dependencies exposed to routes through application state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from fastapi import Request

from investigation_agent.application.delete_thread import DeleteThread
from investigation_agent.application.invoke_turn import InvokeTurn
from investigation_agent.application.read_history import ReadHistory


class ReadinessResult(Protocol):
    @property
    def ready(self) -> bool: ...


type ReadinessProbe = Callable[[], Awaitable[ReadinessResult]]


def get_invoke_turn(request: Request) -> InvokeTurn:
    return cast(InvokeTurn, _runtime_component(request, "invoke_turn"))


def get_read_history(request: Request) -> ReadHistory:
    return cast(ReadHistory, _runtime_component(request, "read_history"))


def get_delete_thread(request: Request) -> DeleteThread:
    return cast(DeleteThread, _runtime_component(request, "delete_thread"))


def get_readiness_probe(request: Request) -> ReadinessProbe:
    return cast(ReadinessProbe, _runtime_component(request, "readiness_probe"))


def get_sse_chunk_chars(request: Request) -> int:
    value = _runtime_component(request, "sse_chunk_chars")
    if not isinstance(value, int) or value < 1:
        raise RuntimeError("runtime SSE chunk policy is unavailable")
    return value


def get_sse_heartbeat_s(request: Request) -> float:
    value = _runtime_component(request, "sse_heartbeat_s")
    if not isinstance(value, int | float) or value <= 0:
        raise RuntimeError("runtime SSE heartbeat policy is unavailable")
    return float(value)


def get_sse_shutdown_grace_s(request: Request) -> float:
    """Seconds an in-flight stream may keep running after SIGTERM before it is force-closed.

    Runtimes composed without a shutdown budget get sse-starlette's default of no grace.
    """
    value = getattr(_runtime(request), "shutdown_timeout_s", 0.0)
    if not isinstance(value, int | float) or value < 0:
        raise RuntimeError("runtime shutdown grace policy is invalid")
    return float(value)


def get_readiness_timeout_s(request: Request) -> float:
    value = _runtime_component(request, "readiness_timeout_s")
    if not isinstance(value, int | float) or value <= 0:
        raise RuntimeError("runtime readiness timeout policy is unavailable")
    return float(value)


def _runtime_component(request: Request, name: str) -> object:
    runtime = _runtime(request)
    if not hasattr(runtime, name):
        raise RuntimeError("application runtime is not initialized")
    return getattr(runtime, name)


def _runtime(request: Request) -> object:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("application runtime is not initialized")
    return runtime


__all__ = [
    "ReadinessProbe",
    "ReadinessResult",
    "get_delete_thread",
    "get_invoke_turn",
    "get_read_history",
    "get_readiness_probe",
    "get_readiness_timeout_s",
    "get_sse_chunk_chars",
    "get_sse_heartbeat_s",
    "get_sse_shutdown_grace_s",
]
