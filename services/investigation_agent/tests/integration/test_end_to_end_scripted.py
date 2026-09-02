"""Scripted-model end-to-end turn against a real PostgreSQL checkpointer.

Covers: first message through several tools, an interruption after a mid-turn checkpoint,
resume with the same request ID, verified commit, streamed answer, paginated history, a second
turn that receives the projection, byte-identical replay, and deletion.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from evidence_model import FieldLocator, SourceRef
from investigation_agent.adapters.postgres.checkpointer import create_checkpointer
from investigation_agent.adapters.postgres.pools import DatabasePools
from investigation_agent.api.sse import stream_prepared_turn
from investigation_agent.application.delete_thread import DeleteThread
from investigation_agent.application.invoke_turn import (
    InvocationPolicy,
    InvokeRequest,
    InvokeTurn,
    PreparedTurnKind,
    ThreadNotFound,
)
from investigation_agent.application.read_history import (
    CheckpointReader,
    CursorCodec,
    HistoryReadPolicy,
    ReadHistory,
)
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.history import HistoryRole, TurnStatus
from investigation_agent.domain.investigation_state import WorkingProjection
from investigation_agent.domain.tool_outcome import (
    BudgetConsumption,
    EvidenceItem,
    OutcomeStatus,
    ToolOutcome,
    canonical_fingerprint,
)
from investigation_agent.genai.guardrails.schemas import InputGuardrailStatus, InputGuardrailVerdict
from investigation_agent.genai.investigation.agent import (
    AgentComponents,
    AgentLimits,
    build_investigation_agent,
)
from investigation_agent.genai.investigation.schemas import GroundingVerdict
from investigation_agent.genai.shared.retries import AttemptResult, RetryPolicy
from investigation_agent.genai.state_projection.schemas import ProjectionInput
from langchain.tools import ToolRuntime
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

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
        is_follow_up = any("What was the first transfer amount?" in str(item.content) for item in messages)
        template = (
            AIMessage(content="", tool_calls=[_draft("c5", ANSWER_2, "t_85")])
            if is_follow_up
            else self.responses[min(self.calls - 1, len(self.responses) - 1)]
        )
        message = AIMessage(
            content=template.content,
            tool_calls=[{**c, "id": f"{c['id']}-{self.calls}"} for c in template.tool_calls],
            id=f"ai-{self.calls}",
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _tool_call(name: str, args: dict[str, Any], identifier: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


def _draft(identifier: str, answer: str, *ids: str) -> dict[str, Any]:
    return _tool_call(
        "AnswerDraft",
        {
            "answer": answer,
            "claims": [
                {
                    "claim_id": "k1",
                    "text": answer,
                    "kind": "verified",
                    "material": True,
                    "evidence_ids": list(ids),
                }
            ],
        },
        identifier,
    )


def _evidence(evidence_id: str, kind: str, content: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        content_hash=canonical_fingerprint(content),
        source_refs=(
            SourceRef(record_id=f"record-{evidence_id}", locator=FieldLocator(field="text")),
        ),
        content=content,
        evidentiary_status="verified",
    )


@dataclass
class Tools:
    calls: list[str] = field(default_factory=list)

    def build(self) -> list[Any]:
        calls = self.calls

        def make(name: str, item: EvidenceItem, status: OutcomeStatus) -> Any:
            @tool(name, response_format="content_and_artifact", description=name)
            async def implementation(
                intent: dict[str, Any], runtime: ToolRuntime[RuntimeContext, Any]
            ) -> tuple[str, ToolOutcome]:
                del intent
                calls.append(name)
                assert runtime.context.thread_id
                return status.value, ToolOutcome(
                    call_id=runtime.tool_call_id or name,
                    intent_fingerprint="a" * 64,
                    tool=name,
                    status=status,
                    evidence=(item,),
                    consumption=BudgetConsumption(tool_calls=1, physical_attempts=1, rows=1),
                )

            return implementation

        return [
            make(
                "search_evidence",
                _evidence("t_85", "chunk", "Booked transfer of EUR 9,500 on March 3"),
                OutcomeStatus.SUFFICIENT,
            ),
            make(
                "query_records",
                _evidence("t_86", "row", "Booked transfer of EUR 9,700 on March 4"),
                OutcomeStatus.QUERY_SUFFICIENT,
            ),
            make(
                "find_connections",
                _evidence("t_88", "entity", "Booked transfer of EUR 9,800 on March 5"),
                OutcomeStatus.CONNECTIONS_FOUND,
            ),
        ]


class Verifier:
    async def run(
        self, payload: Any, *, context: RuntimeContext
    ) -> AttemptResult[GroundingVerdict]:
        del context
        return AttemptResult(
            value=GroundingVerdict(
                claims=tuple(
                    {"claim_id": c["claim_id"], "supported": True, "safe_reason_code": "entailed"}
                    for c in payload["claims"]
                )
            ),
            attempts=1,
        )


class Projection:
    async def __call__(
        self, request: ProjectionInput, *, repair_violations: tuple[str, ...] = ()
    ) -> WorkingProjection:
        return WorkingProjection(
            source_turn_id=request.source_turn_id,
            user_goal=request.utterance,
            focus_evidence_ids=tuple(i.evidence_id for i in request.evidence_added),
        )


async def _allow(utterance: str, context: RuntimeContext) -> InputGuardrailVerdict:
    del utterance, context
    return InputGuardrailVerdict(status=InputGuardrailStatus.ALLOWED, reason_code="ok")


ANSWER_1 = (
    "Yes. Aegean made three booked transfers on consecutive business days totaling EUR 29,000 "
    "[t_85][t_86][t_88]."
)
ANSWER_2 = "The first transfer was EUR 9,500 [t_85]."


def _responses() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                _tool_call("search_evidence", {"intent": {"question": "q", "objective": "o"}}, "c1")
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                _tool_call("query_records", {"intent": {"question": "q", "objective": "o"}}, "c2"),
                _tool_call(
                    "find_connections", {"intent": {"question": "q", "objective": "o"}}, "c3"
                ),
            ],
        ),
        AIMessage(content="", tool_calls=[_draft("c4", ANSWER_1, "t_85", "t_86", "t_88")]),
    ]


@pytest.mark.asyncio
async def test_scripted_turns_survive_interruption_and_replay_byte_identically(
    database_pools: DatabasePools,
) -> None:
    saver = create_checkpointer(database_pools.writer)
    model = ScriptedChatModel(responses=_responses(), seen=[])
    tools = Tools()
    components = AgentComponents(
        model=model,
        tools=tools.build(),
        guardrail=_allow,
        evidence_guard=None,
        verifier=Verifier(),
        closure=None,
        projection_model=Projection(),
        retry_policy=POLICY,
        transient_errors=(TimeoutError,),
    )
    limits = AgentLimits(
        main_model_call_limit=8,
        main_tool_call_limit=6,
        closure_model_calls=2,
        max_context_tokens=4_000,
        max_answer_chars=4_000,
        max_evidence_cards=50,
        max_history_turns=10,
    )
    agent = build_investigation_agent(components, limits=limits, checkpointer=saver)
    locks = ThreadLockRegistry()
    policy = InvocationPolicy(
        policy_version="e2e", max_message_chars=4_000, turn_timeout_s=30, max_history_turns=10
    )
    invoke = InvokeTurn(graph=agent, locks=locks, policy=policy, clock=lambda: datetime.now(UTC))
    history = ReadHistory(
        graph=agent,
        checkpointer=cast(CheckpointReader, saver),
        locks=locks,
        cursors=CursorCodec(),
        policy=HistoryReadPolicy(default_page_size=2, max_page_size=3),
    )
    deleter = DeleteThread(graph=agent, checkpointer=saver, locks=locks)
    thread = f"e2e-{datetime.now(UTC).timestamp():.0f}"

    async def turn(request_id: str, message: str) -> list[dict[str, Any]]:
        prepared = await invoke.prepare(
            InvokeRequest(request_id=request_id, thread_id=thread, message=message)
        )
        return [json.loads(e["data"]) async for e in stream_prepared_turn(prepared, chunk_chars=16)]

    interrupted = await invoke.prepare(
        InvokeRequest(
            request_id="r1",
            thread_id=thread,
            message="Did Aegean make three booked consecutive-business-day transfers totaling EUR 29,000?",
        )
    )
    stream = cast(
        AsyncGenerator[dict[str, str]],
        stream_prepared_turn(interrupted, chunk_chars=16),
    )
    async for _event in stream:
        if len(tools.calls) == 3:
            break
    await stream.aclose()
    assert sorted(tools.calls) == ["find_connections", "query_records", "search_evidence"]
    page = await history.read_messages(thread_id=thread, page_size=3)
    assert [(m.role, m.turn_status) for m in page.items] == [
        (HistoryRole.USER, TurnStatus.INTERRUPTED)
    ]

    prepared = await invoke.prepare(
        InvokeRequest(
            request_id="r1",
            thread_id=thread,
            message="Did Aegean make three booked consecutive-business-day transfers totaling EUR 29,000?",
        )
    )
    assert prepared.kind is PreparedTurnKind.RESUME
    resumed = [json.loads(e["data"]) async for e in stream_prepared_turn(prepared, chunk_chars=16)]
    assert resumed[-1]["event"] == "run.completed"
    assert sorted(tools.calls) == ["find_connections", "query_records", "search_evidence"]
    streamed = "".join(e["data"]["text"] for e in resumed if e["event"] == "answer.delta")
    assert streamed == ANSWER_1
    assert {c["evidence_id"] for c in resumed[-1]["data"]["citations"]} == {
        "t_85",
        "t_86",
        "t_88",
    }
    calls_after_resume = model.calls

    replay = await turn(
        "r1", "Did Aegean make three booked consecutive-business-day transfers totaling EUR 29,000?"
    )
    assert [e["event"] for e in replay if e["event"] != "answer.delta"] == [
        "run.started",
        "run.completed",
    ]
    assert "".join(e["data"]["text"] for e in replay if e["event"] == "answer.delta") == streamed
    assert model.calls == calls_after_resume

    second = await turn("r2", "What was the first transfer amount?")
    assert second[-1]["event"] == "run.completed"
    assert "".join(e["data"]["text"] for e in second if e["event"] == "answer.delta") == ANSWER_2
    first_second_call = model.seen[-1]
    assert isinstance(first_second_call[0], SystemMessage) and len(first_second_call) == 2
    assert (
        "Did Aegean make three" in first_second_call[0].content
        and "t_85" in first_second_call[0].content
    )

    pages: list[Any] = []
    cursor = None
    while True:
        page = await history.read_messages(thread_id=thread, cursor=cursor, page_size=2)
        pages.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert [(m.role, m.request_id, m.turn_status) for m in pages] == [
        (HistoryRole.USER, "r1", TurnStatus.COMPLETED),
        (HistoryRole.ASSISTANT, "r1", TurnStatus.COMPLETED),
        (HistoryRole.USER, "r2", TurnStatus.COMPLETED),
        (HistoryRole.ASSISTANT, "r2", TurnStatus.COMPLETED),
    ]
    assert len({m.message_id for m in pages}) == 4
    threads = await history.list_threads(page_size=50)
    assert any(t.thread_id == thread and t.status is TurnStatus.COMPLETED for t in threads.items)

    await deleter.delete(thread)
    with pytest.raises(ThreadNotFound):
        await history.read_messages(thread_id=thread)
    fresh = await invoke.prepare(InvokeRequest(request_id="r1", thread_id=thread, message="new"))
    assert fresh.kind is PreparedTurnKind.NEW and fresh.graph_input is not None
    assert fresh.graph_input["control"]["policy_version"] == "e2e"
    await fresh.close()
