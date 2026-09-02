"""Provider-tolerant serializers for captured GenAI message content."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

_ROLE_BY_MESSAGE_TYPE = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
    "function": "tool",
}
_FINISH_REASON_KEYS = ("finish_reason", "finishReason", "stop_reason", "stopReason")


def resolve_finish_reason(*sources: Any) -> str | None:
    """Return the first provider-reported finish reason without inventing one."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _FINISH_REASON_KEYS:
            value = source.get(key)
            if value is not None and str(value):
                return str(value)
    return None


def _role(message: Any, default: str = "user") -> str:
    if isinstance(message, dict):
        value = message.get("role") or message.get("type")
    else:
        value = getattr(message, "role", None) or getattr(message, "type", None)
    return _ROLE_BY_MESSAGE_TYPE.get(str(value), str(value or default))


def _part(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"type": "text", "content": value}
    if not isinstance(value, dict):
        return {"type": "text", "content": str(value)}

    part_type = str(value.get("type") or "text")
    if part_type == "non_standard" and isinstance(value.get("value"), dict):
        return _part(value["value"])
    if part_type in {"text", "reasoning"}:
        return {
            "type": part_type,
            "content": value.get("content", value.get("text", "")),
        }
    if part_type in {"tool_call", "tool_use"}:
        name = value.get("name")
        if not name:
            return {
                "type": "unknown_tool_call",
                "arguments": value.get("arguments", value.get("args", {})),
            }
        result = {
            "type": "tool_call",
            "name": str(name),
            "arguments": value.get("arguments", value.get("args", {})),
        }
        if call_id := value.get("id"):
            result["id"] = str(call_id)
        return result

    # Content capture is already opt-in. Preserve provider-normalized blocks
    # rather than silently flattening JSON, image, reasoning, or extension data.
    return dict(value)


def _content_blocks(message: Any) -> Any:
    try:
        normalized = getattr(message, "content_blocks", None)
    except (AttributeError, TypeError, ValueError):
        normalized = None
    return normalized if normalized is not None else getattr(message, "content", "")


def _parts(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, dict):
        content = message.get("content", "")
        tool_calls = message.get("tool_calls") or []
    else:
        content = _content_blocks(message)
        tool_calls = getattr(message, "tool_calls", None) or []

    values = content if isinstance(content, list) else [content]
    result = [_part(value) for value in values]
    if not any(part.get("type") == "tool_call" for part in result):
        for call in tool_calls:
            if isinstance(call, dict):
                result.append(_part({"type": "tool_call", **call}))
            else:
                result.append(
                    _part(
                        {
                            "type": "tool_call",
                            "id": getattr(call, "id", None),
                            "name": getattr(call, "name", None),
                            "args": getattr(call, "args", {}),
                        }
                    )
                )
    return result


def _message(message: Any, default_role: str = "user") -> dict[str, Any]:
    role = _role(message, default_role)
    if role != "tool":
        return {"role": role, "parts": _parts(message)}

    if isinstance(message, dict):
        response = message.get("content", "")
        tool_call_id = message.get("tool_call_id")
    else:
        response = getattr(message, "content", "")
        tool_call_id = getattr(message, "tool_call_id", None)
    part: dict[str, Any] = {"type": "tool_call_response", "response": response}
    if tool_call_id:
        part["id"] = str(tool_call_id)
    return {"role": role, "parts": [part]}


def serialize_messages(messages: Sequence[Any]) -> str:
    """Serialize request messages using the OpenTelemetry role/parts schema."""
    return json.dumps([_message(message) for message in messages], ensure_ascii=False, default=str)


def _first_conversation(messages: Sequence[Any]) -> tuple[list[Any], int]:
    if not messages:
        return [], 0
    batches = messages if isinstance(messages[0], (list, tuple)) else [messages]
    return list(batches[0]), len(batches)


def _text_observation_messages(
    messages: Sequence[dict[str, Any]], *, omit_empty_reasoning: bool = False
) -> list[dict[str, str]] | None:
    """Return a concise text projection without dropping meaningful content."""
    rendered: list[dict[str, str]] = []
    for message in messages:
        parts = message.get("parts")
        if not isinstance(parts, list):
            return None
        if omit_empty_reasoning:
            parts = [
                part
                for part in parts
                if not (
                    isinstance(part, dict)
                    and part.get("type") == "reasoning"
                    and part.get("content") == ""
                )
            ]
        if len(parts) != 1:
            return None
        part = parts[0]
        if not isinstance(part, dict) or part.get("type") != "text":
            return None
        content = part.get("content")
        if not isinstance(content, str):
            return None
        rendered.append({"role": str(message.get("role", "user")), "content": content})
    return rendered


def serialize_observation_input(messages: Sequence[Any]) -> str:
    """Serialize a readable observation input without losing complex message parts."""
    conversation, _ = _first_conversation(messages)
    canonical = [_message(message) for message in conversation]
    rendered = _text_observation_messages(canonical)
    return json.dumps(
        rendered if rendered is not None else canonical, ensure_ascii=False, default=str
    )


def serialize_chat_model_input(
    messages: Sequence[Any], *, separate_system_instructions: bool
) -> tuple[str | None, str, int]:
    """Serialize the first LangChain conversation and report its physical batch size."""
    conversation, batch_size = _first_conversation(messages)
    if not conversation:
        return None, "[]", batch_size
    system_instructions = None
    if separate_system_instructions:
        system_parts: list[dict[str, Any]] = []
        chat_history: list[Any] = []
        for message in conversation:
            if _role(message) == "system":
                system_parts.extend(_parts(message))
            else:
                chat_history.append(message)
        if system_parts:
            system_instructions = json.dumps(system_parts, ensure_ascii=False, default=str)
        conversation = chat_history
    return system_instructions, serialize_messages(conversation), batch_size


def _llm_result_messages(response: Any) -> list[dict[str, Any]]:
    output_messages: list[dict[str, Any]] = []
    for generation_list in getattr(response, "generations", None) or []:
        for generation in generation_list:
            source = getattr(generation, "message", None)
            if source is None:
                source = {"role": "assistant", "content": getattr(generation, "text", "")}
            message = _message(source, default_role="assistant")
            message["finish_reason"] = (
                resolve_finish_reason(
                    getattr(source, "response_metadata", None),
                    getattr(generation, "generation_info", None),
                )
                or "unknown"
            )
            output_messages.append(message)
    return output_messages


def serialize_llm_result(response: Any) -> str:
    """Serialize every LangChain generation without losing provider content blocks."""
    return json.dumps(_llm_result_messages(response), ensure_ascii=False, default=str)


def serialize_observation_output(response: Any, *, output_type: str | None) -> str:
    """Serialize concise observation output, falling back to the lossless envelope."""
    canonical = _llm_result_messages(response)
    # Some adapters emit an empty reasoning block next to the actual text. It
    # carries no presentation content, so omit it only from this backend-facing
    # projection. The canonical gen_ai.output.messages value remains unchanged.
    rendered = _text_observation_messages(canonical, omit_empty_reasoning=True)
    if rendered is None:
        return json.dumps(canonical, ensure_ascii=False, default=str)
    if len(rendered) != 1:
        return json.dumps(rendered, ensure_ascii=False, default=str)

    content = rendered[0]["content"]
    if output_type == "json":
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            pass
        else:
            return json.dumps(parsed, ensure_ascii=False, default=str)
    return json.dumps(content, ensure_ascii=False, default=str)
