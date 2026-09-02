from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from investigation_agent.bootstrap.runtime import RuntimeFactories, build_runtime
from investigation_agent.config.secrets import ServingSecrets
from investigation_agent.config.settings import Settings
from investigation_agent.core.context import RuntimeContext
from investigation_agent.core.errors import BudgetExhaustedFailure
from investigation_agent.genai.shared.llm import ModelClients
from langgraph.checkpoint.memory import InMemorySaver


class FakePool:
    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0

    async def open(self, *, wait: bool = True) -> None:
        del wait
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1


class FakePools:
    def __init__(self) -> None:
        self.reader = FakePool()
        self.writer = FakePool()

    async def open(self) -> None:
        await self.reader.open()
        await self.writer.open()

    async def close(self, *, timeout_s: float = 10.0) -> None:
        del timeout_s
        await self.reader.close()
        await self.writer.close()


class FakeEvidenceReader:
    pass


class NeverInvoked:
    async def ainvoke(self, input: object, **kwargs: Any) -> object:
        raise AssertionError("no model call is expected during composition")


class FakeChat:
    def with_structured_output(self, schema: type[Any], **kwargs: Any) -> Any:
        del schema, kwargs
        return NeverInvoked()

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self


class FakeEmbeddings:
    async def aembed_query(self, text: str) -> list[float]:
        raise AssertionError("no embedding call is expected during composition")


class FakeCancellation:
    cancelled = False

    def check(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        ENVIRONMENT_NAME="local",
        INVESTIGATION_AGENT_HOST="127.0.0.1",
        INVESTIGATION_AGENT_PORT=8080,
        AWS_REGION="eu-west-1",
        BEDROCK_CHAT_MODEL_ID="chat-model",
        BEDROCK_EMBEDDING_MODEL_ID="embedding-model",
        EXPECTED_AGENT_INITIALIZER_VERSION="agent-runtime@1",
    )


def _secrets() -> ServingSecrets:
    return ServingSecrets(
        AGENT_READER_DATABASE_URL="postgresql://agent_reader:x@db:5432/app",
        AGENT_WRITER_DATABASE_URL="postgresql://agent_writer:x@db:5432/app",
    )


def _clients(settings: Settings, callbacks: Any) -> ModelClients:
    del settings, callbacks
    chat = FakeChat()
    return ModelClients(
        planner=chat,
        guardrail=chat,
        search=chat,
        query=chat,
        projection=chat,
        verifier=chat,
        closure=chat,
        embeddings=FakeEmbeddings(),
    )


@pytest.mark.asyncio
async def test_runtime_composes_with_fakes_without_provider_or_database_clients() -> None:
    pools = FakePools()
    saver = InMemorySaver()
    factories = RuntimeFactories(
        pools=lambda settings, secrets: pools,  # type: ignore[arg-type,return-value]
        evidence_reader=lambda p, s: FakeEvidenceReader(),
        model_clients=_clients,
        checkpointer=lambda p: saver,
    )

    runtime = await build_runtime(_settings(), _secrets(), factories=factories)

    assert pools.reader.opened == 1 and pools.writer.opened == 1
    assert runtime.checkpointer is saver
    assert {t for t in runtime.agent.nodes["tools"].bound.tools_by_name} == {
        "search_evidence",
        "query_records",
        "find_connections",
    }
    assert runtime.sse_chunk_chars == 512 and runtime.readiness_timeout_s == 5.0
    with pytest.raises(FrozenInstanceError):
        runtime.sse_chunk_chars = 1  # type: ignore[misc]
    await runtime.close()
    assert pools.reader.closed == 1 and pools.writer.closed == 1


def test_runtime_context_carries_only_trusted_scope_deadline_and_cancellation() -> None:
    now = datetime.now(UTC)
    context = RuntimeContext(
        case_id="case-1",
        thread_id="thread-1",
        request_id="request-1",
        deadline=now + timedelta(seconds=5),
        cancellation=FakeCancellation(),
    )

    assert not hasattr(context, "owner_id") and not hasattr(context, "principal")
    assert context.remaining_seconds(now=now) == 5
    context.check_active(now=now)
    with pytest.raises(BudgetExhaustedFailure):
        context.check_active(now=now + timedelta(seconds=5))
