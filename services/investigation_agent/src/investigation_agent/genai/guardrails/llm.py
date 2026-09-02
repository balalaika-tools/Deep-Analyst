"""Structured model adapters for input and evidence guardrails."""

from __future__ import annotations

from typing import Any

from investigation_agent.core.context import RuntimeContext
from investigation_agent.genai.guardrails.middleware import (
    evaluate_input_guardrail,
    guard_evidence_batch,
)
from investigation_agent.genai.guardrails.prompts import (
    EVIDENCE_GUARDRAIL_PROMPT,
    INPUT_GUARDRAIL_PROMPT,
)
from investigation_agent.genai.guardrails.schemas import (
    EvidenceGuardrailVerdict,
    GuardedEvidenceBatch,
    InputGuardrailVerdict,
)
from investigation_agent.genai.shared.retries import RetryPolicy, TransientExhaustedError
from investigation_agent.genai.shared.structured import (
    StructuredChat,
    StructuredRunner,
    cancellation_token,
    loop_deadline,
)


class InputGuardrailRunner:
    """No-tool structured input guard with bounded physical retries."""

    def __init__(
        self,
        model: Any,
        *,
        policy: RetryPolicy,
        transient_errors: tuple[type[Exception], ...],
    ) -> None:
        self._chat = StructuredChat(model, InputGuardrailVerdict, INPUT_GUARDRAIL_PROMPT)
        self._policy = policy
        self._transient_errors = transient_errors

    async def __call__(self, utterance: str, context: RuntimeContext) -> InputGuardrailVerdict:
        async def invoke(value: str) -> InputGuardrailVerdict:
            return InputGuardrailVerdict.model_validate(
                await self._chat.invoke({"utterance": value})
            )

        return await evaluate_input_guardrail(
            utterance,
            model=invoke,
            retry_policy=self._policy,
            transient_errors=self._transient_errors,
            cancellation=cancellation_token(context),
            deadline=loop_deadline(context),
        )


class EvidenceGuardrailRunner:
    """Batched evidence guard; deterministic fallback remains middleware-owned."""

    def __init__(
        self,
        model: Any,
        *,
        policy: RetryPolicy,
        transient_errors: tuple[type[Exception], ...],
    ) -> None:
        self._runner = StructuredRunner(
            model,
            EvidenceGuardrailVerdict,
            EVIDENCE_GUARDRAIL_PROMPT,
            retry_policy=policy,
            transient_errors=transient_errors,
        )

    async def __call__(
        self, items: tuple[Any, ...], context: RuntimeContext
    ) -> GuardedEvidenceBatch:
        attempts = 0

        async def invoke(values: tuple[tuple[str, str], ...]) -> EvidenceGuardrailVerdict:
            nonlocal attempts
            result = await self._runner.run({"items": values}, context=context)
            attempts = result.attempts
            return result.value

        try:
            normalized = await guard_evidence_batch(items, model=invoke)
        except TransientExhaustedError as exc:
            raise TransientExhaustedError(
                "evidence guardrail unavailable", attempts=exc.attempts
            ) from exc
        return GuardedEvidenceBatch(
            items=normalized,
            model_calls=attempts,
            physical_attempts=attempts,
        )


__all__ = ["EvidenceGuardrailRunner", "InputGuardrailRunner"]
