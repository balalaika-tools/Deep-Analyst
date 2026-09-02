from __future__ import annotations

from typing import Any

import pytest
from investigation_agent.genai.shared.structured import StructuredChat
from pydantic import BaseModel


class Verdict(BaseModel):
    allowed: bool


class StructuredRunnable:
    async def ainvoke(self, input: object, **kwargs: Any) -> Verdict:
        del input, kwargs
        return Verdict(allowed=True)


class RecordingModel:
    def __init__(self, profile: dict[str, object] | None) -> None:
        self.profile = profile
        self.method: str | None = None

    def with_structured_output(
        self,
        schema: type[BaseModel],
        *,
        include_raw: bool,
        method: str,
    ) -> StructuredRunnable:
        del schema, include_raw
        self.method = method
        return StructuredRunnable()


@pytest.mark.asyncio
async def test_native_structured_output_is_selected_from_model_capabilities() -> None:
    model = RecordingModel({"structured_output": True, "tool_calling": True})

    result = await StructuredChat(model, Verdict, "Return a verdict.").invoke({"value": 1})

    assert result == Verdict(allowed=True)
    assert model.method == "json_schema"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        pytest.param({"structured_output": False, "tool_calling": True}, id="tool-calling"),
        pytest.param(None, id="missing-profile"),
    ],
)
async def test_function_calling_remains_the_compatibility_fallback(
    profile: dict[str, object] | None,
) -> None:
    model = RecordingModel(profile)

    result = await StructuredChat(model, Verdict, "Return a verdict.").invoke({"value": 1})

    assert result == Verdict(allowed=True)
    assert model.method == "function_calling"
