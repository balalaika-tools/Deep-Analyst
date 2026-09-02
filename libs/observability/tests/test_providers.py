import subprocess
import sys
from collections.abc import Iterator

import pytest
from observability import (
    ObservabilityConfig,
    ObservabilityConfigurationError,
    configure_observability,
    current_providers,
    shutdown_observability,
)


def _config(**overrides: object) -> ObservabilityConfig:
    values: dict[str, object] = {
        "service_name": "unit-test",
        "service_namespace": "deep-analyst",
        "service_version": "test",
        "service_instance_id": "instance-1",
        "environment": "test",
        "traces_endpoint": None,
        "metrics_endpoint": None,
        "logs_endpoint": None,
    }
    values.update(overrides)
    return ObservabilityConfig(**values)  # type: ignore[arg-type]


@pytest.fixture
def clean_lifecycle() -> Iterator[None]:
    shutdown_observability()
    yield
    shutdown_observability()


def test_import_is_inert() -> None:
    script = (
        "import observability, opentelemetry.trace as t, opentelemetry.metrics as m\n"
        "print(type(t.get_tracer_provider()).__name__, type(m.get_meter_provider()).__name__)"
    )
    output = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    ).stdout
    assert output.split() == ["ProxyTracerProvider", "_ProxyMeterProvider"]


def test_same_configuration_is_idempotent_and_a_different_one_is_rejected(
    clean_lifecycle: None,
) -> None:
    first = configure_observability(_config())

    assert configure_observability(_config()) is first
    assert current_providers() is first
    with pytest.raises(ObservabilityConfigurationError):
        configure_observability(_config(service_name="other"))


def test_shutdown_is_idempotent_and_allows_reconfiguration(clean_lifecycle: None) -> None:
    configure_observability(_config())
    shutdown_observability()
    shutdown_observability()

    assert current_providers() is None
    providers = configure_observability(_config(logs_endpoint="http://127.0.0.1:9/v1/logs"))
    assert providers.logger_provider is not None
    assert providers.tracer("x") is not None


def test_trace_provider_flushes_and_shuts_down_once(
    clean_lifecycle: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = configure_observability(_config())
    calls: list[str] = []

    def force_flush(timeout_millis: int) -> bool:
        calls.append(f"flush:{timeout_millis}")
        return True

    def shutdown() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(
        providers.tracer_provider,
        "force_flush",
        force_flush,
    )
    monkeypatch.setattr(providers.tracer_provider, "shutdown", shutdown)

    shutdown_observability()
    shutdown_observability()

    assert calls == [f"flush:{providers.config.shutdown_timeout_ms}", "shutdown"]
