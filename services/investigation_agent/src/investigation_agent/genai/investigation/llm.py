"""Task-level model binding for the main investigation capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from investigation_agent.genai.investigation.prompts import (
    CLOSURE_SYSTEM_PROMPT,
    GROUNDING_SYSTEM_PROMPT,
)
from investigation_agent.genai.investigation.schemas import AnswerDraft, GroundingVerdict
from investigation_agent.genai.shared.retry import RetryPolicy
from investigation_agent.genai.shared.structured_output import (
    StructuredResultRunner,
    StructuredRunner,
)


@dataclass(frozen=True, slots=True)
class InvestigationModels:
    planner: Any
    verifier: StructuredResultRunner[GroundingVerdict]
    closure: StructuredResultRunner[AnswerDraft]


def build_investigation_models(
    *,
    planner: Any,
    verifier: Any,
    closure: Any,
    retry_policy: RetryPolicy,
    transient_errors: tuple[type[Exception], ...],
) -> InvestigationModels:
    return InvestigationModels(
        planner=planner,
        verifier=StructuredRunner(
            verifier,
            GroundingVerdict,
            GROUNDING_SYSTEM_PROMPT,
            retry_policy=retry_policy,
            transient_errors=transient_errors,
        ),
        closure=StructuredRunner(
            closure,
            AnswerDraft,
            CLOSURE_SYSTEM_PROMPT,
            retry_policy=retry_policy,
            transient_errors=transient_errors,
        ),
    )


__all__ = ["InvestigationModels", "build_investigation_models"]
