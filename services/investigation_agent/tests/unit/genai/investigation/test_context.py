"""Current-turn context trimming keeps evidence delimiters well-formed."""

from __future__ import annotations

from investigation_agent.genai.investigation.middleware.context import trim_turn_messages
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage


def test_truncated_tool_output_recloses_the_open_evidence_delimiter() -> None:
    body = "x" * 400
    content = (
        '{"tool": "search_evidence"}\n'
        f"<untrusted-evidence id='e1'>\n{body}\n</untrusted-evidence>\n"
        f"<suspicious-untrusted-evidence id='e2'>\n{body}\n</suspicious-untrusted-evidence>"
    )
    messages: list[AnyMessage] = [
        HumanMessage(content="question"),
        AIMessage(content="", tool_calls=[{"name": "search_evidence", "args": {}, "id": "c1"}]),
        ToolMessage(content=content, tool_call_id="c1", name="search_evidence"),
    ]

    kept, trimmed = trim_turn_messages(messages, max_chars=600)

    assert trimmed
    truncated = str(kept[-1].content)
    assert truncated.endswith("\n[trimmed]</suspicious-untrusted-evidence>")
    assert truncated.count("<suspicious-untrusted-evidence") == truncated.count(
        "</suspicious-untrusted-evidence>"
    )


def test_truncation_after_a_closed_delimiter_adds_no_stray_closing_tag() -> None:
    content = "<untrusted-evidence id='e1'>\nshort\n</untrusted-evidence>\n" + "y" * 300
    messages: list[AnyMessage] = [
        HumanMessage(content="question"),
        AIMessage(content="", tool_calls=[{"name": "search_evidence", "args": {}, "id": "c1"}]),
        ToolMessage(content=content, tool_call_id="c1", name="search_evidence"),
    ]

    kept, trimmed = trim_turn_messages(messages, max_chars=200)

    assert trimmed
    assert str(kept[-1].content).endswith("\n[trimmed]")
