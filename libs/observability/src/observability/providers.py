"""Process-wide OpenTelemetry providers: explicit, idempotent, bounded shutdown."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from observability.config import ObservabilityConfig

_log = logging.getLogger(__name__)


class ObservabilityConfigurationError(RuntimeError):
    """A second, incompatible configuration was requested in the same process."""


@dataclass(slots=True)
class Providers:
    config: ObservabilityConfig
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider | None

    def tracer(self, name: str) -> trace.Tracer:
        return self.tracer_provider.get_tracer(name)

    def meter(self, name: str) -> metrics.Meter:
        return self.meter_provider.get_meter(name)


_providers: Providers | None = None


def _resource(config: ObservabilityConfig) -> Resource:
    return Resource.create(
        {
            "service.namespace": config.service_namespace,
            "service.name": config.service_name,
            "service.version": config.service_version,
            "service.instance.id": config.service_instance_id,
            "deployment.environment.name": config.environment,
        }
    )


def _tracer_provider(config: ObservabilityConfig, resource: Resource) -> TracerProvider:
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    if config.traces_endpoint:
        exporter = OTLPSpanExporter(
            endpoint=config.traces_endpoint, timeout=config.export_timeout_ms / 1000
        )
        provider.add_span_processor(
            BatchSpanProcessor(exporter, export_timeout_millis=config.export_timeout_ms)
        )
    return provider


def _meter_provider(
    config: ObservabilityConfig, resource: Resource, views: Sequence[View]
) -> MeterProvider:
    readers: list[MetricReader] = []
    if config.metrics_endpoint:
        exporter = OTLPMetricExporter(
            endpoint=config.metrics_endpoint, timeout=config.export_timeout_ms / 1000
        )
        readers.append(
            PeriodicExportingMetricReader(
                exporter,
                export_interval_millis=config.metric_export_interval_ms,
                export_timeout_millis=config.export_timeout_ms,
            )
        )
    return MeterProvider(resource=resource, metric_readers=readers, views=list(views))


def _logger_provider(config: ObservabilityConfig, resource: Resource) -> LoggerProvider | None:
    if not config.logs_endpoint:
        return None
    provider = LoggerProvider(resource=resource)
    exporter = OTLPLogExporter(
        endpoint=config.logs_endpoint, timeout=config.export_timeout_ms / 1000
    )
    provider.add_log_record_processor(
        BatchLogRecordProcessor(exporter, export_timeout_millis=config.export_timeout_ms)
    )
    return provider


def configure_observability(
    config: ObservabilityConfig, *, metric_views: Sequence[View] = ()
) -> Providers:
    """Build and register the providers once per process.

    Calling again with an equal configuration returns the existing providers; calling
    with a different configuration raises instead of silently re-stamping identity.
    """
    global _providers
    if _providers is not None:
        if _providers.config == config:
            return _providers
        raise ObservabilityConfigurationError(
            "observability is already configured with a different configuration"
        )

    resource = _resource(config)
    tracer_provider = _tracer_provider(config, resource)
    meter_provider = _meter_provider(config, resource, metric_views)
    logger_provider = _logger_provider(config, resource)

    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    if logger_provider is not None:
        set_logger_provider(logger_provider)

    _providers = Providers(
        config=config,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
    )
    return _providers


def current_providers() -> Providers | None:
    return _providers


def shutdown_observability() -> None:
    """Flush and stop every provider once, within the configured bound.

    Export failures are logged and never raised: telemetry must not replace the
    process's own outcome.
    """
    global _providers
    if _providers is None:
        return
    providers, _providers = _providers, None
    timeout = providers.config.shutdown_timeout_ms
    steps = (
        ("traces", providers.tracer_provider.force_flush, providers.tracer_provider.shutdown),
        ("metrics", providers.meter_provider.force_flush, providers.meter_provider.shutdown),
    )
    for signal, flush, stop in steps:
        try:
            flush(timeout)
            stop()
        except Exception:
            _log.warning("telemetry shutdown failed", extra={"signal": signal}, exc_info=True)
    if providers.logger_provider is not None:
        try:
            providers.logger_provider.force_flush(timeout)
            providers.logger_provider.shutdown()
        except Exception:
            _log.warning("telemetry shutdown failed", extra={"signal": "logs"}, exc_info=True)
