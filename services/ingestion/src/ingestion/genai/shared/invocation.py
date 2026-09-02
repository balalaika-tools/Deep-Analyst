"""One agent invocation as one `invoke_agent` span, bounded by the throttle."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from observability import start_genai_span
from opentelemetry.trace import Tracer
from pydantic import BaseModel

from ingestion.genai.shared.failures import translate_provider_error
from ingestion.genai.shared.throttle import ModelThrottle
from ingestion.ports.entity_extractor import PermanentExtractionError


async def run_structured_agent[T: BaseModel](
    agent: Runnable[Any, Any],
    *,
    message: str,
    schema: type[T],
    agent_name: str,
    throttle: ModelThrottle,
    tracer: Tracer,
) -> T:
    """Invoke the agent for one chunk and return its validated structured response.

    Provider failures escape as the port taxonomy. A run that ends without a
    structured response is permanent: the retry policy already had its attempts.
    """
    attributes = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent_name}
    async with throttle.slot():
        try:
            with start_genai_span(
                f"invoke_agent {agent_name}", tracer=tracer, attributes=attributes
            ):
                result = await agent.ainvoke({"messages": [HumanMessage(message)]})
                structured = result.get("structured_response")
                if not isinstance(structured, schema):
                    raise PermanentExtractionError(f"{agent_name} returned no structured response")
        except Exception as exc:
            raise translate_provider_error(exc, operation=agent_name) from exc
        return structured
