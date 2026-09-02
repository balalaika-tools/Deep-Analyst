"""Composition root: builds every concrete dependency, runs once, cleans up on every path."""

from __future__ import annotations

import logging
import platform
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import BaseRateLimiter
from observability import (
    LoggingConfig,
    ObservabilityConfig,
    Providers,
    configure_logging,
    configure_observability,
    error_type_of,
    get_logger,
    mark_failed,
    shutdown_observability,
    start_genai_span,
)
from observability.genai_metrics import GenAIInstruments, genai_metric_views
from observability.langchain import OTelModelCallback
from opentelemetry.sdk.metrics.view import View
from sqlalchemy.ext.asyncio import AsyncEngine

from ingestion.adapters.fixtures.manifest import Manifest, parse_manifest
from ingestion.adapters.s3.evidence_bucket import (
    EvidenceBucket,
    S3EditionSources,
    build_evidence_bucket,
)
from ingestion.application.ingest_dataset import (
    IngestionDependencies,
    IngestionPlan,
    RunOutcome,
    ingest_dataset,
)
from ingestion.config.settings import PIPELINE_VERSION, Settings
from ingestion.db.engine import build_engine, build_session_factory
from ingestion.db.indexes import bootstrap_store
from ingestion.db.store import SqlEvidenceStore
from ingestion.genai.embeddings.embedder import BedrockTextEmbedder
from ingestion.genai.embeddings.llm import build_embeddings
from ingestion.genai.entity_extraction.agent import build_entity_agent
from ingestion.genai.entity_extraction.extractor import AgentEntityExtractor
from ingestion.genai.entity_extraction.llm import build_chat_model
from ingestion.genai.relationship_extraction.agent import build_relationship_agent
from ingestion.genai.relationship_extraction.extractor import AgentRelationshipExtractor
from ingestion.genai.shared.throttle import build_throttle
from ingestion.observability.events import (
    JOB_NAME,
    SPAN_RUN,
    IngestionInstruments,
    LogEvent,
    Outcome,
)
from ingestion.ports.ingestion_ledger import (
    ChunkingConfig,
    compute_fingerprint,
    compute_source_digest,
)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
SERVICE_NAMESPACE = "deep-analyst"


async def _prepare_store(engine: AsyncEngine, embedding_dimensions: int) -> None:
    async with engine.begin() as conn:
        await bootstrap_store(conn, embedding_dimensions=embedding_dimensions)


@dataclass(frozen=True, slots=True)
class RuntimeFactories:
    """Every constructor with an external effect, so a test can substitute them."""

    configure_observability: Callable[..., Providers] = configure_observability
    configure_logging: Callable[..., None] = configure_logging
    shutdown_observability: Callable[[], None] = shutdown_observability
    engine: Callable[[Settings], AsyncEngine] = build_engine
    prepare_store: Callable[[AsyncEngine, int], Awaitable[None]] = _prepare_store
    evidence_bucket: Callable[[Settings], EvidenceBucket] = build_evidence_bucket
    embeddings: Callable[[Settings], Embeddings] = build_embeddings
    chat_model: Callable[..., BaseChatModel] = build_chat_model
    ingest: Callable[[IngestionPlan, IngestionDependencies], Awaitable[RunOutcome]] = ingest_dataset


DEFAULT_FACTORIES = RuntimeFactories()


def observability_config(settings: Settings) -> ObservabilityConfig:
    return ObservabilityConfig(
        service_name=settings.otel_service_name,
        service_namespace=SERVICE_NAMESPACE,
        service_version=settings.service_version,
        service_instance_id=settings.service_instance_id or platform.node(),
        environment="development",
        traces_endpoint=settings.traces_endpoint,
        metrics_endpoint=settings.metrics_endpoint,
        logs_endpoint=settings.logs_endpoint if settings.log_export == "otlp" else None,
    )


def _metric_views() -> Sequence[View]:
    return genai_metric_views()


def build_plan(settings: Settings, manifest: Manifest) -> IngestionPlan:
    chunking = ChunkingConfig(settings.chunk_window_chars, settings.chunk_overlap_chars)
    package_root = Path(__file__).resolve().parents[1]
    return IngestionPlan(
        edition=manifest.edition,
        edition_dir=manifest.path.parent,
        fingerprint=compute_fingerprint(
            manifest_bytes=manifest.raw_bytes,
            source_digest=compute_source_digest(package_root),
            embedding_model_id=settings.bedrock_embedding_model_id,
            chunking=chunking,
            pipeline_version=PIPELINE_VERSION,
        ),
        dataset_version=manifest.dataset_version,
        embedding_model_id=settings.bedrock_embedding_model_id,
        chunking=chunking,
        pipeline_version=PIPELINE_VERSION,
    )


def _build_dependencies(
    settings: Settings,
    plan: IngestionPlan,
    engine: AsyncEngine,
    providers: Providers,
    factories: RuntimeFactories,
    callback: OTelModelCallback,
    genai_instruments: GenAIInstruments,
    bucket: EvidenceBucket,
    sources: S3EditionSources,
) -> IngestionDependencies:
    tracer = providers.tracer("ingestion")
    throttle = build_throttle(settings)
    rate_limiter: BaseRateLimiter = throttle.rate_limiter
    callbacks: list[BaseCallbackHandler] = [callback]
    model = factories.chat_model(settings, rate_limiter=rate_limiter, callbacks=callbacks)
    store = SqlEvidenceStore(build_session_factory(engine))
    return IngestionDependencies(
        sources=sources,
        store=store,
        ledger=store,
        receipts=bucket,
        embedder=BedrockTextEmbedder(
            embeddings=factories.embeddings(settings),
            model_id=settings.bedrock_embedding_model_id,
            dimensions=settings.embedding_dimensions,
            throttle=throttle,
            tracer=tracer,
            instruments=genai_instruments,
        ),
        entity_extractor=AgentEntityExtractor(
            build_entity_agent(model, max_retries=settings.llm_max_retries),
            throttle=throttle,
            tracer=tracer,
        ),
        relationship_extractor=AgentRelationshipExtractor(
            build_relationship_agent(model, max_retries=settings.llm_max_retries),
            throttle=throttle,
            tracer=tracer,
        ),
        tracer=tracer,
        instruments=IngestionInstruments.create(providers.meter("ingestion")),
        clock=lambda: datetime.now(UTC),
    )


async def run(settings: Settings, factories: RuntimeFactories = DEFAULT_FACTORIES) -> int:
    """One ingestion run. Exit 0 on completed or skipped; 1 on any failure.

    Telemetry is configured first and force-flushed last; the engine is disposed on
    every path, including a failing run.
    """
    providers = factories.configure_observability(
        observability_config(settings), metric_views=_metric_views()
    )
    factories.configure_logging(
        LoggingConfig(
            service_name=settings.otel_service_name,
            level=settings.log_level,
            exception_detail="full",
            delivery=settings.log_export,
        ),
        logger_provider=providers.logger_provider,
    )
    # langchain_aws logs a traceback for every failed physical request; the run's own
    # failure record is the single owner of that detail.
    logging.getLogger("langchain_aws").setLevel(logging.CRITICAL)
    log = get_logger(__name__)
    genai_instruments = GenAIInstruments.create(providers.meter("ingestion.genai"))
    callback = OTelModelCallback(
        tracer=providers.tracer("ingestion.genai"),
        instruments=genai_instruments,
        capture_content=settings.capture_ai_content,
        # ChatBedrockConverse removes SystemMessage objects from chat history and
        # sends them through the provider's top-level `system` request field.
        separate_system_instructions=True,
    )
    engine: AsyncEngine | None = None
    sources: S3EditionSources | None = None
    exit_code = EXIT_FAILURE
    try:
        with start_genai_span(
            SPAN_RUN, tracer=providers.tracer("ingestion"), attributes={"app.job.name": JOB_NAME}
        ) as span:
            try:
                bucket = factories.evidence_bucket(settings)
                edition = settings.dataset_edition
                manifest = parse_manifest(
                    bucket.read_manifest_bytes(edition),
                    path=Path(f"datasets/{edition}/manifest.json"),
                )
                if manifest.edition != edition:
                    raise ValueError(
                        f"DATASET_EDITION={edition!r} does not match manifest language "
                        f"{manifest.edition!r}"
                    )
                plan = build_plan(settings, manifest)
                engine = factories.engine(settings)
                await factories.prepare_store(engine, settings.embedding_dimensions)
                sources = S3EditionSources(bucket, edition, engine)
                deps = _build_dependencies(
                    settings,
                    plan,
                    engine,
                    providers,
                    factories,
                    callback,
                    genai_instruments,
                    bucket,
                    sources,
                )
                outcome = await factories.ingest(plan, deps)
            except Exception as exc:
                # The one exception record for the run, emitted while the span is
                # current so it carries the trace and span identifiers.
                mark_failed(span, error_type_of(exc))
                span.set_attribute("app.outcome", Outcome.ERROR)
                log.error(LogEvent.RUN_FAILED, exc_info=True, **{"error.type": error_type_of(exc)})
            else:
                span.set_attribute("app.outcome", outcome.outcome)
                for key, value in outcome.counts.items():
                    span.set_attribute(f"app.ingestion.{key}", value)
                exit_code = EXIT_SUCCESS
    finally:
        callback.abandon_open_runs()
        if sources is not None:
            sources.close()
        if engine is not None:
            await engine.dispose()
        factories.shutdown_observability()
    return exit_code


__all__: list[Any] = ["DEFAULT_FACTORIES", "RuntimeFactories", "build_plan", "run"]
