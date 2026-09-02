from investigation_agent.genai.evidence_search.prompts import SEARCH_AGENT_SYSTEM_PROMPT
from investigation_agent.genai.guardrails.prompts import (
    EVIDENCE_GUARDRAIL_PROMPT,
    INPUT_GUARDRAIL_PROMPT,
)
from investigation_agent.genai.investigation.prompts import (
    ANSWER_REPAIR_INSTRUCTION,
    ANSWER_STYLE_GUIDANCE,
    CLOSURE_SYSTEM_PROMPT,
    GROUNDING_SYSTEM_PROMPT,
    MAIN_SYSTEM_PROMPT,
    STRUCTURED_ANSWER_INSTRUCTION,
)
from investigation_agent.genai.record_query.prompts import QUERY_AGENT_SYSTEM_PROMPT
from investigation_agent.genai.state_projection.prompts import (
    PROJECTION_REPAIR_PROMPT,
    PROJECTION_SYSTEM_PROMPT,
)


def normalized(prompt: str) -> str:
    return " ".join(prompt.split())


def test_user_facing_answer_prompts_require_readable_markdown() -> None:
    assert "Lead with the direct conclusion" in ANSWER_STYLE_GUIDANCE
    assert "short paragraphs" in ANSWER_STYLE_GUIDANCE
    assert "bullets" in ANSWER_STYLE_GUIDANCE
    assert "established facts" in ANSWER_STYLE_GUIDANCE
    assert "raw HTML" in ANSWER_STYLE_GUIDANCE
    assert ANSWER_STYLE_GUIDANCE in MAIN_SYSTEM_PROMPT
    assert ANSWER_STYLE_GUIDANCE in CLOSURE_SYSTEM_PROMPT
    assert "concise Markdown presentation" in ANSWER_REPAIR_INSTRUCTION


def test_active_investigation_prompts_do_not_reintroduce_legacy_partitioning() -> None:
    prompts = (
        SEARCH_AGENT_SYSTEM_PROMPT,
        INPUT_GUARDRAIL_PROMPT,
        EVIDENCE_GUARDRAIL_PROMPT,
        MAIN_SYSTEM_PROMPT,
        CLOSURE_SYSTEM_PROMPT,
        GROUNDING_SYSTEM_PROMPT,
        STRUCTURED_ANSWER_INSTRUCTION,
        QUERY_AGENT_SYSTEM_PROMPT,
        PROJECTION_SYSTEM_PROMPT,
        PROJECTION_REPAIR_PROMPT,
    )

    assert all("one case" not in prompt.casefold() for prompt in prompts)
    assert all("fixed case" not in prompt.casefold() for prompt in prompts)
    assert "global evidence corpus" in SEARCH_AGENT_SYSTEM_PROMPT
    assert "global evidence corpus" in normalized(MAIN_SYSTEM_PROMPT)


def test_nested_agent_prompts_keep_structured_output_and_provenance_boundaries() -> None:
    assert "Return only the SearchVerdict structured output" in SEARCH_AGENT_SYSTEM_PROMPT
    assert "every selected identifier appeared" in SEARCH_AGENT_SYSTEM_PROMPT
    assert "Return only the QueryVerdict structured output" in QUERY_AGENT_SYSTEM_PROMPT
    assert "Project `record_id`, `content_hash`, and `source_refs`" in QUERY_AGENT_SYSTEM_PROMPT
    assert "Return only the structured projection" in PROJECTION_SYSTEM_PROMPT
    assert "every referenced ID is available" in normalized(PROJECTION_SYSTEM_PROMPT)
