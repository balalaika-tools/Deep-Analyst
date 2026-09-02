"""Shared structlog configuration: JSON, trace correlation, redaction, one delivery owner.

Importing this module is inert. A service calls `configure_logging` from its
composition root after `configure_observability`, passing the logger provider when
OTLP delivery is selected.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from time import time_ns
from typing import Any, TextIO

import structlog
from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.context import get_current
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.trace import format_span_id, format_trace_id

from observability.config import LoggingConfig
from observability.spans import WORKFLOW_RUN_ID

type EventDict = MutableMapping[str, Any]

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|credential|"
    r"access[_-]?key|session[_-]?key|cookie)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]+=*"),
    # Credentials embedded in a URL such as postgresql://user:secret@host.
    re.compile(r"(?<=://)[^/:@\s]+:[^@\s]+(?=@)"),
)
_SEVERITY = {
    "debug": SeverityNumber.DEBUG,
    "info": SeverityNumber.INFO,
    "warning": SeverityNumber.WARN,
    "error": SeverityNumber.ERROR,
    "critical": SeverityNumber.FATAL,
}


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _SENSITIVE_VALUE_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value
    if isinstance(value, Mapping):
        return {key: _redact_item(str(key), item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(item) for item in value]
    return value


def _redact_item(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return REDACTED
    return _redact_value(value)


def redact_credentials(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Mask credential-looking keys and values, including inside nested fields."""
    for key in list(event_dict):
        if key == "exc_info":
            continue
        event_dict[key] = _redact_item(key, event_dict[key])
    return event_dict


def add_trace_context(_: Any, __: str, event_dict: EventDict) -> EventDict:
    span = trace.get_current_span()
    context = span.get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format_trace_id(context.trace_id)
        event_dict["span_id"] = format_span_id(context.span_id)
    attributes = getattr(span, "attributes", None)
    if isinstance(attributes, Mapping) and isinstance(attributes.get(WORKFLOW_RUN_ID), str):
        event_dict.setdefault("workflow_run_id", attributes[WORKFLOW_RUN_ID])
    return event_dict


def _exception_type(exc_info: Any) -> str | None:
    if exc_info is True:
        exc_info = sys.exc_info()
    if isinstance(exc_info, BaseException):
        return type(exc_info).__name__
    if isinstance(exc_info, tuple) and len(exc_info) == 3 and exc_info[0] is not None:
        return str(exc_info[0].__name__)
    return None


class ExceptionDetailProcessor:
    """Full traceback in local/dev/staging; bounded indicators only in production."""

    def __init__(self, detail: str) -> None:
        self._full = detail == "full"
        self._format = structlog.processors.format_exc_info

    def __call__(self, logger: Any, method: str, event_dict: EventDict) -> EventDict:
        safe_stacktrace = event_dict.pop("exception.stacktrace", None)
        if safe_stacktrace and self._full:
            event_dict["exception"] = safe_stacktrace
        exc_info = event_dict.get("exc_info")
        if not exc_info:
            return event_dict
        exception_type = _exception_type(exc_info)
        if exception_type and "error.type" not in event_dict:
            event_dict["error.type"] = exception_type
        if self._full:
            return self._format(logger, method, event_dict)
        event_dict.pop("exc_info", None)
        return event_dict


class OtelLogDelivery:
    """Emit every structlog record as an OpenTelemetry log record and drop it locally."""

    def __init__(self, provider: LoggerProvider, scope: str) -> None:
        self._logger = provider.get_logger(scope)

    def __call__(self, _: Any, method: str, event_dict: EventDict) -> EventDict:
        level = str(event_dict.get("level", method)).lower()
        # Loki/Grafana renders the OTLP body by default. Keep native indexed
        # attributes as well, but make the visible record structured JSON too.
        body = json.dumps(event_dict, default=str, separators=(",", ":"), sort_keys=True)
        attributes = {
            key: value if isinstance(value, str | bool | int | float) else str(value)
            for key, value in event_dict.items()
            if key not in {"event", "level", "timestamp", "exception", "trace_id", "span_id"}
        }
        self._logger.emit(
            timestamp=time_ns(),
            body=body,
            severity_number=_SEVERITY.get(level, SeverityNumber.INFO),
            severity_text=level.upper(),
            attributes=attributes,
            context=get_current(),
        )
        raise structlog.DropEvent


def _shared_processors(config: LoggingConfig) -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _ServiceName(config.service_name),
        add_trace_context,
        ExceptionDetailProcessor(config.exception_detail),
        redact_credentials,
    ]


class _ServiceName:
    def __init__(self, service_name: str) -> None:
        self._service_name = service_name

    def __call__(self, _: Any, __: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service.name", self._service_name)
        return event_dict


def _stdlib_to_structlog(stream: TextIO, shared: list[Any]) -> None:
    """Route third-party stdlib records (for example exporter failures) through the same JSON."""
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.WARNING)


def configure_logging(
    config: LoggingConfig,
    *,
    logger_provider: LoggerProvider | None = None,
    stream: TextIO | None = None,
) -> None:
    """Configure structlog for one delivery owner.

    `stdout` renders JSON lines to the stream. `otlp` emits application events through
    the given logger provider and drops the local copy so a stdout collector cannot
    ingest a second one; stdlib records from libraries still render to the stream
    because they must never recurse into the exporter that emitted them.
    """
    output = stream or sys.stdout
    shared = _shared_processors(config)
    final: list[Any]
    if config.delivery == "otlp":
        if logger_provider is None:
            raise ValueError("otlp log delivery requires a logger provider")
        final = [OtelLogDelivery(logger_provider, config.service_name)]
    else:
        final = [structlog.processors.JSONRenderer()]
    level = logging.getLevelNamesMapping()[config.level.upper()]
    structlog.configure(
        processors=[*shared, *final],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(output),
        cache_logger_on_first_use=False,
    )
    _stdlib_to_structlog(output, shared)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
