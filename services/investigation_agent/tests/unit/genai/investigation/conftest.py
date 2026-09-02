"""Scripted models and fake tools for exercising the assembled investigation agent."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from evidence_model import FieldLocator, SourceRef
from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.history import stable_message_id, stable_turn_id
from investigation_agent.domain.investigation_state import (
    ControlState,
    InvestigationState,
    WorkingProjection,
    new_turn_state,
    parse_state,
    state_update,
)
from investigation_agent.domain.tool_outcome import (
    BudgetConsumption,
    EvidenceField,
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
from investigation_agent.genai.shared.retries import AttemptResult, CancellationToken, RetryPolicy
from investigation_agent.genai.state_projection.schemas import ProjectionInput
from langchain.tools import ToolRuntime
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver

NOW = datetime(2026, 5, 6, 7, 8, tzinfo=UTC)
CASE = "case-1"
POLICY = RetryPolicy(
    max_attempts=2, initial_delay_s=0, backoff_factor=1, max_delay_s=0, jitter=False
)


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    failures: int = 0
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
        if self.calls <= self.failures:
            raise TimeoutError("transient provider failure")
        index = min(self.calls - self.failures - 1, len(self.responses) - 1)
        template = self.responses[index]
        # Real providers mint fresh message and tool-call IDs per call; the reducer keys on them.
        message = AIMessage(
            content=template.content,
            tool_calls=[
                {**call, "id": f"{call['id']}-{self.calls}"} for call in template.tool_calls
            ],
            id=f"ai-{self.calls}",
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_call(name: str, args: Mapping[str, Any], identifier: str) -> dict[str, Any]:
    return {"name": name, "args": dict(args), "id": identifier, "type": "tool_call"}


def draft_call(
    identifier: str, *, answer: str, claims: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return tool_call("AnswerDraft", {"answer": answer, "claims": list(claims)}, identifier)


def claim(claim_id: str, text: str, *evidence_ids: str, kind: str = "verified") -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "text": text,
        "kind": kind,
        "material": True,
        "evidence_ids": list(evidence_ids),
    }


def evidence(
    evidence_id: str,
    *,
    kind: str = "chunk",
    content: str = "Transfer of 50 to account 77",
    status: str = "verified",
    case_id: str = CASE,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        case_id=case_id,
        content_hash=canonical_fingerprint(content),
        source_refs=(
            SourceRef(record_id=f"record-{evidence_id}", locator=FieldLocator(field="text")),
        ),
        content=content if kind != "row" else None,
        fields=(EvidenceField(name="amount", value=50),) if kind == "row" else (),
        evidentiary_status=status,
    )


@dataclass
class FakeToolBehaviour:
    """Per-tool scripted outcomes; each call pops the next outcome."""

    outcomes: dict[str, list[ToolOutcome | Exception]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    contexts: list[RuntimeContext] = field(default_factory=list)

    def next(self, name: str, args: dict[str, Any], context: RuntimeContext) -> ToolOutcome:
        self.calls.append((name, args))
        self.contexts.append(context)
        queue = self.outcomes.get(name) or []
        item = queue.pop(0) if queue else outcome(name, status=OutcomeStatus.NO_SUPPORT)
        if isinstance(item, Exception):
            raise item
        return item


def outcome(
    tool_name: str,
    *,
    status: OutcomeStatus = OutcomeStatus.SUFFICIENT,
    items: Sequence[EvidenceItem] = (),
    case_id: str = CASE,
    warnings: Sequence[str] = (),
) -> ToolOutcome:
    return ToolOutcome(
        call_id=f"{tool_name}-call",
        intent_fingerprint="a" * 64,
        tool=tool_name,
        case_id=case_id,
        status=status,
        evidence=tuple(items),
        warnings=tuple(warnings),
        consumption=BudgetConsumption(model_calls=1, physical_attempts=1, rows=len(items)),
    )


def fake_tools(behaviour: FakeToolBehaviour) -> list[BaseTool]:
    def make(name: str, doc: str) -> BaseTool:
        @tool(name, response_format="content_and_artifact", description=doc)
        async def implementation(
            intent: dict[str, Any], runtime: ToolRuntime[RuntimeContext, Any]
        ) -> tuple[str, ToolOutcome]:
            runtime.context.check_active()
            result = behaviour.next(name, intent, runtime.context)
            runtime.stream_writer({"phase": "searching_evidence", "tool": name, "attempt": 1})
            return result.status.value, result

        return implementation

    return [
        make("search_evidence", "search"),
        make("query_records", "query"),
        make("find_connections", "graph"),
    ]


class FakeStructuredRunner:
    def __init__(self, results: Sequence[Any]) -> None:
        self._results = list(results)
        self.calls: list[Any] = []

    async def run(self, payload: Any, *, context: RuntimeContext) -> AttemptResult[Any]:
        del context
        self.calls.append(payload)
        item = self._results.pop(0) if self._results else self._results_default()
        if isinstance(item, Exception):
            raise item
        return AttemptResult(value=item, attempts=1)

    @staticmethod
    def _results_default() -> Any:
        raise AssertionError("no scripted structured result remains")


def entailed(*claim_ids: str, supported: bool = True) -> GroundingVerdict:
    return GroundingVerdict(
        claims=tuple(
            {
                "claim_id": item,
                "supported": supported,
                "safe_reason_code": "entailed" if supported else "not_entailed",
            }
            for item in claim_ids
        )
    )


class FakeProjectionModel:
    def __init__(
        self, factory: Callable[[ProjectionInput, tuple[str, ...]], WorkingProjection] | None = None
    ) -> None:
        self.calls: list[ProjectionInput] = []
        self._factory = factory or (
            lambda request, _violations: WorkingProjection(
                source_turn_id=request.source_turn_id,
                user_goal=request.utterance,
                dialogue_summary=f"{request.outcome}: {request.answer or request.failure_code}",
                focus_evidence_ids=tuple(item.evidence_id for item in request.evidence_added),
            )
        )

    async def __call__(
        self, request: ProjectionInput, *, repair_violations: tuple[str, ...] = ()
    ) -> WorkingProjection:
        self.calls.append(request)
        return self._factory(request, repair_violations)


def allow_all() -> Callable[[str, RuntimeContext], Any]:
    async def evaluate(utterance: str, context: RuntimeContext) -> InputGuardrailVerdict:
        del utterance, context
        return InputGuardrailVerdict(status=InputGuardrailStatus.ALLOWED, reason_code="ok")

    return evaluate


def limits(**overrides: int) -> AgentLimits:
    values = {
        "main_model_call_limit": 8,
        "main_tool_call_limit": 6,
        "closure_model_calls": 2,
        "max_context_tokens": 4_000,
        "max_answer_chars": 4_000,
        "max_evidence_cards": 50,
        "max_history_turns": 10,
    }
    values.update(overrides)
    return AgentLimits(**values)


@dataclass
class Harness:
    model: ScriptedChatModel
    behaviour: FakeToolBehaviour
    verifier: FakeStructuredRunner
    closure: FakeStructuredRunner
    projection: FakeProjectionModel
    saver: InMemorySaver
    agent: Any
    cancellation: CancellationToken = field(default_factory=CancellationToken.create)

    def context(self, *, thread_id: str, request_id: str, seconds: float = 30) -> RuntimeContext:
        return RuntimeContext(
            case_id=CASE,
            thread_id=thread_id,
            request_id=request_id,
            deadline=datetime.now(UTC) + timedelta(seconds=seconds),
            cancellation=self.cancellation,
        )

    def config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}

    async def existing(self, thread_id: str) -> InvestigationState | None:
        snapshot = await self.agent.aget_state(self.config(thread_id))
        return parse_state(snapshot.values)

    def new_turn_input(
        self, *, thread_id: str, request_id: str, message: str, existing: InvestigationState | None
    ) -> dict[str, Any]:
        turn_id = stable_turn_id(thread_id, request_id)
        turn = new_turn_state(
            turn_id=turn_id,
            request_id=request_id,
            message_id=stable_message_id(turn_id),
            utterance=message,
            case_id=CASE,
            opened_at=NOW,
        )
        payload: dict[str, Any] = {
            "messages": [HumanMessage(content=message, id=turn.user_message_id)]
        }
        if existing is None:
            payload.update(
                state_update(control=ControlState(case_id=CASE, policy_version="v1"), turn=turn)
            )
        else:
            payload.update(state_update(turn=turn))
        return payload

    async def run_turn(
        self,
        *,
        thread_id: str = "thread-1",
        request_id: str = "request-1",
        message: str = "Trace the transfer to account 77",
        resume: bool = False,
        seconds: float = 30,
    ) -> tuple[InvestigationState, list[dict[str, Any]]]:
        existing = await self.existing(thread_id)
        graph_input = (
            None
            if resume
            else self.new_turn_input(
                thread_id=thread_id, request_id=request_id, message=message, existing=existing
            )
        )
        events: list[dict[str, Any]] = []
        async for part in self.agent.astream(
            graph_input,
            self.config(thread_id),
            context=self.context(thread_id=thread_id, request_id=request_id, seconds=seconds),
            stream_mode=["updates", "custom"],
            durability="sync",
            version="v2",
        ):
            events.append(part)
        state = await self.existing(thread_id)
        assert state is not None
        return state, events


def build_harness(
    responses: Sequence[AIMessage],
    *,
    behaviour: FakeToolBehaviour | None = None,
    verifier_results: Sequence[Any] = (),
    closure_results: Sequence[Any] = (),
    projection: FakeProjectionModel | None = None,
    guardrail: Callable[[str, RuntimeContext], Any] | None = None,
    agent_limits: AgentLimits | None = None,
    failures: int = 0,
    saver: InMemorySaver | None = None,
    interrupt_after: Sequence[str] = (),
) -> Harness:
    model = ScriptedChatModel(responses=list(responses), failures=failures, seen=[])
    behaviour = behaviour or FakeToolBehaviour()
    verifier = FakeStructuredRunner(verifier_results)
    closure = FakeStructuredRunner(closure_results)
    projection = projection or FakeProjectionModel()
    saver = saver or InMemorySaver()
    components = AgentComponents(
        model=model,
        tools=fake_tools(behaviour),
        guardrail=guardrail or allow_all(),
        evidence_guard=None,
        verifier=verifier,
        closure=closure,
        projection_model=projection,
        retry_policy=POLICY,
        transient_errors=(TimeoutError,),
    )
    agent = build_investigation_agent(
        components, limits=agent_limits or limits(), checkpointer=saver
    )
    if interrupt_after:
        agent = build_investigation_agent(
            components, limits=agent_limits or limits(), checkpointer=saver
        )
    return Harness(
        model=model,
        behaviour=behaviour,
        verifier=verifier,
        closure=closure,
        projection=projection,
        saver=saver,
        agent=agent,
    )


def wait_for(predicate: Callable[[], bool], *, timeout: float = 1.0) -> Any:
    async def _wait() -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not predicate():
            if loop.time() > end:
                raise TimeoutError("condition not met")
            await asyncio.sleep(0.01)

    return _wait()


@pytest.fixture
def support() -> Any:
    """Expose this module's scripted harness to tests under importlib import mode."""

    return sys.modules[__name__]
