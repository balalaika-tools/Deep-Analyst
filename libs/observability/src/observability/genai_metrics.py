"""Standard GenAI client instruments, their histogram boundaries, and token usage.

Checked against the OpenTelemetry GenAI semantic conventions (2026-08-21 revision).
This module has no LangChain dependency; `langchain.py` is its only in-library caller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from opentelemetry.metrics import Histogram, Meter
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.trace import Span

GENAI_OPERATION_NAME = "gen_ai.operation.name"
GENAI_PROVIDER_NAME = "gen_ai.provider.name"
GENAI_REQUEST_MODEL = "gen_ai.request.model"
GENAI_RESPONSE_MODEL = "gen_ai.response.model"
GENAI_RESPONSE_ID = "gen_ai.response.id"
GENAI_FINISH_REASONS = "gen_ai.response.finish_reasons"
GENAI_OUTPUT_TYPE = "gen_ai.output.type"
GENAI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GENAI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GENAI_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
GENAI_CACHE_WRITE_INPUT_TOKENS = "gen_ai.usage.cache_write.input_tokens"
GENAI_INPUT_MESSAGES = "gen_ai.input.messages"
GENAI_OUTPUT_MESSAGES = "gen_ai.output.messages"
GENAI_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"
GENAI_TOKEN_TYPE = "gen_ai.token.type"
GENAI_AGENT_NAME = "gen_ai.agent.name"
APP_INPUT_TOKEN_DETAILS = "app.gen_ai.usage.input_token_details"
APP_OUTPUT_TOKEN_DETAILS = "app.gen_ai.usage.output_token_details"
APP_OBSERVATION_INPUT = "app.gen_ai.observation.input"
APP_OBSERVATION_OUTPUT = "app.gen_ai.observation.output"

# Every attribute that may carry prompt or completion content; gated by capture.
GENAI_CONTENT_ATTRIBUTES: frozenset[str] = frozenset(
    {
        GENAI_SYSTEM_INSTRUCTIONS,
        GENAI_INPUT_MESSAGES,
        GENAI_OUTPUT_MESSAGES,
        "gen_ai.tool.definitions",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        APP_OBSERVATION_INPUT,
        APP_OBSERVATION_OUTPUT,
    }
)

GENAI_CLIENT_DURATION_BUCKETS = (
    0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92,
)  # fmt: skip
GENAI_TOKEN_BUCKETS = (
    1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864,
)  # fmt: skip


def genai_metric_views() -> list[View]:
    """Convention boundaries; creating a histogram does not configure its buckets."""
    return [
        View(
            instrument_name="gen_ai.client.operation.duration",
            aggregation=ExplicitBucketHistogramAggregation(GENAI_CLIENT_DURATION_BUCKETS),
        ),
        View(
            instrument_name="gen_ai.client.token.usage",
            aggregation=ExplicitBucketHistogramAggregation(GENAI_TOKEN_BUCKETS),
        ),
    ]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized usage; every field optional because providers differ."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    input_details: dict[str, Any] | None = None
    output_details: dict[str, Any] | None = None

    @classmethod
    def from_usage_metadata(cls, usage: Any) -> TokenUsage | None:
        """LangChain `usage_metadata` (or an equivalent mapping) to the normalized shape."""
        if not isinstance(usage, dict) or not usage:
            return None
        input_details = usage.get("input_token_details") or None
        output_details = usage.get("output_token_details") or None
        cache_write = None
        if input_details:
            cache_write = input_details.get("cache_write")
            if cache_write is None:
                # LangChain still calls a cache write `cache_creation`.
                cache_write = input_details.get("cache_creation")
        return cls(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=input_details.get("cache_read") if input_details else None,
            cache_write_tokens=cache_write,
            input_details=dict(input_details) if input_details else None,
            output_details=dict(output_details) if output_details else None,
        )


def set_usage_attributes(span: Span, usage: TokenUsage | None) -> None:
    if usage is None:
        return
    for attribute, value in (
        (GENAI_INPUT_TOKENS, usage.input_tokens),
        (GENAI_OUTPUT_TOKENS, usage.output_tokens),
        (GENAI_CACHE_READ_INPUT_TOKENS, usage.cache_read_tokens),
        (GENAI_CACHE_WRITE_INPUT_TOKENS, usage.cache_write_tokens),
    ):
        if value is not None:
            span.set_attribute(attribute, value)
    if usage.input_details:
        span.set_attribute(APP_INPUT_TOKEN_DETAILS, json.dumps(usage.input_details, default=str))
    if usage.output_details:
        span.set_attribute(APP_OUTPUT_TOKEN_DETAILS, json.dumps(usage.output_details, default=str))


@dataclass(slots=True)
class GenAIInstruments:
    operation_duration: Histogram
    token_usage: Histogram

    @classmethod
    def create(cls, meter: Meter) -> GenAIInstruments:
        return cls(
            operation_duration=meter.create_histogram(
                "gen_ai.client.operation.duration",
                unit="s",
                description="Duration of one GenAI client operation.",
            ),
            token_usage=meter.create_histogram(
                "gen_ai.client.token.usage",
                unit="{token}",
                description="Tokens used by a GenAI client operation.",
            ),
        )

    def record_operation(
        self,
        *,
        duration_s: float,
        operation: str,
        provider: str,
        request_model: str | None,
        response_model: str | None = None,
        usage: TokenUsage | None = None,
        error_type: str | None = None,
    ) -> None:
        """One observation per physical request, on success and failure alike."""
        attributes: dict[str, str] = {
            GENAI_OPERATION_NAME: operation,
            GENAI_PROVIDER_NAME: provider,
        }
        if request_model:
            attributes[GENAI_REQUEST_MODEL] = request_model
        if response_model:
            attributes[GENAI_RESPONSE_MODEL] = response_model
        duration_attributes = dict(attributes)
        if error_type:
            duration_attributes["error.type"] = error_type
        self.operation_duration.record(duration_s, duration_attributes)
        if usage is None:
            return
        for token_type, value in (("input", usage.input_tokens), ("output", usage.output_tokens)):
            if value is not None:
                self.token_usage.record(value, {**attributes, GENAI_TOKEN_TYPE: token_type})
