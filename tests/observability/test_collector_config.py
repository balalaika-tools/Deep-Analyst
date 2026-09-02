"""Contract tests for destination-specific local Collector routing."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config/otel-lgtm/otelcol-config.yaml"
OTEL_CONTENT_KEYS = {
    "gen_ai.system_instructions",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.tool.definitions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
}
PRESENTATION_KEYS = {
    "app.gen_ai.observation.input",
    "app.gen_ai.observation.output",
}
CONTENT_KEYS = OTEL_CONTENT_KEYS | PRESENTATION_KEYS
GENAI_MARKER = "app.telemetry.category"
MIXED_SPAN_TREE: list[dict[str, Any]] = [
    {
        "trace_id": "trace-1",
        "span_id": "root",
        "parent_span_id": None,
        "attributes": {GENAI_MARKER: "genai"},
    },
    {
        "trace_id": "trace-1",
        "span_id": "source",
        "parent_span_id": "root",
        "attributes": {"app.ingestion.source_system": "docs"},
    },
    {
        "trace_id": "trace-1",
        "span_id": "index",
        "parent_span_id": "root",
        "attributes": {GENAI_MARKER: "genai"},
    },
    {
        "trace_id": "trace-1",
        "span_id": "workflow",
        "parent_span_id": "index",
        "attributes": {GENAI_MARKER: "genai"},
    },
    {
        "trace_id": "trace-1",
        "span_id": "embedding",
        "parent_span_id": "workflow",
        "attributes": {
            GENAI_MARKER: "genai",
            "gen_ai.input.messages": "canary payload",
            "app.gen_ai.observation.input": '[{"role":"user","content":"canary payload"}]',
            "app.gen_ai.observation.output": '{"entities":[]}',
        },
    },
    {
        "trace_id": "trace-1",
        "span_id": "persist",
        "parent_span_id": "root",
        "attributes": {"db.system.name": "postgresql"},
    },
]


def _config() -> dict[str, Any]:
    loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise AssertionError("Collector config must be a mapping")
    return loaded


def _trace_pipelines(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pipelines = config["service"]["pipelines"]
    return {name: pipeline for name, pipeline in pipelines.items() if name.startswith("traces/")}


def _deleted_keys(config: dict[str, Any], pipeline: dict[str, Any]) -> set[str]:
    deleted: set[str] = set()
    for processor_name in pipeline["processors"]:
        processor = config["processors"].get(processor_name, {})
        deleted.update(
            action["key"]
            for action in processor.get("actions", [])
            if action.get("action") == "delete"
        )
    return deleted


def _projected_spans(config: dict[str, Any], pipeline_name: str) -> list[dict[str, Any]]:
    pipeline = _trace_pipelines(config)[pipeline_name]
    spans = deepcopy(MIXED_SPAN_TREE)
    for processor_name in pipeline["processors"]:
        processor = config["processors"].get(processor_name, {})
        if processor_name == "filter/langfuse_projection":
            conditions = processor.get("trace_conditions", [])
            assert conditions == [f'span.attributes["{GENAI_MARKER}"] != "genai"']
            spans = [span for span in spans if span["attributes"].get(GENAI_MARKER) == "genai"]
        for action in processor.get("actions", []):
            if action.get("action") in {"insert", "update", "upsert"}:
                source_key = action.get("from_attribute")
                for span in spans:
                    if source_key in span["attributes"]:
                        span["attributes"][action["key"]] = span["attributes"][source_key]
            if action.get("action") == "delete":
                for span in spans:
                    span["attributes"].pop(action["key"], None)
    return spans


def test_tempo_drops_content_and_langfuse_preserves_canonical_content() -> None:
    config = _config()
    pipelines = _trace_pipelines(config)
    tempo_paths = [item for item in pipelines.values() if "otlp_http/tempo" in item["exporters"]]
    langfuse_path = pipelines["traces/genai-langfuse"]

    assert len(tempo_paths) == 2
    assert all(CONTENT_KEYS <= _deleted_keys(config, pipeline) for pipeline in tempo_paths)
    assert OTEL_CONTENT_KEYS.isdisjoint(_deleted_keys(config, langfuse_path))
    content_actions = config["processors"]["attributes/drop_genai_content"]["actions"]
    assert {action["key"] for action in content_actions} == CONTENT_KEYS


def test_langfuse_pipeline_projects_readable_io_and_drops_neutral_source() -> None:
    config = _config()
    pipeline = _trace_pipelines(config)["traces/genai-langfuse"]
    assert "attributes/langfuse_observation_io" in pipeline["processors"]

    projected = _projected_spans(config, "traces/genai-langfuse")
    embedding = next(span for span in projected if span["span_id"] == "embedding")
    attributes = embedding["attributes"]
    assert attributes["langfuse.observation.input"] == (
        '[{"role":"user","content":"canary payload"}]'
    )
    assert attributes["langfuse.observation.output"] == '{"entities":[]}'
    assert PRESENTATION_KEYS.isdisjoint(attributes)


def test_only_complete_genai_receiver_feeds_langfuse() -> None:
    config = _config()
    pipelines = _trace_pipelines(config)
    langfuse_paths = [
        pipeline for pipeline in pipelines.values() if "otlp_http/langfuse" in pipeline["exporters"]
    ]

    assert len(langfuse_paths) == 1
    assert langfuse_paths[0]["receivers"] == ["otlp/genai"]
    assert pipelines["traces/operational-tempo"]["receivers"] == ["otlp/operational"]


def test_langfuse_exporter_uses_authenticated_v4_otlp_http() -> None:
    exporter = _config()["exporters"]["otlp_http/langfuse"]

    assert exporter["endpoint"] == "http://langfuse-web:3000/api/public/otel"
    assert exporter["headers"]["Authorization"] == "Basic ${env:LANGFUSE_AUTH_STRING}"
    assert exporter["headers"]["x-langfuse-ingestion-version"] == "4"


def test_marker_projection_preserves_identity_and_parent_closure() -> None:
    config = _config()
    tempo = _projected_spans(config, "traces/genai-tempo")
    langfuse = _projected_spans(config, "traces/genai-langfuse")

    assert [(span["trace_id"], span["span_id"]) for span in tempo] == [
        (span["trace_id"], span["span_id"]) for span in MIXED_SPAN_TREE
    ]
    assert {span["span_id"] for span in langfuse} == {"root", "index", "workflow", "embedding"}
    retained_ids = {span["span_id"] for span in langfuse}
    assert all(
        span["parent_span_id"] is None or span["parent_span_id"] in retained_ids
        for span in langfuse
    )
    assert "gen_ai.input.messages" not in tempo[4]["attributes"]
    embedding = next(span for span in langfuse if span["span_id"] == "embedding")
    assert embedding["attributes"]["gen_ai.input.messages"] == "canary payload"


def test_bundled_backends_and_collector_metrics_use_internal_endpoints() -> None:
    config = _config()
    exporters = config["exporters"]
    pipelines = config["service"]["pipelines"]

    assert exporters["otlp_http/tempo"]["endpoint"] == "http://127.0.0.1:4418"
    assert exporters["otlp_http/prometheus"]["endpoint"] == "http://127.0.0.1:9090/api/v1/otlp"
    assert exporters["otlp_http/loki"]["endpoint"] == "http://127.0.0.1:3100/otlp"
    assert pipelines["metrics/operational-prometheus"]["receivers"] == [
        "otlp/operational",
        "prometheus/collector",
    ]
    assert pipelines["logs/operational-loki"]["receivers"] == ["otlp/operational"]


def test_config_has_health_without_sampling_or_debug_and_only_the_projection_filter() -> None:
    config = _config()

    assert config["extensions"]["health_check"]["path"] == "/ready"
    assert all("sampling" not in name for name in config["processors"])
    assert {name for name in config["processors"] if name.startswith("filter")} == {
        "filter/langfuse_projection"
    }
    assert all(not name.startswith("debug") for name in config["exporters"])
