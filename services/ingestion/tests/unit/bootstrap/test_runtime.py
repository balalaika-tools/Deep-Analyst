from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from ingestion.adapters.fixtures.manifest import parse_manifest
from ingestion.adapters.s3.evidence_bucket import EvidenceBucket
from ingestion.application.ingest_case import IngestionDependencies, IngestionPlan, RunOutcome
from ingestion.bootstrap.runtime import RuntimeFactories, build_plan, run
from ingestion.config.settings import Settings
from observability import ObservabilityConfig, Providers
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeBucket:
    def __init__(self, edition_dir: Path) -> None:
        self.edition_dir = edition_dir
        self.materializations = 0

    def read_manifest_bytes(self, edition: str) -> bytes:
        return (self.edition_dir / "manifest.json").read_bytes()

    @contextmanager
    def materialize_edition(self, edition: str) -> Iterator[Path]:
        self.materializations += 1
        yield self.edition_dir

    def read(self, edition: str) -> None:
        return None

    def write(self, edition: str, receipt: Any) -> None:
        pass


class Recorder:
    def __init__(self, edition_dir: Path) -> None:
        self.events: list[str] = []
        self.exporter = InMemorySpanExporter()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.providers = Providers(
            config=ObservabilityConfig("ingestion", "ns", "v", "i", "test", None, None, None),
            tracer_provider=tracer_provider,
            meter_provider=MeterProvider(),
            logger_provider=None,
        )
        self.engine = FakeEngine()
        self.bucket = FakeBucket(edition_dir)

    def configure_observability(self, config: ObservabilityConfig, **kwargs: Any) -> Providers:
        self.events.append("configure_observability")
        return self.providers

    def configure_logging(self, *args: Any, **kwargs: Any) -> None:
        self.events.append("configure_logging")

    def shutdown_observability(self) -> None:
        self.events.append("shutdown_observability")

    def build_engine(self, settings: Settings) -> Any:
        self.events.append("engine")
        return self.engine

    async def prepare_store(self, engine: Any, dimensions: int) -> None:
        self.events.append(f"prepare_store:{dimensions}")

    def factories(self, ingest: Any) -> RuntimeFactories:
        return RuntimeFactories(
            configure_observability=self.configure_observability,
            configure_logging=self.configure_logging,
            shutdown_observability=self.shutdown_observability,
            engine=self.build_engine,
            prepare_store=self.prepare_store,
            evidence_bucket=lambda settings: cast(EvidenceBucket, self.bucket),
            embeddings=lambda settings: object(),  # type: ignore[arg-type,return-value]
            chat_model=lambda settings, **kwargs: object(),  # type: ignore[arg-type]
            ingest=ingest,
        )


def _settings() -> Settings:
    return Settings(
        ENVIRONMENT_NAME="local",
        DATABASE_URL="postgresql+psycopg://app:pw@127.0.0.1:5432/app",
        EVIDENCE_S3_ENDPOINT="http://127.0.0.1:9090",
        EVIDENCE_S3_BUCKET="evidence-test",
        EVIDENCE_S3_ACCESS_KEY="evidence-user",
        EVIDENCE_S3_SECRET_KEY="evidence-secret",
        DATASET_EDITION="en",
        AWS_REGION="eu-central-1",
        BEDROCK_CHAT_MODEL_ID="chat-model",
        BEDROCK_EMBEDDING_MODEL_ID="embed-model",
        EMBEDDING_DIMENSIONS=8,
    )


@pytest.mark.asyncio
async def test_failing_run_still_disposes_the_engine_and_shuts_telemetry_down(
    edition_dir: Path,
) -> None:
    recorder = Recorder(edition_dir)

    async def failing_ingest(plan: IngestionPlan, deps: IngestionDependencies) -> RunOutcome:
        recorder.events.append("ingest")
        raise RuntimeError("bedrock down")

    exit_code = await run(_settings(), recorder.factories(failing_ingest))

    assert exit_code == 1
    assert recorder.engine.disposed
    assert recorder.events == [
        "configure_observability",
        "configure_logging",
        "engine",
        "prepare_store:8",
        "ingest",
        "shutdown_observability",
    ]
    (span,) = recorder.exporter.get_finished_spans()
    assert span.name == "run ingestion" and span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None and span.attributes["error.type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_successful_run_exits_zero_with_outcome_on_the_root_span(
    edition_dir: Path,
) -> None:
    recorder = Recorder(edition_dir)
    seen: dict[str, Any] = {}

    async def ingest(plan: IngestionPlan, deps: IngestionDependencies) -> RunOutcome:
        seen["plan"] = plan
        seen["deps"] = deps
        return RunOutcome("success", {"records": 142})

    exit_code = await run(_settings(), recorder.factories(ingest))

    assert exit_code == 0 and recorder.engine.disposed
    assert seen["plan"].case_id == "case_trg_001" and seen["plan"].edition == "en"
    assert seen["deps"].embedder.dimensions == 8
    assert recorder.bucket.materializations == 0, "raw evidence remains lazy until a source loads"
    (span,) = recorder.exporter.get_finished_spans()
    assert span.attributes is not None
    assert (
        span.attributes["app.outcome"] == "success"
        and span.attributes["app.ingestion.records"] == 142
    )
    assert span.attributes["app.job.name"] == "ingestion"


def test_plan_fingerprint_follows_the_manifest_model_and_chunking(
    edition_dir: Path,
) -> None:
    manifest = parse_manifest(
        (edition_dir / "manifest.json").read_bytes(), path=edition_dir / "manifest.json"
    )
    base = build_plan(_settings(), manifest)
    other_model = build_plan(
        _settings().model_copy(update={"bedrock_embedding_model_id": "x"}), manifest
    )
    assert base.fingerprint == build_plan(_settings(), manifest).fingerprint
    assert base.fingerprint != other_model.fingerprint
    assert base.dataset_version == "trg-synth-en-v1.0.0"
    assert datetime.now(UTC).year >= 2026
