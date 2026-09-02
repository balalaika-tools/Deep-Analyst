## Why

The repository now contains indexed, source-backed evidence but no executable investigation
experience: analysts cannot yet ask a multi-turn question, follow identifiers across text,
structured records, and graph relationships, or receive a streamed, cited answer. This change
adds that runtime while preserving case isolation, provenance, and bounded agent behavior, at a
scope that fits a three-day single-replica prototype and leaves multi-replica hardening,
authentication, and authorization to the documented production evolution.

## What Changes

- Add an independently deployable FastAPI investigation service with liveness, dependency
  readiness, a validated thread-scoped invocation endpoint, paginated conversation reads, thread
  deletion, and a stable SSE envelope for progress, verified final-answer deltas, completion, and
  terminal failure. Planner and sub-agent tokens are never exposed, and the final draft is
  validated and checkpointed before its text is released incrementally.
- Implement the investigation agent as one LangChain `create_agent` with exactly three tools,
  an `AsyncPostgresSaver` checkpointer, and a custom agent state. Middleware hooks own turn
  intake, the LLM input guardrail, per-turn context assembly, evidence indexing, grounding and
  citation verification, turn close, and hard call limits. Durable state combines the framework
  message list for the current turn, a validated LLM-authored `WorkingProjection` refreshed at
  turn close, a bounded evidence index, current-turn status, and the product history transcript.
- Implement `search_evidence` as a nested, checkpointer-free `create_agent` whose single tool
  performs hybrid BM25 and vector retrieval with deterministic fusion. The nested agent judges
  relevance, may reformulate, is limited to three distinct retrievals, and returns one typed
  outcome that distinguishes sufficient support from an explicit retrieval miss.
- Implement `query_records` as a nested, checkpointer-free `create_agent` whose single tool
  executes model-authored SQL only after a deterministic parser/policy gate restricted to
  `agent_read` views, on a read-only role inside a transaction whose case scope is set by trusted
  code. The nested agent sees safe failure classes and may correct itself within three distinct
  plans.
- Implement `find_connections` as bounded, deterministic traversal over sourced entity and
  relationship rows, with explicit status, predicate, time, depth, path, and result limits.
- Use built-in LangChain middleware for transient model and tool retries and for model-call and
  tool-call limits at both the main and nested agents. Add LLM guardrail middleware for prompt
  injection and out-of-scope requests; PII is retained in application data, while telemetry
  content capture remains separately controlled and disabled by default.
- Persist all per-thread state, including the product transcript, in `AsyncPostgresSaver`
  checkpoints and serve history through the public LangGraph state API. Idempotency, per-thread
  serialization, and interruption semantics derive from the latest checkpoint and an in-process
  lock, with no separate conversation tables, leases, or recovery coordinator.
- Keep persistence in the existing application PostgreSQL deployment with two runtime roles and
  pools: a read-only evidence reader and a checkpoint writer. A one-shot initializer with the
  existing owner credential creates roles, views, and checkpoint tables.
- Reuse the existing OpenTelemetry library and Collector routing to produce one finite trace per
  turn attempt with HTTP, agent, guardrail, retrieval, tool, model, projection, and final
  streaming boundaries, bounded error data, token/TTFC metrics, and trace-correlated logs.
- Provide no authentication or authorization layer in the prototype. Requests carry `thread_id`
  and `case_id`; a thread is bound to its case at the first turn, and case scope is applied by
  trusted code at every tool boundary. Identity, ownership, and case authorization are recorded as
  production evolution to be added in front of the endpoints without changing the agent.
- Supersede the earlier design preference for a custom outer state graph with a one-decision
  strategist, per-tool projection compaction, and owner-derived checkpoint identity. The ReAct
  loop, checkpoint cadence, and call limits of `create_agent` are used directly.

## Capabilities

### New Capabilities

- `investigation-api`: FastAPI lifecycle, thread-scoped invocation, sanitized SSE streaming,
  paginated history transport, thread deletion, validation, idempotency, per-thread serialization,
  cancellation, and safe error contracts.
- `investigation-agent`: Single `create_agent` orchestration with custom state, middleware-owned
  guardrails, context assembly, evidence indexing, grounding and citation verification, turn-close
  projection, call limits, retry ownership, and turn-level observability.
- `investigation-tools`: Typed and case-scoped contracts for a self-correcting hybrid retrieval
  sub-agent, a policy-gated structured-record query sub-agent, and bounded sourced graph
  traversal.
- `conversation-history`: One checkpointed persistence model holding recovery state and the
  product transcript, with case binding, idempotent turns, interruption semantics, paginated
  reads, and thread deletion.

### Modified Capabilities

- `evidence-store`: Add a read-only `agent_read` view surface for model-authored queries with
  server-controlled session case scope, without changing the canonical evidence schemas.
- `local-app-database`: Extend the existing application PostgreSQL deployment with two runtime
  roles, checkpoint persistence, a one-shot initializer, readiness, and local agent-service wiring
  without changing evidence-domain source-of-truth semantics.

## Impact

- Adds a new `services/investigation_agent` workspace member, FastAPI/ASGI container, service-local
  configuration baseline and environment contract, and API/unit/integration/contract/E2E tests.
- Adds runtime dependencies for FastAPI, an ASGI server, LangGraph checkpoint persistence,
  psycopg async pooling, SSE delivery, and SQL AST validation; LangChain/LangGraph/provider versions
  remain pinned and compatibility-tested.
- Extends `postgres-app` with the `agent_read` and `agent_runtime` schemas, two roles, and grants
  while leaving the evidence tables and ingestion ownership intact.
- Reuses `libs/evidence_model` for evidence/provenance contracts and extends
  `libs/observability` only where generic reusable telemetry is genuinely missing; agent-specific
  event vocabulary and LangChain instrumentation stay inside the new service.
- Updates `docs/DESIGN.md`, `docs/DATA_MODEL.md`, `README.md`, Compose, root and service
  `.env.example` files, and `config/investigation-agent/local.yaml` to describe and run the new
  capability.
- Requires Bedrock chat and embedding access, the populated evidence store, the existing OTLP
  routing topology, and the reader and writer role passwords.
- Explicitly single-replica and unauthenticated: per-thread serialization is an in-process lock,
  and any caller may use any thread or case. Authentication, case authorization, database leases,
  fencing, forced RLS, and signed scope tokens are recorded as production evolution.
