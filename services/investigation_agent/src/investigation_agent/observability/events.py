"""Bounded signal names and aggregate instruments for an investigation attempt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from opentelemetry.metrics import Counter, Histogram, Meter

WORKFLOW_NAME: Final = "investigation_turn"
AGENT_NAME: Final = "investigation_agent"
ATTEMPT_SPAN_NAME: Final = "invoke_workflow investigation_turn"

FailureClass = Literal[
    "validation",
    "authorization",
    "conflict",
    "policy",
    "no_support",
    "transient_exhaustion",
    "budget",
    "cancelled",
    "dependency",
    "incompatible_state",
    "internal",
]


class LogEvent:
    REQUEST_FAILED: Final = "investigation.request_failed"
    ATTEMPT_COMPLETED: Final = "investigation.attempt_completed"
    ATTEMPT_FAILED: Final = "investigation.attempt_failed"
    ATTEMPT_CANCELLED: Final = "agent_invocation_cancelled"


class Outcome:
    SUCCESS: Final = "success"
    ERROR: Final = "error"
    REFUSED: Final = "refused"
    BUDGET_EXHAUSTED: Final = "budget_exhausted"
    CANCELLED: Final = "cancelled"


_RESULT_KINDS: Final = frozenset({"evidence", "rows", "paths", "findings"})


def bounded_result_kind(kind: str) -> str:
    """Keep model- or adapter-originated result labels in a closed metric set."""
    return kind if kind in _RESULT_KINDS else "other"


@dataclass(frozen=True, slots=True)
class AttemptMeasurements:
    """One immutable aggregate emitted exactly once when an attempt closes."""

    duration_s: float
    outcome: str
    error_type: FailureClass | Literal["CancelledError"] | None
    first_safe_progress_s: float | None
    answer_ready_s: float | None
    first_public_delta_s: float | None
    api_first_public_delta_s: float | None
    model_calls: int
    tool_calls: int
    model_retries: int
    tool_retries: int
    input_tokens: int | None
    output_tokens: int | None
    result_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class InvestigationInstruments:
    """Standard GenAI and product aggregate instruments used by this service."""

    workflow_duration: Histogram
    agent_duration: Histogram
    agent_inference_calls: Histogram
    agent_tool_calls: Histogram
    model_time_to_first_chunk: Histogram
    tool_duration: Histogram
    api_time_to_first_chunk: Histogram
    agent_time_to_first_chunk: Histogram
    first_safe_progress: Histogram
    answer_ready: Histogram
    completion: Histogram
    model_retries: Histogram
    tool_retries: Histogram
    token_usage: Histogram
    result_count: Histogram
    cancellations: Counter

    @classmethod
    def create(cls, meter: Meter) -> InvestigationInstruments:
        return cls(
            workflow_duration=meter.create_histogram(
                "gen_ai.invoke_workflow.duration",
                unit="s",
                description="Duration of one investigation workflow attempt.",
            ),
            agent_duration=meter.create_histogram(
                "gen_ai.invoke_agent.duration",
                unit="s",
                description="Duration of one investigation-agent invocation.",
            ),
            agent_inference_calls=meter.create_histogram(
                "gen_ai.invoke_agent.inference_calls",
                unit="{inference_call}",
                description="Physical model calls in one investigation invocation.",
            ),
            agent_tool_calls=meter.create_histogram(
                "gen_ai.invoke_agent.tool_calls",
                unit="{tool_call}",
                description="Physical tool calls in one investigation invocation.",
            ),
            model_time_to_first_chunk=meter.create_histogram(
                "gen_ai.client.operation.time_to_first_chunk",
                unit="s",
                description="Time to first chunk from one physical model request.",
            ),
            tool_duration=meter.create_histogram(
                "gen_ai.execute_tool.duration",
                unit="s",
                description="Duration of one physical tool execution.",
            ),
            api_time_to_first_chunk=meter.create_histogram(
                "app.investigation.api.time_to_first_chunk",
                unit="s",
                description="API request start to first committed public delta.",
            ),
            agent_time_to_first_chunk=meter.create_histogram(
                "app.agent.time_to_first_chunk",
                unit="s",
                description="Investigation invocation to first committed public delta.",
            ),
            first_safe_progress=meter.create_histogram(
                "app.investigation.time_to_first_safe_progress",
                unit="s",
                description="Attempt start to first safe progress event.",
            ),
            answer_ready=meter.create_histogram(
                "app.investigation.answer_ready.duration",
                unit="s",
                description="Attempt start to a validated and durable answer.",
            ),
            completion=meter.create_histogram(
                "app.investigation.completion.duration",
                unit="s",
                description="Attempt start to terminal completion.",
            ),
            model_retries=meter.create_histogram(
                "app.investigation.model.retries",
                unit="{retry}",
                description="Physical model retries in one attempt.",
            ),
            tool_retries=meter.create_histogram(
                "app.investigation.tool.retries",
                unit="{retry}",
                description="Physical tool retries in one attempt.",
            ),
            token_usage=meter.create_histogram(
                "app.investigation.token.usage",
                unit="{token}",
                description="Aggregate provider-reported tokens in one attempt.",
            ),
            result_count=meter.create_histogram(
                "app.investigation.result_count",
                unit="{result}",
                description="Bounded result counts produced in one attempt.",
            ),
            cancellations=meter.create_counter(
                "app.agent.cancellations",
                unit="{cancellation}",
                description="Observed investigation-agent cancellations.",
            ),
        )

    def record_model_time_to_first_chunk(
        self,
        duration_s: float,
        *,
        provider: str,
        request_model: str | None,
    ) -> None:
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
        }
        if request_model:
            attributes["gen_ai.request.model"] = request_model
        self.model_time_to_first_chunk.record(duration_s, attributes)

    def record_tool_execution(
        self,
        duration_s: float,
        *,
        tool_name: str,
        error_type: str | None,
    ) -> None:
        attributes = {
            "gen_ai.tool.name": tool_name,
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.tool.type": "function",
        }
        if error_type:
            attributes["error.type"] = error_type
        self.tool_duration.record(duration_s, attributes)

    def record_attempt(self, measurements: AttemptMeasurements) -> None:
        """Publish one bounded aggregate; no correlation identifier is a label."""
        workflow = {"gen_ai.workflow.name": WORKFLOW_NAME}
        agent = {"gen_ai.agent.name": AGENT_NAME}
        failure = {"error.type": measurements.error_type} if measurements.error_type else {}
        self.workflow_duration.record(
            measurements.duration_s,
            {**workflow, **failure},
        )
        self.agent_duration.record(measurements.duration_s, {**agent, **failure})
        self.agent_inference_calls.record(measurements.model_calls, agent)
        self.agent_tool_calls.record(measurements.tool_calls, agent)

        product = {"workflow": WORKFLOW_NAME, "outcome": measurements.outcome}
        self.completion.record(measurements.duration_s, product)
        self.model_retries.record(measurements.model_retries, product)
        self.tool_retries.record(measurements.tool_retries, product)
        if measurements.first_safe_progress_s is not None:
            self.first_safe_progress.record(measurements.first_safe_progress_s, product)
        if measurements.answer_ready_s is not None:
            self.answer_ready.record(measurements.answer_ready_s, product)
        if measurements.first_public_delta_s is not None:
            self.agent_time_to_first_chunk.record(
                measurements.first_public_delta_s,
                agent,
            )
        if measurements.api_first_public_delta_s is not None:
            self.api_time_to_first_chunk.record(
                measurements.api_first_public_delta_s,
                product,
            )
        for token_type, count in (
            ("input", measurements.input_tokens),
            ("output", measurements.output_tokens),
        ):
            if count is not None:
                self.token_usage.record(
                    count,
                    {**product, "gen_ai.token.type": token_type},
                )
        for kind, count in measurements.result_counts.items():
            self.result_count.record(
                count,
                {**product, "result.kind": bounded_result_kind(kind)},
            )
        if measurements.outcome == Outcome.CANCELLED:
            self.cancellations.add(1, {"gen_ai.agent.name": AGENT_NAME})
