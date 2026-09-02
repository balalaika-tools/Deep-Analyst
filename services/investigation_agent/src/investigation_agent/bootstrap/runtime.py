"""Composition root: build every interface once before the ASGI lifespan begins.

Domain and application modules never import FastAPI, psycopg, LangChain, or provider clients;
this module is where those concrete adapters meet. Every external constructor is a replaceable
factory so a unit test can compose the runtime with fakes.
"""

from __future__ import annotations

import platform
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from investigation_agent.adapters.postgres.checkpointer import create_checkpointer
from investigation_agent.adapters.postgres.evidence_reader import PostgresEvidenceReader
from investigation_agent.adapters.postgres.pools import (
    DatabasePools,
    PoolBounds,
    create_database_pools,
    probe_database_readiness,
)
from investigation_agent.api.dependencies import ReadinessResult
from investigation_agent.application.delete_thread import DeleteThread
from investigation_agent.application.invoke_turn import InvocationPolicy, InvokeTurn
from investigation_agent.application.read_history import (
    CheckpointReader,
    CursorCodec,
    HistoryReadPolicy,
    ReadHistory,
)
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.config.secrets import ServingSecrets
from investigation_agent.config.settings import Settings
from investigation_agent.genai.evidence_search.agent import SearchAgentPolicy, SearchEvidenceAgent
from investigation_agent.genai.evidence_search.llm import BedrockTextEmbedder
from investigation_agent.genai.evidence_search.retrieval import FusionPolicy
from investigation_agent.genai.guardrails.llm import (
    EvidenceGuardrailRunner,
    InputGuardrailRunner,
)
from investigation_agent.genai.investigation.agent import (
    AgentComponents,
    AgentLimits,
    build_investigation_agent,
)
from investigation_agent.genai.investigation.connections import FindConnections, GraphLimits
from investigation_agent.genai.investigation.prompts import (
    CLOSURE_SYSTEM_PROMPT,
    GROUNDING_SYSTEM_PROMPT,
)
from investigation_agent.genai.investigation.schemas import AnswerDraft, GroundingVerdict
from investigation_agent.genai.investigation.tools import (
    ToolDependencies,
    build_investigation_tools,
)
from investigation_agent.genai.record_query.agent import QueryAgentPolicy, QueryRecordsAgent
from investigation_agent.genai.record_query.executor import ExecutorLimits
from investigation_agent.genai.shared.llm import ModelClients, build_model_clients
from investigation_agent.genai.shared.retries import RetryPolicy
from investigation_agent.genai.shared.structured import StructuredRunner
from investigation_agent.genai.state_projection.llm import ProjectionModelRunner
from investigation_agent.observability.events import InvestigationInstruments
from investigation_agent.observability.instrumentation import (
    AttemptTelemetryFactory,
    InvestigationModelCallback,
    LogicalModelTelemetryMiddleware,
    LogicalToolTelemetryMiddleware,
    PhysicalToolTelemetryMiddleware,
)

SERVICE_NAMESPACE = "deep-analyst"
POLICY_VERSION = "investigation-policy@2"

# Provider and transport failures that are safe to retry physically; validation and policy
# outcomes are never in this tuple.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (TimeoutError, ConnectionError)


class Telemetry(Protocol):
    def tracer(self, name: str) -> Any: ...

    def meter(self, name: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Runtime:
    """Already-built application interfaces exposed through FastAPI application state."""

    invoke_turn: InvokeTurn
    read_history: ReadHistory
    delete_thread: DeleteThread
    readiness_probe: Callable[[], Awaitable[ReadinessResult]]
    sse_chunk_chars: int
    sse_heartbeat_s: float
    readiness_timeout_s: float
    shutdown_timeout_s: float
    agent: Any
    checkpointer: Any
    close: Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RuntimeFactories:
    """Every constructor with an external effect, so a test can substitute them."""

    pools: Callable[[Settings, ServingSecrets], DatabasePools] | None = None
    evidence_reader: Callable[[DatabasePools, Settings], Any] | None = None
    model_clients: Callable[[Settings, Sequence[object]], ModelClients] | None = None
    checkpointer: Callable[[DatabasePools], Any] | None = None
    telemetry: Telemetry | None = None
    callbacks: Sequence[object] = field(default_factory=tuple)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


def retry_policy(settings: Settings, *, attempts: int) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=attempts,
        initial_delay_s=settings.retry_initial_delay_s,
        backoff_factor=settings.retry_backoff_factor,
        max_delay_s=settings.retry_max_delay_s,
        jitter=settings.retry_jitter,
    )


def agent_limits(settings: Settings) -> AgentLimits:
    return AgentLimits(
        main_model_call_limit=settings.main_model_call_limit,
        main_tool_call_limit=settings.main_tool_call_limit,
        closure_model_calls=settings.closure_model_calls,
        max_context_tokens=settings.max_context_tokens,
        max_answer_chars=settings.max_answer_chars,
        max_evidence_cards=settings.max_evidence_cards,
        max_history_turns=settings.max_history_turns,
    )


def _default_pools(settings: Settings, secrets: ServingSecrets) -> DatabasePools:
    bounds = PoolBounds
    return create_database_pools(
        reader_dsn=secrets.reader_database_url.get_secret_value(),
        writer_dsn=secrets.writer_database_url.get_secret_value(),
        reader_bounds=bounds(
            settings.reader_pool_min_size,
            settings.reader_pool_max_size,
            settings.pool_acquire_timeout_s,
        ),
        writer_bounds=bounds(
            settings.writer_pool_min_size,
            settings.writer_pool_max_size,
            settings.pool_acquire_timeout_s,
        ),
    )


def _default_reader(pools: DatabasePools, settings: Settings) -> PostgresEvidenceReader:
    return PostgresEvidenceReader(
        pools.reader,
        acquisition_timeout_s=settings.pool_acquire_timeout_s,
        statement_timeout_ms=int(settings.tool_timeout_s * 1000),
    )


def build_agent_components(
    settings: Settings,
    *,
    evidence_reader: Any,
    reader_pool: Any,
    clients: ModelClients,
    telemetry: Sequence[Any] = (),
) -> AgentComponents:
    """Assemble nested agents, tools, guardrails, and structured runners from validated settings."""

    model_policy = retry_policy(settings, attempts=settings.model_retry_attempts)
    tool_policy = retry_policy(settings, attempts=settings.tool_retry_attempts)
    search = SearchEvidenceAgent(
        model=clients.search,
        reader=evidence_reader,
        embedder=BedrockTextEmbedder(clients.embeddings),
        fusion_policy=FusionPolicy(
            lexical_weight=settings.lexical_weight,
            vector_weight=settings.vector_weight,
            rrf_k=settings.reciprocal_rank_constant,
        ),
        retry_policy=tool_policy,
        transient_errors=TRANSIENT_ERRORS,
        policy=SearchAgentPolicy(
            model_call_limit=settings.nested_model_call_limit,
            tool_call_limit=settings.nested_tool_call_limit,
            max_top_k=settings.retrieval_top_k,
            max_evidence=min(100, settings.max_retrieved_rows),
            max_bytes=settings.max_result_bytes,
        ),
    )
    query = QueryRecordsAgent(
        model=clients.query,
        reader_pool=reader_pool,
        executor_limits=ExecutorLimits(
            max_rows=settings.max_query_rows,
            max_bytes=settings.max_result_bytes,
            statement_timeout_ms=int(settings.tool_timeout_s * 1000),
            acquisition_timeout_s=settings.pool_acquire_timeout_s,
        ),
        retry_policy=tool_policy,
        transient_errors=TRANSIENT_ERRORS,
        policy=QueryAgentPolicy(
            model_call_limit=settings.nested_model_call_limit,
            tool_call_limit=settings.nested_tool_call_limit,
            max_evidence=settings.max_query_rows,
        ),
    )
    connections = FindConnections(
        reader=evidence_reader,
        server_limits=GraphLimits(
            max_depth=settings.graph_max_depth,
            max_paths=settings.max_graph_paths,
            max_nodes=settings.graph_max_nodes,
            max_edges=settings.graph_max_edges,
            max_rows=settings.max_retrieved_rows,
        ),
    )
    tools = build_investigation_tools(
        ToolDependencies(
            search=search,
            query=query,
            connections=connections,
            retry_policy=tool_policy,
            transient_errors=TRANSIENT_ERRORS,
        )
    )
    return AgentComponents(
        model=clients.planner,
        tools=tools,
        guardrail=InputGuardrailRunner(
            clients.guardrail,
            policy=model_policy,
            transient_errors=TRANSIENT_ERRORS,
        ),
        evidence_guard=EvidenceGuardrailRunner(
            clients.guardrail,
            policy=model_policy,
            transient_errors=TRANSIENT_ERRORS,
        ),
        verifier=StructuredRunner(
            clients.verifier,
            GroundingVerdict,
            GROUNDING_SYSTEM_PROMPT,
            retry_policy=model_policy,
            transient_errors=TRANSIENT_ERRORS,
        ),
        closure=StructuredRunner(
            clients.closure,
            AnswerDraft,
            CLOSURE_SYSTEM_PROMPT,
            retry_policy=model_policy,
            transient_errors=TRANSIENT_ERRORS,
        ),
        projection_model=ProjectionModelRunner(clients.projection),
        retry_policy=model_policy,
        transient_errors=TRANSIENT_ERRORS,
        telemetry=tuple(telemetry),
    )


async def build_runtime(
    settings: Settings,
    secrets: ServingSecrets,
    *,
    factories: RuntimeFactories | None = None,
) -> Runtime:
    """Open pools, construct clients and the agent once, and return the immutable runtime."""

    factories = factories or RuntimeFactories()
    pools = (factories.pools or _default_pools)(settings, secrets)
    await pools.open()
    try:
        return await _compose(settings, pools, factories)
    except BaseException:
        await pools.close(timeout_s=settings.shutdown_timeout_s)
        raise


async def _compose(
    settings: Settings, pools: DatabasePools, factories: RuntimeFactories
) -> Runtime:
    callbacks: list[object] = list(factories.callbacks)
    telemetry_middleware: list[Any] = []
    attempt_factory: AttemptTelemetryFactory | None = None
    if factories.telemetry is not None:
        tracer = factories.telemetry.tracer("investigation_agent.genai")
        from observability.genai_metrics import GenAIInstruments

        investigation_instruments = InvestigationInstruments.create(
            factories.telemetry.meter("investigation_agent")
        )
        attempt_factory = AttemptTelemetryFactory(
            tracer=factories.telemetry.tracer("investigation_agent"),
            instruments=investigation_instruments,
        )
        callbacks.append(
            InvestigationModelCallback(
                tracer=tracer,
                model_instruments=GenAIInstruments.create(
                    factories.telemetry.meter("investigation_agent.genai")
                ),
                investigation_instruments=investigation_instruments,
                capture_content=settings.capture_ai_content,
                separate_system_instructions=True,
            )
        )
        telemetry_middleware = [
            LogicalModelTelemetryMiddleware(),
            LogicalToolTelemetryMiddleware(),
            PhysicalToolTelemetryMiddleware(
                known_tools=frozenset({"search_evidence", "query_records", "find_connections"})
            ),
        ]
    clients = (factories.model_clients or (lambda s, c: build_model_clients(s, callbacks=c)))(
        settings, callbacks
    )
    reader = (factories.evidence_reader or _default_reader)(pools, settings)
    checkpointer = (factories.checkpointer or (lambda p: create_checkpointer(p.writer)))(pools)
    components = build_agent_components(
        settings,
        evidence_reader=reader,
        reader_pool=pools.reader,
        clients=clients,
        telemetry=telemetry_middleware,
    )
    agent = build_investigation_agent(
        components, limits=agent_limits(settings), checkpointer=checkpointer
    )
    locks = ThreadLockRegistry()
    invoke_turn = InvokeTurn(
        graph=agent,
        locks=locks,
        policy=InvocationPolicy(
            policy_version=POLICY_VERSION,
            max_message_chars=64_000,
            turn_timeout_s=settings.turn_timeout_s,
            max_history_turns=settings.max_history_turns,
        ),
        clock=factories.clock,
        telemetry=attempt_factory,
    )
    read_history = ReadHistory(
        graph=agent,
        checkpointer=cast(CheckpointReader, checkpointer),
        locks=locks,
        cursors=CursorCodec(),
        policy=HistoryReadPolicy(
            default_page_size=settings.history_page_size,
            max_page_size=settings.history_max_page_size,
        ),
    )
    delete_thread = DeleteThread(graph=agent, checkpointer=checkpointer, locks=locks)

    async def readiness() -> ReadinessResult:
        return await probe_database_readiness(
            pools,
            expected_initializer_version=settings.expected_agent_initializer_version,
            timeout_s=settings.readiness_timeout_s,
        )

    async def close() -> None:
        await pools.close(timeout_s=settings.shutdown_timeout_s)

    return Runtime(
        invoke_turn=invoke_turn,
        read_history=read_history,
        delete_thread=delete_thread,
        readiness_probe=readiness,
        sse_chunk_chars=settings.sse_chunk_chars,
        sse_heartbeat_s=settings.sse_heartbeat_s,
        readiness_timeout_s=settings.readiness_timeout_s,
        shutdown_timeout_s=settings.shutdown_timeout_s,
        agent=agent,
        checkpointer=checkpointer,
        close=close,
    )


def observability_config(settings: Settings) -> Any:
    from observability import ObservabilityConfig

    return ObservabilityConfig(
        service_name=settings.otel_service_name,
        service_namespace=SERVICE_NAMESPACE,
        service_version=settings.service_version,
        service_instance_id=settings.service_instance_id or platform.node(),
        environment=settings.environment_name,
        traces_endpoint=settings.traces_endpoint,
        metrics_endpoint=settings.metrics_endpoint,
        logs_endpoint=settings.logs_endpoint if settings.log_export == "otlp" else None,
    )


__all__ = [
    "POLICY_VERSION",
    "TRANSIENT_ERRORS",
    "Runtime",
    "RuntimeFactories",
    "agent_limits",
    "build_agent_components",
    "build_runtime",
    "observability_config",
    "retry_policy",
]
