"""Grounding verification and bounded repair middleware."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from pydantic import ValidationError

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import (
    EvidenceCard,
    InvestigationState,
    TurnState,
    state_update,
)
from investigation_agent.genai.investigation.grounding import (
    GroundingValidationError,
    deterministic_violations,
    verify_answer_draft,
)
from investigation_agent.genai.investigation.middleware.contracts import require_state
from investigation_agent.genai.investigation.middleware.evidence import (
    coverage_incomplete_from_messages,
)
from investigation_agent.genai.investigation.middleware.model_failures import SAFE_FAILURE_KEY
from investigation_agent.genai.investigation.prompts import (
    ANSWER_REPAIR_INSTRUCTION,
    STRUCTURED_ANSWER_INSTRUCTION,
)
from investigation_agent.genai.investigation.schemas import AnswerDraft, GroundingVerdict
from investigation_agent.genai.shared.retries import (
    OperationCancelledError,
    TransientExhaustedError,
)
from investigation_agent.genai.shared.structured import StructuredResultRunner
from investigation_agent.observability.instrumentation import phase_span


class GroundingMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Verify the private answer draft, allow one repair, then fail closed."""

    def __init__(
        self,
        *,
        verifier: StructuredResultRunner[GroundingVerdict] | None,
        max_answer_chars: int,
        max_repairs: int = 1,
    ) -> None:
        self._verifier = verifier
        self._max_answer_chars = max_answer_chars
        self._max_repairs = max_repairs

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(
        self, state: dict[str, Any], runtime: Runtime[RuntimeContext]
    ) -> dict[str, Any] | None:
        with phase_span("verify_grounding"):
            return await self._after_model(state, runtime)

    async def _after_model(
        self, state: dict[str, Any], runtime: Runtime[RuntimeContext]
    ) -> dict[str, Any] | None:
        parsed, turn = require_state(state)
        messages: list[AnyMessage] = list(state.get("messages", []))
        last_ai = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)), None
        )
        if last_ai is None:
            return None
        failure = last_ai.response_metadata.get(SAFE_FAILURE_KEY)
        if isinstance(failure, str) and failure:
            return _fail(turn, failure, ("model_unavailable",))
        structured = state.get("structured_response")
        domain_calls = [call for call in last_ai.tool_calls if call["name"] != AnswerDraft.__name__]
        if structured is None:
            if domain_calls:
                return None
            return self._reject(
                turn,
                ("unstructured_answer",),
                domain_calls=(),
                instruction=STRUCTURED_ANSWER_INSTRUCTION,
            )
        runtime.stream_writer({"phase": "verifying_answer"})
        try:
            draft = AnswerDraft.model_validate(structured)
        except ValidationError:
            return self._reject(
                turn, ("invalid_answer_draft",), domain_calls=domain_calls, instruction=None
            )
        return await self._verify_or_reject(
            draft,
            parsed,
            turn,
            messages,
            domain_calls=domain_calls,
            context=runtime.context,
        )

    async def _verify_or_reject(
        self,
        draft: AnswerDraft,
        state: InvestigationState,
        turn: TurnState,
        messages: Sequence[AnyMessage],
        *,
        domain_calls: Sequence[Mapping[str, Any]],
        context: RuntimeContext,
    ) -> dict[str, Any]:
        incomplete = coverage_incomplete_from_messages(messages)
        violations = list(
            deterministic_violations(
                draft,
                state,
                max_answer_chars=self._max_answer_chars,
                coverage_incomplete=incomplete,
            )
        )
        if domain_calls:
            violations.append("mixed_tool_calls")
        if not violations:
            try:
                verdict = await self._verify(draft, state, context)
                verified = verify_answer_draft(
                    draft,
                    state,
                    verdict=verdict,
                    max_answer_chars=self._max_answer_chars,
                    coverage_incomplete=incomplete,
                )
            except GroundingValidationError as exc:
                violations.extend(exc.violations)
            except (TransientExhaustedError, OperationCancelledError):
                return _fail(turn, "transient_exhausted", ("verifier_unavailable",))
            else:
                accepted = turn.model_copy(
                    update={
                        "answer_kind": "grounded",
                        "pending_answer": verified.answer,
                        "pending_citations": verified.citations,
                        "verification_violations": (),
                    }
                )
                return state_update(turn=accepted)
        return self._reject(turn, tuple(violations), domain_calls=domain_calls, instruction=None)

    async def _verify(
        self, draft: AnswerDraft, state: InvestigationState, context: RuntimeContext
    ) -> GroundingVerdict | None:
        material = [
            claim for claim in draft.claims if claim.material and claim.kind.value != "limitation"
        ]
        if not material or self._verifier is None:
            return GroundingVerdict(claims=()) if not material else None
        result = await self._verifier.run(
            {
                "claims": [claim.model_dump(mode="json") for claim in material],
                "cited_evidence": _cited_evidence(draft, state),
            },
            context=context,
        )
        return result.value

    def _reject(
        self,
        turn: TurnState,
        violations: tuple[str, ...],
        *,
        domain_calls: Sequence[Mapping[str, Any]],
        instruction: str | None,
    ) -> dict[str, Any]:
        if turn.repair_count >= self._max_repairs:
            return _fail(turn, "grounding_failed", violations)
        repaired = turn.model_copy(
            update={
                "repair_count": turn.repair_count + 1,
                "verification_violations": violations[:64],
            }
        )
        skipped = [
            ToolMessage(
                content="not executed: tool calls cannot accompany a final answer",
                tool_call_id=str(call["id"]),
                name=str(call["name"]),
            )
            for call in domain_calls
        ]
        text = instruction or ANSWER_REPAIR_INSTRUCTION.format(
            violations=", ".join(violations[:16])
        )
        return {
            **state_update(turn=repaired),
            "messages": [*skipped, HumanMessage(content=text)],
            "structured_response": None,
            "jump_to": "model",
        }


def _fail(turn: TurnState, code: str, violations: tuple[str, ...]) -> dict[str, Any]:
    failed = turn.model_copy(
        update={
            "safe_failure_code": code,
            "verification_violations": violations[:64],
            "answer_kind": None,
            "pending_answer": None,
            "pending_citations": (),
        }
    )
    return {**state_update(turn=failed), "structured_response": None, "jump_to": "end"}


def _cited_evidence(draft: AnswerDraft, state: InvestigationState) -> list[dict[str, object]]:
    identifiers = {identifier for claim in draft.claims for identifier in claim.evidence_ids}
    return [
        {
            "evidence_id": card.evidence_id,
            "content_hash": card.content_hash,
            "evidentiary_status": card.evidentiary_status,
            "content": _model_visible_card(card),
        }
        for card in state.evidence.ordered()
        if card.evidence_id in identifiers
    ]


def _model_visible_card(card: EvidenceCard) -> str:
    fields = "\n".join(f"{field.name}: {field.value}" for field in card.fields)
    visible = "\n".join(part for part in (card.display or "", fields) if part)
    label = "suspicious-untrusted-evidence" if card.suspicious_content else "untrusted-evidence"
    return f"<{label}>\n{visible}\n</{label}>"


__all__ = ["GroundingMiddleware"]
