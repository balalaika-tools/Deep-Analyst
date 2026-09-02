from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from investigation_agent.api.dependencies import get_sse_shutdown_grace_s
from investigation_agent.api.routers import investigations
from starlette.responses import Response


class RecordingResponse(Response):
    kwargs: dict[str, Any] = {}

    def __init__(self, content: Any, **kwargs: Any) -> None:
        RecordingResponse.kwargs = kwargs
        super().__init__(content=b"", media_type="text/event-stream")


class PreparedStub:
    telemetry = None

    async def close(self) -> None:
        return None


class InvokeStub:
    async def prepare(self, body: Any) -> PreparedStub:
        return PreparedStub()


def _app(runtime: SimpleNamespace) -> FastAPI:
    app = FastAPI()
    app.include_router(investigations.router)
    app.state.runtime = runtime
    return app


def _body() -> dict[str, str]:
    return {
        "request_id": "request-1",
        "thread_id": "thread-1",
        "message": "Investigate",
    }


def test_stream_gets_the_shutdown_budget_as_its_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(investigations, "EventSourceResponse", RecordingResponse)
    runtime = SimpleNamespace(
        invoke_turn=InvokeStub(), sse_chunk_chars=32, sse_heartbeat_s=1.0, shutdown_timeout_s=7.5
    )

    response = TestClient(_app(runtime)).post("/v1/agent/invoke", json=_body())

    assert response.status_code == 200
    assert RecordingResponse.kwargs["shutdown_grace_period"] == 7.5


def test_runtime_without_a_shutdown_budget_gets_no_grace() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=SimpleNamespace())))

    assert get_sse_shutdown_grace_s(request) == 0.0  # type: ignore[arg-type]
