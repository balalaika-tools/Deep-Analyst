"""``search_evidence`` nested agent: one ``retrieve`` tool inside a checkpointer-free loop."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import ValidationError

from investigation_agent.genai.evidence_search.prompts import SEARCH_AGENT_SYSTEM_PROMPT
from investigation_agent.genai.evidence_search.retrieval import (
    CandidateReader,
    FusionPolicy,
    TextEmbedder,
    retrieve_hybrid,
)
from investigation_agent.genai.evidence_search.schemas import (
    MAX_SEMANTIC_ATTEMPTS,
    FusedCandidate,
    RetrievalQuery,
    SearchAttempt,
    SearchConsumption,
    SearchEvidence,
    SearchIntent,
    SearchOutcome,
    SearchVerdict,
)
from investigation_agent.genai.guardrails.middleware import normalize_untrusted_text
from investigation_agent.genai.shared.retries import (
    CancellationToken,
    RetryPolicy,
    model_retry_middleware,
    tool_retry_middleware,
)

type ProgressWriter = Callable[[Mapping[str, object]], None]


class RetrievalTransientError(ConnectionError):
    """Both modalities failed on one physical attempt; retry the identical retrieval."""


@dataclass(slots=True)
class SearchInvocation:
    """Invocation-local, trusted state the nested model can neither read nor write."""

    deadline: float
    cancellation: CancellationToken
    excluded_chunk_ids: set[str]
    max_top_k: int
    fingerprints: set[str] = field(default_factory=set)
    attempts: list[SearchAttempt] = field(default_factory=list)
    retrieved: dict[str, FusedCandidate] = field(default_factory=dict)
    physical_attempts: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchAgentPolicy:
    model_call_limit: int = 6
    tool_call_limit: int = MAX_SEMANTIC_ATTEMPTS
    max_top_k: int = 20
    max_evidence: int = 40
    max_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if not 1 <= self.tool_call_limit <= MAX_SEMANTIC_ATTEMPTS:
            raise ValueError("retrieve calls must be between one and three")
        if min(self.model_call_limit, self.max_top_k, self.max_evidence, self.max_bytes) < 1:
            raise ValueError("search agent bounds must be positive")


class SearchEvidenceAgent:
    """Builds the nested agent once; every invocation gets fresh local state and no memory."""

    def __init__(
        self,
        *,
        model: Any,
        reader: CandidateReader,
        embedder: TextEmbedder,
        fusion_policy: FusionPolicy,
        retry_policy: RetryPolicy,
        transient_errors: tuple[type[Exception], ...],
        policy: SearchAgentPolicy | None = None,
    ) -> None:
        self._reader = reader
        self._embedder = embedder
        self._fusion_policy = fusion_policy
        self._policy = policy or SearchAgentPolicy()
        retrieve = self._build_retrieve_tool()
        self._agent = create_agent(
            model=model,
            tools=[retrieve],
            system_prompt=SEARCH_AGENT_SYSTEM_PROMPT,
            response_format=SearchVerdict,
            context_schema=SearchInvocation,
            middleware=[
                model_retry_middleware(retry_policy, transient_errors),
                tool_retry_middleware(
                    retry_policy,
                    (RetrievalTransientError, *transient_errors),
                    tools=["retrieve"],
                ),
                cast(
                    Any,
                    ModelCallLimitMiddleware(
                        run_limit=self._policy.model_call_limit, exit_behavior="end"
                    ),
                ),
                cast(
                    Any,
                    ToolCallLimitMiddleware(
                        run_limit=self._policy.tool_call_limit, exit_behavior="continue"
                    ),
                ),
            ],
            checkpointer=None,
        )

    def _build_retrieve_tool(self) -> Any:
        reader = self._reader
        embedder = self._embedder
        fusion_policy = self._fusion_policy

        @tool
        async def retrieve(
            query: str,
            runtime: ToolRuntime[SearchInvocation],
            source_systems: list[str] | None = None,
            event_time_from: str | None = None,
            event_time_to: str | None = None,
            top_k: int = 20,
        ) -> str:
            """Fetch BM25 and vector candidates for one focused query and return them fused."""

            invocation = runtime.context
            try:
                proposal = RetrievalQuery.model_validate(
                    {
                        "query": query,
                        "source_systems": tuple(source_systems or ()),
                        "event_time_from": event_time_from,
                        "event_time_to": event_time_to,
                        "top_k": min(top_k, invocation.max_top_k),
                    }
                )
            except ValidationError:
                return _rejection(
                    "invalid_query", "The query did not satisfy the retrieval schema."
                )
            fingerprint = proposal.fingerprint()
            if fingerprint in invocation.fingerprints:
                invocation.warnings.append("repeated_query_rejected")
                return _rejection("repeated_query", "This query was already attempted.")
            if len(invocation.attempts) >= MAX_SEMANTIC_ATTEMPTS:
                return _rejection("attempts_exhausted", "No further retrieval is permitted.")
            invocation.cancellation.check()
            invocation.physical_attempts += 1
            result = await retrieve_hybrid(
                reader=reader,
                embedder=embedder,
                query=proposal,
                excluded_chunk_ids=frozenset(invocation.excluded_chunk_ids),
                deadline=invocation.deadline,
                policy=fusion_policy,
            )
            if result.lexical_status != "ok" and result.vector_status != "ok":
                raise RetrievalTransientError("both retrieval modalities failed")
            invocation.fingerprints.add(fingerprint)
            attempt_number = len(invocation.attempts) + 1
            invocation.warnings.extend(result.warnings)
            for candidate in result.candidates:
                invocation.retrieved.setdefault(candidate.chunk_id, candidate)
                invocation.excluded_chunk_ids.add(candidate.chunk_id)
            invocation.attempts.append(
                SearchAttempt(
                    semantic_attempt=attempt_number,
                    query_fingerprint=fingerprint,
                    physical_attempts=invocation.physical_attempts,
                    retrieved_chunk_ids=tuple(sorted(c.chunk_id for c in result.candidates)),
                    lexical_status=result.lexical_status,
                    vector_status=result.vector_status,
                    safe_diagnostic=("partial_modality" if result.partial else None),
                )
            )
            invocation.physical_attempts = 0
            runtime.stream_writer(
                {
                    "phase": "searching_evidence",
                    "tool": "search_evidence",
                    "attempt": attempt_number,
                }
            )
            return _candidate_message(result.candidates, partial=result.partial)

        return retrieve

    async def run(
        self,
        intent: SearchIntent,
        *,
        call_id: str,
        deadline: float,
        cancellation: CancellationToken,
        seen_chunk_ids: frozenset[str],
        progress: ProgressWriter | None = None,
    ) -> SearchOutcome:
        invocation = SearchInvocation(
            deadline=deadline,
            cancellation=cancellation,
            excluded_chunk_ids=set(seen_chunk_ids),
            max_top_k=self._policy.max_top_k,
        )
        verdict: SearchVerdict | None = None
        model_calls = 0
        payload = intent.model_dump(mode="json")
        async for part in self._agent.astream(
            {"messages": [HumanMessage(content=json.dumps(payload, sort_keys=True))]},
            context=invocation,
            stream_mode=["updates", "custom"],
            version="v2",
        ):
            if part["type"] == "custom":
                if progress is not None:
                    progress(dict(part["data"]))
                continue
            update = cast(dict[str, Any], part["data"])
            if "model" in update:
                model_calls += 1
                structured = update["model"].get("structured_response")
                if structured is not None:
                    verdict = SearchVerdict.model_validate(structured)
        return _outcome(
            invocation,
            verdict,
            call_id=call_id,
            intent_fingerprint=_digest(payload),
            model_calls=model_calls,
            policy=self._policy,
        )


def _outcome(
    invocation: SearchInvocation,
    verdict: SearchVerdict | None,
    *,
    call_id: str,
    intent_fingerprint: str,
    model_calls: int,
    policy: SearchAgentPolicy,
) -> SearchOutcome:
    warnings = list(invocation.warnings)
    if verdict is None:
        status = "retrieval_incomplete"
        selected_ids: list[str] = []
        warnings.append("nested_agent_limit_reached")
    else:
        status = verdict.status
        retrieved = set(invocation.retrieved)
        selected_ids = sorted(set(verdict.selected_chunk_ids) & retrieved)
        if set(verdict.selected_chunk_ids) - retrieved:
            warnings.append("unretrieved_selection_dropped")
        if status == "sufficient" and not selected_ids:
            status = "retrieval_incomplete"
            warnings.append("sufficient_without_selection")
    evidence: list[SearchEvidence] = []
    encoded_bytes = 0
    for chunk_id in selected_ids:
        candidate = invocation.retrieved[chunk_id]
        item_bytes = len(candidate.text.encode("utf-8"))
        if len(evidence) >= policy.max_evidence or encoded_bytes + item_bytes > policy.max_bytes:
            warnings.append("evidence_bound_reached")
            break
        attempt = next(
            (a.semantic_attempt for a in invocation.attempts if chunk_id in a.retrieved_chunk_ids),
            1,
        )
        evidence.append(
            SearchEvidence(
                evidence_id=chunk_id,
                content_hash=candidate.content_hash,
                source_refs=candidate.source_refs,
                content=candidate.text,
                provenance=tuple(
                    item.model_copy(update={"semantic_attempt": attempt})
                    for item in candidate.contributions
                ),
            )
        )
        encoded_bytes += item_bytes
    rows = sum(len(a.retrieved_chunk_ids) for a in invocation.attempts)
    return SearchOutcome(
        call_id=call_id,
        intent_fingerprint=intent_fingerprint,
        status=status,
        attempts=tuple(invocation.attempts),
        evidence=tuple(evidence),
        warnings=tuple(dict.fromkeys(warnings))[:16],
        consumption=SearchConsumption(
            model_calls=model_calls,
            tool_calls=len(invocation.attempts),
            rows=rows,
            bytes=encoded_bytes,
            physical_attempts=sum(a.physical_attempts for a in invocation.attempts),
        ),
    )


def _candidate_message(candidates: tuple[FusedCandidate, ...], *, partial: bool) -> str:
    header = {"candidate_count": len(candidates), "partial_modality": partial}
    parts = [json.dumps(header, sort_keys=True)]
    for candidate in candidates:
        text = normalize_untrusted_text(candidate.text)
        parts.append(
            f"<untrusted-evidence id={candidate.chunk_id!r} source={candidate.source_system!r}>\n"
            f"{text}\n</untrusted-evidence>"
        )
    return "\n".join(parts)


def _rejection(code: str, message: str) -> str:
    return json.dumps({"rejected": code, "message": message}, sort_keys=True)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = [
    "RetrievalTransientError",
    "SearchAgentPolicy",
    "SearchEvidenceAgent",
    "SearchInvocation",
]
