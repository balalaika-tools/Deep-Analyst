"""Input guardrail hook and the uniform untrusted-evidence boundary."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langgraph.runtime import Runtime

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import (
    InvestigationState,
    TurnState,
    parse_state,
    state_update,
)
from investigation_agent.domain.tool_outcome import EvidenceItem
from investigation_agent.genai.guardrails.schemas import (
    EvidenceGuardrailVerdict,
    InputGuardrailStatus,
    InputGuardrailVerdict,
    NormalizedEvidence,
)
from investigation_agent.genai.shared.retries import (
    CancellationToken,
    RetryPolicy,
    TransientExhaustedError,
    retry_async,
)
from investigation_agent.observability.instrumentation import phase_span

REFUSAL_TEXT = {
    InputGuardrailStatus.PROMPT_INJECTION: (
        "I can only help with investigation questions about this case, and I cannot change my "
        "instructions or reveal hidden configuration."
    ),
    InputGuardrailStatus.OFF_TOPIC: (
        "I can only help with investigation questions about this case."
    ),
}
GUARDRAIL_UNAVAILABLE_TEXT = (
    "The request could not be checked by the safety filter right now. Please try again."
)


type InputGuardrailModel = Callable[[str], Awaitable[InputGuardrailVerdict]]
type EvidenceGuardrailModel = Callable[
    [tuple[tuple[str, str], ...]], Awaitable[EvidenceGuardrailVerdict]
]


class GuardrailUnavailableError(RuntimeError):
    code = "guardrail_unavailable"

    def __init__(self, message: str, *, attempts: int = 0) -> None:
        self.attempts = attempts
        super().__init__(message)


_INSTRUCTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\b(?:ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\b",
        r"^\s*system\s*:",
        r"\breport\s+no\s+findings\b",
        r"\b(system|developer)\s+prompt\b",
        r"\breveal\s+(?:your|the)\s+(?:hidden|secret|system)\b",
        r"\bcall\s+(?:the\s+)?tool\b",
        r"\boverride\s+(?:authorization|policy|instructions)\b",
        r"\bexecute\s+(?:this\s+)?(?:sql|command|instruction)\b",
    )
)

_GREETING_ONLY = frozenset(
    {
        "greetings",
        "hello",
        "hello there",
        "hey",
        "hey there",
        "hi",
        "sup",
        "what s up",
        "whats up",
        "yo",
        "yoo",
        "yooo",
    }
)


async def evaluate_input_guardrail(
    utterance: str,
    *,
    model: InputGuardrailModel,
    retry_policy: RetryPolicy,
    transient_errors: tuple[type[BaseException], ...],
    cancellation: CancellationToken,
    deadline: float,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    on_attempt: Callable[[int, BaseException | None], None] | None = None,
) -> InputGuardrailVerdict:
    """Fail closed when a schema-valid verdict cannot be obtained within bounds."""

    async def operation(attempt: int) -> InputGuardrailVerdict:
        del attempt
        return InputGuardrailVerdict.model_validate(await model(utterance))

    kwargs: dict[str, Any] = {} if sleep is None else {"sleep": sleep}
    try:
        result = await retry_async(
            operation,
            policy=retry_policy,
            retry_on=transient_errors,
            cancellation=cancellation,
            deadline=deadline,
            on_attempt=on_attempt,
            **kwargs,
        )
    except TransientExhaustedError as exc:
        raise GuardrailUnavailableError(
            "input guardrail unavailable",
            attempts=exc.attempts,
        ) from exc
    verdict = result.value
    if verdict.status is InputGuardrailStatus.INDETERMINATE:
        raise GuardrailUnavailableError(
            "input guardrail returned an indeterminate verdict",
            attempts=result.attempts,
        )
    return verdict


type GuardrailEvaluator = Callable[[str, RuntimeContext], Awaitable[InputGuardrailVerdict]]


class InputGuardrailMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """``before_agent`` hook: a blocked or unavailable verdict ends the run before any tool."""

    def __init__(self, evaluator: GuardrailEvaluator) -> None:
        self._evaluator = evaluator

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self, state: dict[str, Any], runtime: Runtime[RuntimeContext]
    ) -> dict[str, Any] | None:
        parsed = parse_state(state)
        if parsed is None or parsed.turn is None:
            raise RuntimeError("input guardrail requires an open turn")
        turn = parsed.turn
        if turn.answer_kind is not None or turn.guardrail_status == "allowed":
            return None
        runtime.stream_writer({"phase": "checking_scope"})
        if _is_greeting_only(turn.utterance):
            return _refuse(
                parsed,
                turn,
                status=InputGuardrailStatus.OFF_TOPIC.value,
                text=REFUSAL_TEXT[InputGuardrailStatus.OFF_TOPIC],
            )
        try:
            with phase_span("input_guardrail"):
                verdict = await self._evaluator(turn.utterance, runtime.context)
        except GuardrailUnavailableError:
            return _refuse(
                parsed, turn, status="guardrail_unavailable", text=GUARDRAIL_UNAVAILABLE_TEXT
            )
        if verdict.status is InputGuardrailStatus.ALLOWED:
            return state_update(turn=turn.model_copy(update={"guardrail_status": "allowed"}))
        return _refuse(parsed, turn, status=verdict.status.value, text=REFUSAL_TEXT[verdict.status])


def _is_greeting_only(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    words = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()
    return words in _GREETING_ONLY


def _refuse(
    state: InvestigationState, turn: TurnState, *, status: str, text: str
) -> dict[str, Any]:
    del state
    updated = turn.model_copy(
        update={
            "guardrail_status": status,
            "answer_kind": "refusal",
            "pending_answer": text,
            "pending_citations": (),
        }
    )
    return {**state_update(turn=updated), "jump_to": "end"}


def normalize_untrusted_text(value: str) -> str:
    """Normalize model-facing text without changing the exact evidence kept in state."""

    normalized = unicodedata.normalize("NFKC", value).replace("\x00", "�")
    return "".join(
        character for character in normalized if character in "\n\t" or ord(character) >= 32
    )


def looks_instruction_like(value: str) -> bool:
    return any(pattern.search(value) for pattern in _INSTRUCTION_PATTERNS)


async def guard_evidence_batch(
    evidence: tuple[EvidenceItem, ...],
    *,
    model: EvidenceGuardrailModel,
) -> tuple[NormalizedEvidence, ...]:
    """Batch-classify text while preserving IDs/content and denying verdict fabrication."""

    normalized = tuple(
        (item.evidence_id, normalize_untrusted_text(_visible_text(item))) for item in evidence
    )
    verdict = EvidenceGuardrailVerdict.model_validate(await model(normalized))
    supplied_ids = {item.evidence_id for item in evidence}
    returned_ids = [item.evidence_id for item in verdict.items]
    if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != supplied_ids:
        raise ValueError("evidence guardrail verdict IDs do not match the supplied batch")
    by_id = {item.evidence_id: item for item in verdict.items}
    return tuple(
        _normalized_evidence(item, text, by_id[item.evidence_id].suspicious)
        for item, (_, text) in zip(evidence, normalized, strict=True)
    )


def deterministic_evidence_boundary(
    evidence: tuple[EvidenceItem, ...],
) -> tuple[NormalizedEvidence, ...]:
    """Safe deterministic fallback used when the evidence classifier is unavailable."""

    return tuple(
        _normalized_evidence(
            item,
            normalize_untrusted_text(_visible_text(item)),
            looks_instruction_like(_visible_text(item)),
        )
        for item in evidence
    )


def _visible_text(item: EvidenceItem) -> str:
    fields = "\n".join(f"{field.name}: {field.value}" for field in item.fields)
    return "\n".join(part for part in (item.content or "", fields) if part)


def _normalized_evidence(item: EvidenceItem, text: str, suspicious: bool) -> NormalizedEvidence:
    label = "suspicious-untrusted-evidence" if suspicious else "untrusted-evidence"
    rendered = f"<{label} id={item.evidence_id!r}>\n{text}\n</{label}>"
    return NormalizedEvidence(
        evidence_id=item.evidence_id,
        content_hash=item.content_hash,
        rendered=rendered,
        suspicious=suspicious,
        guard_status="flagged" if suspicious else "allowed",
    )


__all__ = [
    "GUARDRAIL_UNAVAILABLE_TEXT",
    "REFUSAL_TEXT",
    "EvidenceGuardrailModel",
    "GuardrailEvaluator",
    "GuardrailUnavailableError",
    "InputGuardrailMiddleware",
    "InputGuardrailModel",
    "deterministic_evidence_boundary",
    "evaluate_input_guardrail",
    "guard_evidence_batch",
    "looks_instruction_like",
    "normalize_untrusted_text",
]
