from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from investigation_agent.api.dependencies import ReadinessResult
from investigation_agent.api.problems import install_problem_handlers
from investigation_agent.api.routers.health import router as health_router
from investigation_agent.api.routers.investigations import router as investigations_router
from investigation_agent.api.routers.threads import router as threads_router
from investigation_agent.application.invoke_turn import (
    RequestInProgress,
    ThreadBusy,
    ThreadNotFound,
)
from investigation_agent.application.read_history import (
    MessageItem,
    MessagePage,
    ThreadPage,
    ThreadSummary,
)
from investigation_agent.domain.history import HistoryRole, TurnStatus


@dataclass(frozen=True)
class Ready:
    ready: bool


class NeverInvoke:
    calls = 0

    async def prepare(self, body: object) -> None:
        del body
        self.calls += 1
        raise AssertionError("invalid requests must not enter application execution")


@dataclass
class RecordingErrorLogger:
    records: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def error(self, event: str, **fields: Any) -> None:
        self.records.append((event, fields))


@dataclass
class FixedHistory:
    thread_ids: list[str] = field(default_factory=list)

    async def list_threads(
        self, *, cursor: str | None = None, page_size: int | None = None
    ) -> ThreadPage:
        del cursor, page_size
        return ThreadPage(
            items=(
                ThreadSummary(
                    thread_id="thread-1",
                    turn_id="turn-1",
                    status=TurnStatus.COMPLETED,
                    created_at=datetime(2026, 4, 5, tzinfo=UTC),
                ),
            )
        )

    async def read_messages(
        self, *, thread_id: str, cursor: str | None = None, page_size: int | None = None
    ) -> MessagePage:
        del cursor, page_size
        self.thread_ids.append(thread_id)
        if thread_id != "thread-1":
            raise ThreadNotFound
        return MessagePage(
            items=(
                MessageItem(
                    message_id="message-1",
                    sequence=1,
                    turn_id="turn-1",
                    request_id="request-1",
                    role=HistoryRole.USER,
                    content="Exact account 77",
                    citations=(),
                    turn_status=TurnStatus.COMPLETED,
                    created_at=datetime(2026, 4, 5, tzinfo=UTC),
                ),
            )
        )


@dataclass
class FixedDeleter:
    deleted: list[str] = field(default_factory=list)

    async def delete(self, thread_id: str) -> None:
        if thread_id == "busy":
            raise ThreadBusy
        if thread_id != "thread-1":
            raise ThreadNotFound
        self.deleted.append(thread_id)


def _app(
    *,
    readiness_probe: Callable[[], Awaitable[ReadinessResult]],
    readiness_timeout_s: float = 0.05,
    invoke: object | None = None,
    error_logger: RecordingErrorLogger | None = None,
) -> FastAPI:
    app = FastAPI()
    install_problem_handlers(app, logger=error_logger)
    app.include_router(health_router)
    app.include_router(investigations_router)
    app.include_router(threads_router)
    app.state.runtime = SimpleNamespace(
        readiness_probe=readiness_probe,
        readiness_timeout_s=readiness_timeout_s,
        invoke_turn=invoke or NeverInvoke(),
        read_history=FixedHistory(),
        delete_thread=FixedDeleter(),
        sse_chunk_chars=32,
        sse_heartbeat_s=1.0,
    )

    @app.get("/busy")
    async def busy() -> None:
        raise ThreadBusy("private request and database detail")

    @app.get("/in-progress")
    async def in_progress() -> None:
        raise RequestInProgress("private request detail")

    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError("secret=abc SELECT * FROM private evidence")

    return app


async def _available() -> Ready:
    return Ready(True)


def test_health_never_probes_dependencies_and_ready_reflects_outage() -> None:
    calls = 0

    async def unavailable() -> Ready:
        nonlocal calls
        calls += 1
        return Ready(False)

    with TestClient(_app(readiness_probe=unavailable)) as client:
        health = client.get("/health")
        assert health.status_code == 200 and health.json() == {"status": "ok"} and calls == 0
        ready = client.get("/ready")
        assert ready.status_code == 503 and ready.json() == {"status": "not_ready"} and calls == 1


def test_ready_enforces_its_own_deadline() -> None:
    async def never_finishes() -> Ready:
        await asyncio.Event().wait()
        return Ready(True)

    with TestClient(_app(readiness_probe=never_finishes, readiness_timeout_s=0.001)) as client:
        response = client.get("/ready")

    assert response.status_code == 503 and response.json() == {"status": "not_ready"}


def test_versioned_problem_details_are_sanitized_and_busy_has_retry_guidance() -> None:
    error_logger = RecordingErrorLogger()
    with TestClient(
        _app(readiness_probe=_available, error_logger=error_logger),
        raise_server_exceptions=False,
    ) as client:
        busy = client.get("/busy")
        in_progress = client.get("/in-progress")
        broken = client.get("/broken")

    assert busy.status_code == 409
    assert busy.headers["content-type"].startswith("application/problem+json")
    assert busy.headers["retry-after"] == "1"
    assert busy.json() == {
        "schema_version": 1,
        "type": "urn:investigation-agent:problem:thread_busy",
        "title": "Conflict",
        "status": 409,
        "code": "thread_busy",
        "detail": "Another request is already running for this thread.",
        "retryable": True,
    }
    assert in_progress.status_code == 409 and in_progress.headers["retry-after"] == "1"
    assert in_progress.json()["code"] == "request_in_progress"
    assert broken.status_code == 500 and broken.json()["code"] == "internal"
    assert "secret" not in broken.text.lower() and "select" not in broken.text.lower()
    assert error_logger.records == [
        (
            "investigation.request_failed",
            {
                "exc_info": True,
                "http.route": "/broken",
                "http.request.method": "GET",
                "http.response.status_code": 500,
                "error.type": "RuntimeError",
                "failure_code": "internal",
                "app.outcome": "error",
            },
        )
    ]


def test_removed_scope_field_is_rejected_before_execution() -> None:
    app = _app(readiness_probe=_available)
    removed_field = "case" + "_id"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/agent/invoke",
            json={
                "request_id": "request-1",
                "thread_id": "thread-1",
                removed_field: "legacy",
                "message": "Investigate",
            },
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "invalid_request"
    assert app.state.runtime.invoke_turn.calls == 0


def test_history_routes_return_only_public_dtos_without_any_identity() -> None:
    app = _app(readiness_probe=_available)
    with TestClient(app, raise_server_exceptions=False) as client:
        threads = client.get(
            "/v1/threads", headers={"X-Owner-Id": "attacker", "Authorization": "Bearer x"}
        )
        messages = client.get("/v1/threads/thread-1/messages")
        missing = client.get("/v1/threads/thread-9/messages")

    assert threads.status_code == 200 and threads.json()["items"][0]["thread_id"] == "thread-1"
    assert messages.status_code == 200
    assert messages.json()["items"][0] == {
        "message_id": "message-1",
        "sequence": 1,
        "turn_id": "turn-1",
        "request_id": "request-1",
        "role": "user",
        "content": "Exact account 77",
        "citations": [],
        "turn_status": "completed",
        "created_at": "2026-04-05T00:00:00Z",
    }
    assert missing.status_code == 404 and missing.json()["code"] == "resource_not_found"
    payload = f"{threads.text}{messages.text}".lower()
    removed_field = "case" + "_id"
    assert removed_field not in payload
    assert all(
        private not in payload for private in ("checkpoint", "sql", "tool", "diagnostic", "owner")
    )


def test_thread_deletion_returns_204_404_and_409() -> None:
    app = _app(readiness_probe=_available)
    with TestClient(app, raise_server_exceptions=False) as client:
        deleted = client.delete("/v1/threads/thread-1")
        missing = client.delete("/v1/threads/thread-9")
        busy = client.delete("/v1/threads/busy")

    assert deleted.status_code == 204 and deleted.content == b""
    assert missing.status_code == 404 and missing.json()["code"] == "resource_not_found"
    assert (
        busy.status_code == 409
        and busy.json()["code"] == "thread_busy"
        and busy.headers["retry-after"] == "1"
    )
    assert app.state.runtime.delete_thread.deleted == ["thread-1"]
