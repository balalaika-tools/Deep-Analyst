"""Versioned, bounded public failures for HTTP and streaming transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from observability import error_type_of, get_logger, mark_failed
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from investigation_agent.observability.events import LogEvent


class ErrorLogger(Protocol):
    def error(self, event: str, **fields: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class PublicFailure:
    code: str
    message: str
    status_code: int
    retryable: bool


class ProblemDetails(BaseModel):
    """Application-owned RFC problem shape; details never copy exception text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    type: str
    title: str
    status: int
    code: str
    detail: str
    retryable: bool


_FAILURES: dict[str, PublicFailure] = {
    "invalid_request": PublicFailure(
        "invalid_request", "The request or configuration is invalid.", 422, False
    ),
    "invalid_cursor": PublicFailure(
        "invalid_cursor", "The pagination cursor is invalid.", 422, False
    ),
    "resource_not_found": PublicFailure(
        "resource_not_found", "The requested resource is not available.", 404, False
    ),
    "method_not_allowed": PublicFailure(
        "method_not_allowed", "The request method is not supported for this resource.", 405, False
    ),
    "request_in_progress": PublicFailure(
        "request_in_progress", "This request is already in progress.", 409, True
    ),
    "thread_busy": PublicFailure(
        "thread_busy", "Another request is already running for this thread.", 409, True
    ),
    "idempotency_conflict": PublicFailure(
        "idempotency_conflict",
        "The request ID was already used with different content.",
        409,
        False,
    ),
    "conflict": PublicFailure(
        "conflict", "The request conflicts with the current resource state.", 409, False
    ),
    "thread_full": PublicFailure(
        "thread_full", "This thread cannot accept another turn.", 409, False
    ),
    "policy_rejected": PublicFailure(
        "policy_rejected", "The requested operation is not permitted by policy.", 400, False
    ),
    "no_support": PublicFailure(
        "no_support",
        "No supporting evidence was retrieved within the configured limits.",
        422,
        False,
    ),
    "no_retrieved_support": PublicFailure(
        "no_retrieved_support",
        "No supporting evidence was retrieved within the configured limits.",
        422,
        False,
    ),
    "retrieval_incomplete": PublicFailure(
        "retrieval_incomplete",
        "Evidence retrieval did not complete within the configured limits.",
        422,
        False,
    ),
    "transient_exhausted": PublicFailure(
        "transient_exhausted", "Temporary operation attempts were exhausted.", 503, True
    ),
    "budget_exhausted": PublicFailure(
        "budget_exhausted", "The investigation reached a configured execution limit.", 422, False
    ),
    "cancelled": PublicFailure("cancelled", "The investigation was cancelled.", 499, True),
    "dependency_unavailable": PublicFailure(
        "dependency_unavailable", "A required service is temporarily unavailable.", 503, True
    ),
    "guardrail_unavailable": PublicFailure(
        "guardrail_unavailable", "The safety check is temporarily unavailable.", 503, True
    ),
    "incompatible_state": PublicFailure(
        "incompatible_state", "The saved investigation state is not supported.", 409, False
    ),
    "grounding_failed": PublicFailure(
        "grounding_failed", "A grounded answer could not be produced.", 422, False
    ),
    "persistence_failed": PublicFailure(
        "persistence_failed", "The result could not be durably confirmed.", 503, True
    ),
    "delivery_failed": PublicFailure(
        "delivery_failed", "The response could not be delivered.", 503, True
    ),
    "internal": PublicFailure("internal", "The investigation could not be completed.", 500, False),
}


def public_failure_for_code(code: str | None) -> PublicFailure:
    """Resolve only allowlisted codes; unknown persisted strings remain private."""

    return _FAILURES.get(code or "", _FAILURES["internal"])


def public_failure(error: BaseException) -> PublicFailure:
    code = getattr(error, "code", None)
    return public_failure_for_code(code if isinstance(code, str) else None)


def problem_response(error: BaseException) -> JSONResponse:
    return problem_response_for_failure(public_failure(error))


def problem_response_for_failure(failure: PublicFailure) -> JSONResponse:
    problem = ProblemDetails(
        type=f"urn:investigation-agent:problem:{failure.code}",
        title=_title(failure.code),
        status=failure.status_code,
        code=failure.code,
        detail=failure.message,
        retryable=failure.retryable,
    )
    headers = {"Cache-Control": "no-store"}
    if failure.code in {"request_in_progress", "thread_busy"}:
        headers["Retry-After"] = "1"
    return JSONResponse(
        problem.model_dump(mode="json"),
        status_code=failure.status_code,
        media_type="application/problem+json",
        headers=headers,
    )


def install_problem_handlers(app: FastAPI, *, logger: ErrorLogger | None = None) -> None:
    """Install sanitizing handlers and own one log for failed HTTP operations."""

    log = logger or get_logger(__name__)

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del request, error
        return problem_response(_CodeFailure("invalid_request"))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        del request
        return problem_response_for_failure(_http_failure(error.status_code))

    @app.exception_handler(Exception)
    async def _safe_exception_handler(request: Request, error: Exception) -> JSONResponse:
        failure = public_failure(error)
        if failure.status_code >= 500:
            error_type = error_type_of(error)
            mark_failed(trace.get_current_span(), error_type)
            log.error(
                LogEvent.REQUEST_FAILED,
                exc_info=True,
                **{
                    "http.route": _route_template(request),
                    "http.request.method": request.method,
                    "http.response.status_code": failure.status_code,
                    "error.type": error_type,
                    "failure_code": failure.code,
                    "app.outcome": "error",
                },
            )
        return problem_response(error)


class _CodeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("public failure code")
        self.code = code


def _http_failure(status_code: int) -> PublicFailure:
    """Map framework-raised HTTP statuses onto public codes without copying their detail."""

    if status_code == 404:
        return _FAILURES["resource_not_found"]
    if status_code == 405:
        return _FAILURES["method_not_allowed"]
    if status_code >= 500:
        return _FAILURES["internal"]
    invalid = _FAILURES["invalid_request"]
    return PublicFailure(invalid.code, invalid.message, status_code, False)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def _title(code: str) -> str:
    if code in {
        "request_in_progress",
        "thread_busy",
        "idempotency_conflict",
        "conflict",
        "thread_full",
    }:
        return "Conflict"
    if code == "resource_not_found":
        return "Not Found"
    if code == "method_not_allowed":
        return "Method Not Allowed"
    if code in {
        "dependency_unavailable",
        "guardrail_unavailable",
        "transient_exhausted",
        "persistence_failed",
        "delivery_failed",
    }:
        return "Service Unavailable"
    if code == "internal":
        return "Internal Server Error"
    return "Invalid Request"


__all__ = [
    "ProblemDetails",
    "PublicFailure",
    "install_problem_handlers",
    "problem_response",
    "problem_response_for_failure",
    "public_failure",
    "public_failure_for_code",
]
