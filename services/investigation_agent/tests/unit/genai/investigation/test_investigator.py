from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from investigation_agent.application.invoke_turn import CancellationController
from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import ControlState, InvestigationState
from investigation_agent.genai.investigation.investigator import LangGraphInvestigator
from investigation_agent.ports.investigator import InvestigationError


@dataclass(frozen=True)
class Snapshot:
    values: Mapping[str, Any]


@dataclass
class Harness:
    values: Mapping[str, Any] = field(
        default_factory=lambda: InvestigationState(
            control=ControlState(policy_version="policy-v1")
        ).as_update()
    )
    configs: list[Mapping[str, object]] = field(default_factory=list)
    stream_options: list[dict[str, object]] = field(default_factory=list)
    error: Exception | None = None

    async def aget_state(self, config: Mapping[str, object]) -> Snapshot:
        self.configs.append(config)
        if self.error is not None:
            raise self.error
        return Snapshot(self.values)

    async def astream(
        self,
        input: Mapping[str, Any] | None,
        config: Mapping[str, object],
        **options: object,
    ) -> AsyncIterator[object]:
        del input
        self.configs.append(config)
        self.stream_options.append(options)
        yield {"type": "custom", "data": {"phase": "started"}}


@pytest.mark.asyncio
async def test_adapter_owns_langgraph_configuration_and_returns_domain_state() -> None:
    harness = Harness()
    investigator = LangGraphInvestigator(harness)

    state = await investigator.load_state("thread-1")

    assert state is not None and state.control.policy_version == "policy-v1"
    assert harness.configs == [
        {
            "configurable": {"thread_id": "thread-1"},
            "metadata": {"app": "investigation", "public_thread_id": "thread-1"},
            "recursion_limit": 200,
        }
    ]


@pytest.mark.asyncio
async def test_adapter_hides_stream_controls_from_the_application_port() -> None:
    harness = Harness()
    investigator = LangGraphInvestigator(harness)
    context = RuntimeContext(
        thread_id="thread-1",
        request_id="request-1",
        deadline=datetime(2026, 1, 1, tzinfo=UTC),
        cancellation=CancellationController.create(),
    )

    events = [
        event
        async for event in investigator.stream_turn(
            {}, thread_id="thread-1", context=context, max_steps=42
        )
    ]

    assert events == [{"type": "custom", "data": {"phase": "started"}}]
    assert harness.configs[0]["recursion_limit"] == 42
    assert harness.stream_options[0] == {
        "context": context,
        "stream_mode": ["updates", "custom"],
        "durability": "sync",
        "version": "v2",
    }


@pytest.mark.asyncio
async def test_adapter_contains_framework_failure_details() -> None:
    investigator = LangGraphInvestigator(Harness(error=ConnectionError("private endpoint")))

    with pytest.raises(InvestigationError) as captured:
        await investigator.load_state("thread-1")

    assert "private endpoint" not in str(captured.value)
