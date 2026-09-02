"""Nested ``search_evidence`` agent behaviour with a scripted chat model."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from evidence_model import FieldLocator, SourceRef
from investigation_agent.genai.evidence_search.agent import SearchAgentPolicy, SearchEvidenceAgent
from investigation_agent.genai.evidence_search.retrieval import FusionPolicy
from investigation_agent.genai.evidence_search.schemas import (
    RetrievalCandidate,
    RetrievalModality,
    RetrievalQuery,
    SearchIntent,
)
from investigation_agent.genai.shared.retries import CancellationToken, RetryPolicy
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

POLICY = RetryPolicy(
    max_attempts=2, initial_delay_s=0, backoff_factor=1, max_delay_s=0, jitter=False
)


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
    ) -> Any:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self.seen.append(list(messages))
        self.calls += 1
        template = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        message = AIMessage(
            content=template.content,
            tool_calls=[{**c, "id": f"{c['id']}-{self.calls}"} for c in template.tool_calls],
            id=f"ai-{self.calls}",
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def retrieve_call(query: str, **extra: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "retrieve", "args": {"query": query, **extra}, "id": "r", "type": "tool_call"}
        ],
    )


def verdict(status: str, *ids: str, reason: str = "sufficient") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "SearchVerdict",
                "args": {
                    "status": status,
                    "selected_chunk_ids": list(ids),
                    "safe_reason_code": reason,
                },
                "id": "v",
                "type": "tool_call",
            }
        ],
    )


def _candidate(
    chunk_id: str,
    modality: RetrievalModality,
    rank: int,
    *,
    text: str = "Transfer of 50 to account 77",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        record_id=f"record-{chunk_id}",
        text=text,
        content_hash="a" * 64,
        source_ref=SourceRef(record_id=f"record-{chunk_id}", locator=FieldLocator(field="text")),
        source_system="bank",
        modality=modality,
        raw_score=1.0 / rank,
        rank=rank,
    )


@dataclass
class FakeReader:
    lexical: dict[str, list[str]] = field(default_factory=dict)
    vector: dict[str, list[str]] = field(default_factory=dict)
    lexical_failures: int = 0
    vector_failures: int = 0
    calls: list[tuple[str, str, frozenset[str]]] = field(default_factory=list)

    async def search_lexical(
        self,
        *,
        query: RetrievalQuery,
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> list[RetrievalCandidate]:
        del deadline
        self.calls.append(("lexical", query.query, excluded_chunk_ids))
        if self.lexical_failures:
            self.lexical_failures -= 1
            raise ConnectionError("lexical down")
        ids = [c for c in self.lexical.get(query.query, []) if c not in excluded_chunk_ids]
        return [_candidate(c, RetrievalModality.BM25, i + 1) for i, c in enumerate(ids)]

    async def search_vector(
        self,
        *,
        query: RetrievalQuery,
        embedding: Sequence[float],
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> list[RetrievalCandidate]:
        del embedding, deadline
        self.calls.append(("vector", query.query, excluded_chunk_ids))
        if self.vector_failures:
            self.vector_failures -= 1
            raise ConnectionError("vector down")
        ids = [c for c in self.vector.get(query.query, []) if c not in excluded_chunk_ids]
        return [_candidate(c, RetrievalModality.VECTOR, i + 1) for i, c in enumerate(ids)]


class FakeEmbedder:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    async def embed(self, text: str, *, deadline: float) -> Sequence[float]:
        del text, deadline
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise TimeoutError("embedding down")
        return (0.1, 0.2)


def _intent() -> SearchIntent:
    return SearchIntent(question="Who received the transfer?", objective="find the beneficiary")


def _agent(
    responses: list[AIMessage],
    reader: FakeReader,
    *,
    embedder: FakeEmbedder | None = None,
    policy: SearchAgentPolicy | None = None,
) -> tuple[SearchEvidenceAgent, ScriptedChatModel]:
    model = ScriptedChatModel(responses=responses, seen=[])
    agent = SearchEvidenceAgent(
        model=model,
        reader=reader,
        embedder=embedder or FakeEmbedder(),
        fusion_policy=FusionPolicy(),
        retry_policy=POLICY,
        transient_errors=(TimeoutError,),
        policy=policy,
    )
    return agent, model


async def _run(
    agent: SearchEvidenceAgent,
    *,
    seen: frozenset[str] = frozenset(),
    progress: list[Mapping[str, object]] | None = None,
) -> Any:
    return await agent.run(
        _intent(),
        call_id="call-1",
        deadline=asyncio.get_running_loop().time() + 5,
        cancellation=CancellationToken.create(),
        seen_chunk_ids=seen,
        progress=None if progress is None else progress.append,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lexical", "vector", "expected_ids", "modalities"),
    [
        ({"transfer": ["c1"]}, {}, ["c1"], {"bm25"}),
        ({}, {"transfer": ["c2"]}, ["c2"], {"vector"}),
        ({"transfer": ["c1", "c3"]}, {"transfer": ["c3", "c1"]}, ["c1", "c3"], {"bm25", "vector"}),
    ],
    ids=["lexical-only", "vector-only", "overlapping"],
)
async def test_hybrid_retrieval_fuses_modalities_and_returns_only_retrieved_selections(
    lexical: dict[str, list[str]],
    vector: dict[str, list[str]],
    expected_ids: list[str],
    modalities: set[str],
) -> None:
    reader = FakeReader(lexical=lexical, vector=vector)
    agent, model = _agent([retrieve_call("transfer"), verdict("sufficient", *expected_ids)], reader)
    progress: list[Mapping[str, object]] = []

    outcome = await _run(agent, progress=progress)

    assert outcome.status == "sufficient"
    assert sorted(e.evidence_id for e in outcome.evidence) == expected_ids
    assert {p.modality.value for e in outcome.evidence for p in e.provenance} == modalities
    assert len(outcome.attempts) == 1 and outcome.attempts[0].semantic_attempt == 1
    assert outcome.consumption.tool_calls == 1 and outcome.consumption.model_calls == 2
    assert progress and progress[0]["phase"] == "searching_evidence" and "query" not in progress[0]
    assert "<untrusted-evidence" in str(model.seen[1][-1].content)


@pytest.mark.asyncio
async def test_reformulation_excludes_earlier_chunks_and_parent_supplied_seen_set() -> None:
    reader = FakeReader(lexical={"transfer": ["c1", "seen-1"], "beneficiary": ["c1", "c2"]})
    agent, _ = _agent(
        [retrieve_call("transfer"), retrieve_call("beneficiary"), verdict("sufficient", "c2")],
        reader,
    )

    outcome = await _run(agent, seen=frozenset({"seen-1"}))

    assert outcome.status == "sufficient" and [e.evidence_id for e in outcome.evidence] == ["c2"]
    assert len(outcome.attempts) == 2
    assert reader.calls[0][2] == frozenset({"seen-1"})
    assert reader.calls[2][2] == frozenset({"seen-1", "c1"})
    assert outcome.evidence[0].provenance[0].semantic_attempt == 2


@pytest.mark.asyncio
async def test_three_attempts_are_the_limit_and_a_miss_is_not_absence() -> None:
    reader = FakeReader(lexical={})
    responses = [
        retrieve_call("a"),
        retrieve_call("b"),
        retrieve_call("c"),
        retrieve_call("d"),
        verdict("no_retrieved_support", reason="irrelevant"),
    ]
    agent, model = _agent(responses, reader)

    outcome = await _run(agent)

    assert outcome.status == "no_retrieved_support"
    assert len(outcome.attempts) == 3 and len(reader.calls) == 6
    assert outcome.evidence == ()
    assert model.calls == 5
    blocked = str(model.seen[4][-1].content)
    assert "limit" in blocked.lower() or "not" in blocked.lower()


@pytest.mark.asyncio
async def test_repeated_fingerprint_is_rejected_without_io() -> None:
    reader = FakeReader(lexical={"transfer": ["c1"]})
    agent, model = _agent(
        [retrieve_call("transfer"), retrieve_call("  Transfer "), verdict("sufficient", "c1")],
        reader,
    )

    outcome = await _run(agent)

    assert len(reader.calls) == 2
    assert len(outcome.attempts) == 1
    assert "repeated_query_rejected" in outcome.warnings
    assert "repeated_query" in str(model.seen[2][-1].content)


@pytest.mark.asyncio
async def test_unretrieved_selection_is_dropped_with_a_warning() -> None:
    reader = FakeReader(lexical={"transfer": ["c1"]})
    agent, _ = _agent([retrieve_call("transfer"), verdict("sufficient", "c1", "invented")], reader)

    outcome = await _run(agent)

    assert [e.evidence_id for e in outcome.evidence] == ["c1"]
    assert "unretrieved_selection_dropped" in outcome.warnings


@pytest.mark.asyncio
async def test_partial_provider_failure_yields_a_typed_partial_attempt() -> None:
    reader = FakeReader(lexical={"transfer": ["c1"]})
    agent, _ = _agent(
        [
            retrieve_call("transfer"),
            verdict("retrieval_incomplete", "c1", reason="partial_coverage"),
        ],
        reader,
        embedder=FakeEmbedder(failures=5),
    )

    outcome = await _run(agent)

    assert outcome.status == "retrieval_incomplete"
    assert (
        outcome.attempts[0].lexical_status == "ok" and outcome.attempts[0].vector_status == "failed"
    )
    assert outcome.attempts[0].safe_diagnostic == "partial_modality"
    assert "vector_unavailable" in outcome.warnings


@pytest.mark.asyncio
async def test_transient_retry_keeps_one_fingerprint_and_counts_physical_attempts() -> None:
    reader = FakeReader(
        lexical={"transfer": ["c1"]},
        vector={"transfer": ["c1"]},
        lexical_failures=1,
        vector_failures=1,
    )
    agent, model = _agent([retrieve_call("transfer"), verdict("sufficient", "c1")], reader)

    outcome = await _run(agent)

    assert outcome.status == "sufficient"
    assert len(outcome.attempts) == 1 and outcome.attempts[0].physical_attempts == 2
    assert outcome.consumption.physical_attempts == 2
    assert model.calls == 2


@pytest.mark.asyncio
async def test_nested_message_history_is_discarded_between_invocations() -> None:
    reader = FakeReader(lexical={"transfer": ["c1"]})
    agent, model = _agent([retrieve_call("transfer"), verdict("sufficient", "c1")], reader)

    await _run(agent)
    model.responses = [verdict("no_retrieved_support", reason="attempts_exhausted")]
    model.calls = 0
    second = await _run(agent)

    assert isinstance(model.seen[-1][-1], HumanMessage)
    assert all(not isinstance(m, AIMessage) for m in model.seen[-1][1:])
    assert second.status == "no_retrieved_support" and second.attempts == ()


@pytest.mark.asyncio
async def test_model_limit_ends_the_nested_loop_with_an_incomplete_outcome() -> None:
    reader = FakeReader(lexical={"a": ["c1"], "b": ["c2"]})
    agent, model = _agent(
        [retrieve_call("a"), retrieve_call("b")],
        reader,
        policy=SearchAgentPolicy(model_call_limit=2),
    )

    outcome = await _run(agent)

    assert outcome.status == "retrieval_incomplete"
    assert "nested_agent_limit_reached" in outcome.warnings
    assert model.calls == 2 and outcome.evidence == ()
