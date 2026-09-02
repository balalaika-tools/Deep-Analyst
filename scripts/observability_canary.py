"""Emit correlated local observability canaries through the Collector."""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from collections.abc import Sequence

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

SERVICE_NAME = "deep-analyst-canary"
GENAI_PROJECTION = {"app.telemetry.category": "genai"}
SECRET_ATTRIBUTES = {"http.request.header.authorization": "Bearer canary-secret"}
LARGE_FAILURE_DETAIL = "canary stack frame\n" * 4_096
CONTENT_ATTRIBUTES = {
    "gen_ai.system_instructions": '[{"type":"text","content":"canary system"}]',
    "gen_ai.input.messages": '[{"role":"user","content":"canary input"}]',
    "gen_ai.output.messages": '[{"role":"assistant","content":"canary output"}]',
    "gen_ai.tool.definitions": '[{"name":"canary_lookup"}]',
    "gen_ai.tool.call.arguments": '{"query":"canary argument"}',
    "gen_ai.tool.call.result": '{"answer":"canary result"}',
}


def _resource() -> Resource:
    return Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": "1.0.0",
        }
    )


def _trace_provider(endpoint: str) -> TracerProvider:
    provider = TracerProvider(resource=_resource())
    provider.add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    return provider


def _log_provider(endpoint: str, run_id: str) -> tuple[LoggerProvider, logging.Logger]:
    provider = LoggerProvider(resource=_resource())
    provider.add_log_record_processor(
        SimpleLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
    )
    logger = logging.getLogger(f"deep-analyst.observability-canary.{run_id}")
    logger.handlers.clear()
    logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=provider))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return provider, logger


def _emit_operational_trace(
    endpoint: str,
    run_id: str,
    counter: Counter,
    logger: logging.Logger,
) -> tuple[str, str, str, str]:
    provider = _trace_provider(endpoint)
    tracer = provider.get_tracer("deep-analyst.observability-canary")
    log_body = f"deep-analyst observability canary {run_id}"
    failure_marker = f"deep-analyst large failure canary {run_id}"
    attributes = {
        **CONTENT_ATTRIBUTES,
        **SECRET_ATTRIBUTES,
        "canary.kind": "operational",
        "canary.run_id": run_id,
    }
    with tracer.start_as_current_span("canary.operational", attributes=attributes) as span:
        trace_id = trace.format_trace_id(span.get_span_context().trace_id)
        span_id = trace.format_span_id(span.get_span_context().span_id)
        counter.add(1, {"canary.kind": "operational"})
        logger.info(log_body, extra={"canary.kind": "operational", "canary.run_id": run_id})
        logger.error(
            f"{failure_marker}\n{LARGE_FAILURE_DETAIL}",
            extra={
                "event.name": "canary.large_failure",
                "error.type": "ExceptionGroup",
                "canary.run_id": run_id,
            },
        )
    provider.shutdown()
    return trace_id, span_id, log_body, failure_marker


def _emit_genai_trace(endpoint: str, run_id: str) -> str:
    provider = _trace_provider(endpoint)
    tracer = provider.get_tracer("deep-analyst.genai-canary")
    root_attributes = {
        **CONTENT_ATTRIBUTES,
        **SECRET_ATTRIBUTES,
        **GENAI_PROJECTION,
        "canary.kind": "genai",
        "canary.run_id": run_id,
        "gen_ai.operation.name": "invoke_agent",
    }
    with tracer.start_as_current_span("canary.genai.workflow", attributes=root_attributes) as root:
        trace_id = trace.format_trace_id(root.get_span_context().trace_id)
        _emit_genai_children(tracer, run_id)
    provider.shutdown()
    return trace_id


def _emit_genai_children(tracer: trace.Tracer, run_id: str) -> None:
    spans = (
        (
            "POST /v1/chat/completions",
            {"http.request.method": "POST", "url.full": "https://canary.invalid/v1/chat"},
        ),
        ("canary.genai.retrieval", {"gen_ai.operation.name": "retrieval"}),
        (
            "canary.genai.tool",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "canary_lookup",
                "gen_ai.tool.call.arguments": CONTENT_ATTRIBUTES["gen_ai.tool.call.arguments"],
                "gen_ai.tool.call.result": CONTENT_ATTRIBUTES["gen_ai.tool.call.result"],
            },
        ),
        (
            "canary.genai.chat",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "canary",
                "gen_ai.request.model": "canary-model",
                "gen_ai.response.model": "canary-model-v1",
                "gen_ai.input.messages": CONTENT_ATTRIBUTES["gen_ai.input.messages"],
                "gen_ai.output.messages": CONTENT_ATTRIBUTES["gen_ai.output.messages"],
            },
        ),
    )
    for name, attributes in spans:
        with tracer.start_as_current_span(
            name,
            attributes={**attributes, **GENAI_PROJECTION, "canary.run_id": run_id},
        ):
            pass


def _metric_provider(endpoint: str) -> tuple[MeterProvider, Counter]:
    exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)
    provider = MeterProvider(resource=_resource(), metric_readers=[reader])
    counter = provider.get_meter("deep-analyst.observability-canary").create_counter(
        "deep_analyst_canary",
        description="End-to-end local observability verification counter",
    )
    return provider, counter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operational-endpoint", default="http://127.0.0.1:4318")
    parser.add_argument("--genai-endpoint", default="http://127.0.0.1:4328")
    parser.add_argument("--run-id", default=str(uuid.uuid4()))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    metric_provider, counter = _metric_provider(args.operational_endpoint)
    log_provider, logger = _log_provider(args.operational_endpoint, args.run_id)
    operational_trace_id, operational_span_id, log_body, failure_marker = _emit_operational_trace(
        args.operational_endpoint,
        args.run_id,
        counter,
        logger,
    )
    genai_trace_id = _emit_genai_trace(args.genai_endpoint, args.run_id)
    log_provider.force_flush()
    log_provider.shutdown()
    metric_provider.force_flush()
    metric_provider.shutdown()
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "operational_trace_id": operational_trace_id,
                "operational_span_id": operational_span_id,
                "genai_trace_id": genai_trace_id,
                "log_body": log_body,
                "failure_marker": failure_marker,
                "failure_detail_bytes": len(LARGE_FAILURE_DETAIL.encode()),
                "metric": "deep_analyst_canary_total",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
