import io
import json
import logging

import pytest
import structlog
from observability import (
    LoggingConfig,
    configure_logging,
    get_logger,
    start_genai_span,
    start_span,
    workflow_run,
)
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import format_span_id, format_trace_id


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


@pytest.fixture(autouse=True)
def reset_structlog() -> None:
    structlog.reset_defaults()
    logging.getLogger().handlers = []


def test_log_inside_span_carries_trace_ids_and_redacts_secrets(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = span_exporter
    stream = io.StringIO()
    configure_logging(LoggingConfig(service_name="svc"), stream=stream)
    log = get_logger("test")

    with start_span("run", tracer=provider.get_tracer("t")) as span:
        trace_id = format_trace_id(span.get_span_context().trace_id)
        log.info(
            "ingestion.run_started",
            password="hunter2",
            database_url="postgresql://app:s3cret@db:5432/app",
            nested={"aws_secret_access_key": "abc", "ok": "AKIAIOSFODNN7EXAMPLE"},
            header="Bearer eyJhbGciOi",
        )

    (record,) = _records(stream)
    assert record["event"] == "ingestion.run_started"
    assert record["trace_id"] == trace_id
    assert len(str(record["span_id"])) == 16
    assert record["service.name"] == "svc"
    assert record["password"] == "[REDACTED]"
    assert record["database_url"] == "postgresql://[REDACTED]@db:5432/app"
    assert record["nested"] == {"aws_secret_access_key": "[REDACTED]", "ok": "[REDACTED]"}
    assert record["header"] == "[REDACTED]"
    assert "hunter2" not in stream.getvalue() and "s3cret" not in stream.getvalue()


def test_workflow_run_id_is_projected_from_the_current_root_span(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, _ = span_exporter
    stream = io.StringIO()
    configure_logging(LoggingConfig(service_name="svc"), stream=stream)

    with start_genai_span("run ingestion", tracer=provider.get_tracer("t")):
        with workflow_run("run-42"):
            get_logger("test").info("ingestion.run_started")

    (record,) = _records(stream)
    assert record["workflow_run_id"] == "run-42"


def test_exception_detail_policy_is_environment_scoped() -> None:
    full, safe = io.StringIO(), io.StringIO()

    configure_logging(LoggingConfig(service_name="svc", exception_detail="full"), stream=full)
    try:
        raise ValueError("secret detail")
    except ValueError:
        get_logger("t").error("ingestion.run_failed", exc_info=True)

    configure_logging(LoggingConfig(service_name="svc", exception_detail="safe"), stream=safe)
    try:
        raise ValueError("secret detail")
    except ValueError:
        get_logger("t").error("ingestion.run_failed", exc_info=True)

    (full_record,) = _records(full)
    (safe_record,) = _records(safe)
    assert full_record["error.type"] == "ValueError"
    assert "secret detail" in str(full_record["exception"])
    assert safe_record["error.type"] == "ValueError"
    assert "exception" not in safe_record and "secret detail" not in safe.getvalue()


def test_content_safe_stacktrace_is_visible_only_with_full_detail() -> None:
    full, safe = io.StringIO(), io.StringIO()
    stacktrace = 'Traceback (most recent call last):\n  File "agent.py", line 42, in run\n'

    configure_logging(LoggingConfig(service_name="svc", exception_detail="full"), stream=full)
    get_logger("t").error("investigation.attempt_failed", **{"exception.stacktrace": stacktrace})

    configure_logging(LoggingConfig(service_name="svc", exception_detail="safe"), stream=safe)
    get_logger("t").error("investigation.attempt_failed", **{"exception.stacktrace": stacktrace})

    (full_record,) = _records(full)
    (safe_record,) = _records(safe)
    assert full_record["exception"] == stacktrace
    assert "exception" not in safe_record and "stacktrace" not in safe.getvalue()


def test_otlp_delivery_emits_once_through_the_provider_and_not_to_stdout() -> None:
    exporter = InMemoryLogRecordExporter()  # type: ignore[no-untyped-call]
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    stream = io.StringIO()
    configure_logging(
        LoggingConfig(service_name="svc", delivery="otlp"), logger_provider=provider, stream=stream
    )

    get_logger("t").warning("ingestion.candidate_rejected", outcome="rejected_span", count=2)

    (exported,) = exporter.get_finished_logs()
    body = json.loads(str(exported.log_record.body))
    assert body["event"] == "ingestion.candidate_rejected"
    assert body["outcome"] == "rejected_span"
    assert body["count"] == 2
    assert body["service.name"] == "svc"
    assert exported.log_record.attributes is not None
    assert exported.log_record.attributes["outcome"] == "rejected_span"
    assert exported.log_record.severity_text == "WARNING"
    assert stream.getvalue() == ""


def test_large_exception_detail_moves_to_the_otlp_body_with_native_correlation(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    tracer_provider, _ = span_exporter
    exporter = InMemoryLogRecordExporter()  # type: ignore[no-untyped-call]
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    configure_logging(
        LoggingConfig(service_name="svc", delivery="otlp", exception_detail="full"),
        logger_provider=logger_provider,
    )

    with start_span("run ingestion", tracer=tracer_provider.get_tracer("t")) as span:
        try:
            raise ExceptionGroup(
                "concurrent model failures",
                [RuntimeError(f"attempt-{index}: {'x' * 6000}") for index in range(20)],
            )
        except ExceptionGroup:
            get_logger("t").error(
                "ingestion.run_failed",
                exc_info=True,
                workflow_run_id="run-42",
            )

    (exported,) = exporter.get_finished_logs()
    record = exported.log_record
    attributes = record.attributes or {}
    assert isinstance(record.body, str)
    body = json.loads(record.body)
    assert body["event"] == "ingestion.run_failed"
    assert "ExceptionGroup" in body["exception"]
    assert len(record.body.encode()) > 64 * 1024
    assert "exception" not in attributes
    assert "trace_id" not in attributes and "span_id" not in attributes
    assert attributes["error.type"] == "ExceptionGroup"
    assert attributes["workflow_run_id"] == "run-42"
    assert record.trace_id == span.get_span_context().trace_id
    assert record.span_id == span.get_span_context().span_id
    assert format_trace_id(record.trace_id) == format_trace_id(span.get_span_context().trace_id)
    assert format_span_id(record.span_id) == format_span_id(span.get_span_context().span_id)


def test_stdlib_warnings_render_as_json_on_the_stream() -> None:
    stream = io.StringIO()
    configure_logging(LoggingConfig(service_name="svc"), stream=stream)

    logging.getLogger("opentelemetry.exporter.otlp").warning("export failed: %s", "boom")

    (record,) = _records(stream)
    assert record["event"] == "export failed: boom"
    assert record["level"] == "warning"


def test_otlp_delivery_requires_a_provider() -> None:
    with pytest.raises(ValueError, match="requires a logger provider"):
        configure_logging(LoggingConfig(service_name="svc", delivery="otlp"))
