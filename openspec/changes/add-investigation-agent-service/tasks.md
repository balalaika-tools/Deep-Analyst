## 1. Framework Compatibility and Service Foundation

- [x] 1.1 Add `services/investigation_agent` as a uv workspace package with the production and
  development dependencies required by FastAPI, ASGI serving, LangChain/LangGraph,
  `langgraph-checkpoint-postgres`, psycopg async pools, SSE, SQL AST parsing, and the existing
  internal libraries; verify `uv lock --check` and a scoped package import both succeed.
- [x] 1.2 Rewrite the pinned LangChain/LangGraph compatibility contract in
  `tests/contract/framework/` so it proves: `before_agent` can `jump_to="end"` before the model
  node runs; `after_model` can `jump_to="model"` once and `jump_to="end"`; `after_agent` runs
  after `ModelCallLimitMiddleware(exit_behavior="end")` and `ToolCallLimitMiddleware` stop the
  loop; custom stream events emitted by a nested `create_agent` inside a tool reach the parent
  `custom` stream; `durability="sync"` checkpoints before the next node; `None` input resumes
  from the last checkpoint; and v2 `updates` node names and `custom` shapes match the adapter.
  Delete the one-decision capture tests.
- [x] 1.3 Scaffold the capability-oriented service modules and test roots described in the design,
  keeping FastAPI, PostgreSQL, provider, and LangChain imports outside domain/application code;
  verify import-boundary tests and `uv run ruff check services/investigation_agent` pass.
- [x] 1.4 Remove the `CaseAuthorizer` port, `AuthenticatedPrincipal`, the identity middleware, and
  the development authorizer from `core/context.py`, `bootstrap/runtime.py`, and
  `api/dependencies.py`; make `RuntimeContext` carry only case, thread, request, deadline, and
  cancellation; verify a bootstrap unit test can substitute fakes for the evidence reader and
  model clients without importing ingestion internals or a sibling service package.
- [x] 1.5 Remove `AuthorizationFailure` and `AdapterAuthorizationError` from the core error
  taxonomy and the problem-details mapping; verify the remaining classes (validation, conflict,
  policy, no-support, transient exhaustion, budget, cancellation, dependency, incompatible-state,
  internal) each map from one representative exception.

## 2. Typed Configuration and Secrets

- [x] 2.1 Implement service settings with the repository precedence contract (constructor,
  environment, `.env`, environment YAML, then defaults) and typed validation for deadlines,
  retries, limits, retrieval weights, graph limits, history bounds, SSE limits, and telemetry
  policy; verify precedence and invalid-boundary unit tests pass.
- [x] 2.2 Implement a separate secrets model using `SecretStr` for the reader and writer DSNs in
  the serving process and the owner DSN plus two role passwords in the initializer, leaving AWS
  credentials to the SDK chain; verify each entry point loads only its subset and validation errors
  identify fields without rendering any secret value.
- [x] 2.3 Remove `DEVELOPMENT_OWNER_ID` and `DEVELOPMENT_ALLOWED_CASE_IDS` from settings, both
  `.env.example` files, and Compose; add settings for main and nested model-call and tool-call
  limits, the closure reserve, the context token bound, and the evidence-index bound; verify the
  environment-contract test keeps YAML keys, settings fields, and example variables synchronized.
- [x] 2.4 Make the serving and initializer entry points validate configuration before initializing
  telemetry, pools, the saver, or model clients; verify missing, invalid, unknown, or misplaced
  secret fields fail with zero external constructor calls.

## 3. Database Initialization, Roles, and Pools

- [x] 3.1 Implement the idempotent `agent-db-init` entry point that, with the owner credential,
  verifies the expected evidence tables and indexes exist, creates the `agent_reader` and
  `agent_writer` login roles, creates the `agent_read` and `agent_runtime` schemas, runs
  `AsyncPostgresSaver.setup()` with the `agent_runtime` search path, applies grants, and records
  the initializer version; verify a second run is a no-op and a missing evidence schema fails
  closed.
- [x] 3.2 Add the `agent_read` views over allowlisted projection columns, each filtered by
  `case_id = current_setting('app.case_id', true)`, preserving evidence identifiers, `case_id`,
  source references, and content hashes; verify a view contract test exposes required columns and
  nothing unapproved, and that an unset setting returns no rows.
- [x] 3.3 Apply least-privilege grants: `agent_reader` gets `SELECT` on the required evidence
  tables and `agent_read`, defaults to read-only transactions, and lacks `TEMP`, `CREATE`, and
  `agent_runtime`; `agent_writer` gets only `agent_runtime`; ingestion gets no agent grant. Verify
  the positive/negative privilege matrix with the real logins.
- [x] 3.4 Implement the two bounded async pools with purpose-specific connection settings and
  acquisition deadlines, and construct the `AsyncPostgresSaver` on the writer pool; verify
  construction tests inspect role DSNs, pool bounds, autocommit, row factory, prepare threshold, and
  search paths, and that no migration credential is present at runtime.
- [x] 3.5 Implement bounded read-only readiness probes for both pools, the initializer version, the
  `agent_read` views, and BM25/vector availability; verify an absent or incompatible dependency
  reports not ready without writing a row or invoking a model.
- [x] 3.6 Add real-role, cross-case integration tests that omit or contradict case predicates,
  attempt base-table writes, `SELECT INTO`, temporary relations, and `SET`/`set_config`, then reuse
  a pooled connection from case A for case B; verify no row from another case is observable and
  the scope never survives a transaction.

## 4. Agent State

- [x] 4.1 Replace `InvestigationState` with `InvestigationAgentState`, a LangChain `AgentState`
  extended by immutable `control` (case, schema version, policy version), `turn` (turn/request
  IDs, fingerprint, user message ID, status, safe failure code, repair count, exhausted limit,
  prior trace carrier), a bounded `evidence` index keyed by evidence ID, `projection`, `history`,
  and `usage` counters; delete the evidence ledger, `apply_tool_outcomes` reducer, materiality
  predicate, pruning, and `pending_decision`/`candidate_answer` turn fields; verify schema tests
  reject model-authored control mutation and unknown fields.
- [x] 4.2 Implement the evidence-index upsert from a `ToolOutcome`: stable key, no status
  promotion, no provenance overwrite, bound with drop-oldest-unreferenced and a coverage notice;
  verify duplicate deliveries are idempotent and a bounded index records the notice.
- [x] 4.3 Keep the state-schema version check on load; verify an unsupported version yields a
  typed `incompatible_state` failure.

## 5. Working Projection

- [x] 5.1 Keep the no-tool structured projection runner and deterministic validation (schema
  bounds, existing identifiers, status promotion, trusted scope, retrieval-miss semantics) in
  `genai/state_projection/`; verify fabricated identifiers are rejected.
- [x] 5.2 Reduce the projection contract to one purpose, turn close: input is the prior
  projection, the exact utterance, the evidence cards added this turn, and the accepted answer or
  refusal/failure; remove the turn-intake and material-outcome purposes, the compaction-input
  digest, predecessor/ledger revisions, and source user-message binding; verify one repair is
  allowed and a failed replacement keeps the prior projection marked stale.
- [x] 5.3 Add multi-turn projection tests for evolving goals, resolved and unresolved referents,
  qualified hypotheses, and turn-close summaries; verify the next turn's first model call receives
  the projection and the new utterance rather than prior messages.

## 6. Shared Model, Retry, Cancellation, and Limit Controls

- [x] 6.1 Implement service-local model factories for planner, guardrail, projection, verifier,
  closure, and embedding clients using validated configuration and the SDK credential chain;
  verify unit tests inspect model IDs/options without constructing clients on invalid settings.
- [x] 6.2 Configure `ModelRetryMiddleware` and the equivalent bounded retry policy for direct
  structured calls with an explicit transient exception allowlist, exponential backoff, jitter,
  attempt cap, cancellation check, and turn deadline; verify permanent and validation errors
  receive no blind retry.
- [x] 6.3 Configure `ToolRetryMiddleware` on each nested agent for `retrieve` and `execute_sql`
  only, and the same bounded policy around `find_connections`' read, for transient database,
  serialization, rate, and transport failures; verify a physical retry keeps one fingerprint and a
  policy rejection is never retried.
- [x] 6.4 Configure `ModelCallLimitMiddleware(exit_behavior="end")` and `ToolCallLimitMiddleware`
  on the main agent and on both nested agents from settings, plus `asyncio.timeout` for the turn
  and `recursion_limit` as a safety net; verify each limit ends the run without a new call and the
  exhausted dimension is recorded on the turn.
- [x] 6.5 Propagate cooperative cancellation through model, embedding, database, and retry
  boundaries; verify no new attempt begins after cancellation is observed and owned resources
  close within the configured deadline.

## 7. Input and Evidence Guardrails

- [x] 7.1 Rebuild the input guardrail as `InputGuardrailMiddleware.before_agent` using the existing
  structured guard runner: `allowed` continues; refusal or `guardrail_unavailable` records the
  refusal as the turn's answer and jumps to end; verify injection, off-topic, unavailable, and
  valid investigation cases route correctly with zero evidence I/O when blocked.
- [x] 7.2 Keep batched evidence guardrails plus deterministic normalization, untrusted-content
  labels, and delimiters for every model-visible textual tool value, including chunks, structured
  row strings, and graph labels/properties; verify embedded instructions cannot change scope or
  findings.
- [x] 7.3 Add dataset `A-D1`, structured-row injection, graph-label injection, and legitimate
  investigative-language false-positive cases against the assembled agent with fake models;
  verify malicious text remains stored/citable as evidence but is never executed as instructions.
- [x] 7.4 Add PII-retention and telemetry-separation tests proving exact PII survives application
  state, history, and grounded answers while default traces and logs contain no prompt, evidence,
  row, tool, or model content.

## 8. Hybrid `search_evidence` Nested Agent

- [x] 8.1 Define versioned Pydantic `SearchIntent`, attempt records, evidence cards, and
  `SearchOutcome` with `sufficient`, `no_retrieved_support`, and `retrieval_incomplete`; verify
  malformed or model-authored scope performs no I/O.
- [x] 8.2 Implement concurrent BM25 and vector retrieval through the trusted evidence reader with
  `case_id` bound as a parameter and identical source/time filters, exclusions, top-k, deadlines,
  and bounded embedding calls; verify modality-specific failures map to safe typed partial outcomes.
- [x] 8.3 Implement deterministic weighted reciprocal-rank fusion, `chunk_id` deduplication,
  stable tie-breaking, provenance, modality ranks/scores, source locators, and content hashes;
  verify reordered modality results yield the same fused output.
- [x] 8.4 Replace `SearchEvidenceAgent`, `LangChainSearchPlanner`, and the separate relevance
  grader with a nested checkpointer-free `create_agent` whose only tool is `retrieve`: the tool
  keeps invocation-local fingerprints and seen chunks, rejects repeats without I/O, excludes the
  parent-supplied seen set, and returns a delimited untrusted candidate message; the nested agent
  judges sufficiency and returns a structured `SearchOutcome`; the outer tool validates selected
  IDs against retrieved IDs and builds the `ToolOutcome`.
- [x] 8.5 Add search unit tests with a scripted chat model for lexical-only, vector-only,
  overlapping, reformulated, exhausted, repeated-fingerprint, unretrieved-selection,
  partial-provider, and transient-retry cases; verify a miss is always described as unavailable
  retrieved support rather than factual absence and the nested message history is discarded.

## 9. Guarded `query_records` Nested Agent

- [x] 9.1 Define the bounded `QueryIntent`, server-owned schema description, typed parameter map,
  safe diagnostic classes, desired shape, and `QueryOutcome` contracts; verify context excludes
  credentials, role names, and unrestricted schema metadata.
- [x] 9.2 Implement the pinned PostgreSQL AST parser and fail-closed complete-tree policy for one
  read-only `SELECT` or side-effect-free CTE over versioned allowlisted `agent_read` views,
  columns, operators, casts, and functions; verify every unknown node, `SELECT INTO`,
  temporary/unqualified relation, `SET`/`set_config`/`current_setting`, or ambiguous parse is
  rejected.
- [x] 9.3 Add adversarial policy tests for multi-statements, DML/DDL, data-changing CTEs, `COPY`,
  transaction control, locks, catalogs, comments, unapproved functions, interpolation, and
  obfuscation; verify every candidate is blocked before DB I/O.
- [x] 9.4 Implement `execute_guarded_select` on the reader pool with a read-only transaction that
  first runs `SET LOCAL app.case_id`, then the authored statement with separated bound parameters
  inside an outer unweakenable limit, statement/lock/idle timeouts, row/byte caps, cancellation,
  provenance validation, and bounded diagnostic mapping; verify real-role tests cannot weaken any
  control and the scope never outlives the transaction.
- [x] 9.5 Replace `QueryRecordsAgent`, `LangChainSqlPlanner`, and the separate result judge with
  a nested checkpointer-free `create_agent` whose only tool is `execute_sql`: the tool runs the
  policy gate and guarded executor, keeps invocation-local plan fingerprints, rejects repeats
  without I/O, and returns rows or a safe failure class as a delimited untrusted message; the
  nested agent returns a structured `QueryOutcome`; the outer tool validates selected row IDs
  against returned rows. Drop the `SqlPlan` hard-constraint coverage proof.
- [x] 9.6 Add query unit tests with a scripted chat model for successful first plan, policy
  correction, schema correction, empty-result revision, transient physical retry, exhausted plans,
  repeated fingerprint, cancellation, and resource limits; verify the main agent receives one
  bounded typed outcome and never generated SQL.

## 10. Deterministic `find_connections` Tool

- [x] 10.1 Define validated graph-tool inputs and outputs for seed entity IDs, status, predicate,
  and time filters plus server-capped depth, path, node, edge, and row limits; verify model-supplied
  scope is rejected while requested limit values above maxima are deterministically capped.
- [x] 10.2 Implement deterministic case-scoped traversal as parameterized reader-pool queries with
  `case_id` bound on every hop, cycle prevention, stable path ordering, reference resolution, and
  preserved `confirmed` versus `proposed` status; verify unsupported paths are removed rather than
  returned without provenance.
- [x] 10.3 Add graph unit and real-database integration tests for dense components, cycles,
  predicate/time/status filters, proposed edges, missing references, unknown seeds, and cross-case
  seeds; verify output remains deterministic and within every configured bound.

## 11. Main Agent, Middleware, and Grounded Finalization

- [x] 11.1 Build the main `create_agent` in `genai/investigation/agent.py` with the three tools,
  `InvestigationAgentState`, `response_format=ToolStrategy(AnswerDraft)`, the
  `AsyncPostgresSaver`, and the middleware stack in design order; wire it in
  `bootstrap/runtime.py`; verify the compiled graph contains exactly the expected nodes and the
  three tool names.
- [x] 11.2 Implement the three tools as `@tool` functions with `ToolRuntime` in
  `genai/investigation/tools.py`: inject case, deadline, and cancellation from runtime context,
  emit allowlisted `custom` progress, invoke the nested agent or the deterministic traversal, and
  return the normalized `ToolOutcome`; delete `InvestigationToolDispatcher`.
- [x] 11.3 Implement `TurnIntakeMiddleware.before_agent`: reject a thread at the history bound
  with `thread_full`, mark a latest `running` turn as `interrupted` in history when a different
  `request_id` arrives, append the exact user message with the next sequence, open the turn, and
  reset `messages` to that user message; verify a resumed run does not re-run intake and history
  never gains a duplicate.
- [x] 11.4 Implement `ContextMiddleware.wrap_model_call`: system prompt from trusted
  instructions, control state, the projection, and bounded evidence cards; only the current turn's
  messages, trimmed deterministically to the token bound with a notice; verify `history`, prior
  turns, raw rows, and nested messages never reach the model.
- [x] 11.5 Implement `EvidenceIndexMiddleware.wrap_tool_call`: execute the tool, pass every
  model-visible string through the evidence boundary, upsert the evidence index, account usage,
  and return a compact `ToolMessage`; verify a multi-tool fake investigation checkpoints after each
  tool and the index matches the outcomes.
- [x] 11.6 Implement `GroundingMiddleware.after_model` over the `AnswerDraft` structured
  response: deterministic verification against the evidence index (case, content hash, source
  locator, material-claim coverage, proposed status, retrieval-miss semantics, size/format) plus
  the bounded no-tool entailment verifier; one repair via `jump_to="model"` with bounded
  violations; second failure records a safe code and jumps to end; verify invented, stale, and
  cross-case citations, unsupported absence claims, malformed verifier output, and unsupported
  entailment fail closed with no draft exposed.
- [x] 11.7 Implement `TurnCloseMiddleware.after_agent`: one no-tool closure call from indexed
  evidence when a limit ended the run and the reserve remains, otherwise a typed failure; the
  turn-close projection replacement or stale close; append the verified assistant message,
  refusal, or failure to `history`; set the turn status; remove the turn's working messages;
  verify the committed message is byte-identical to what the transport later streams and no model
  call starts after cancellation.
- [x] 11.8 Add agent tests with scripted models for refusal, no support, incomplete retrieval,
  limit exhaustion with closure, transient exhaustion, projection-stale continuation, final
  repair, cancellation, resume from an interrupted checkpoint with `None` input, and normal
  cross-source synthesis; verify every run reaches one explicit terminal status with the evidence
  index preserved.

## 12. FastAPI Lifecycle, SSE, and History API

- [x] 12.1 Implement the FastAPI application factory and lifespan that validates settings, starts
  the shared telemetry provider, builds the immutable runtime once, drains active turns
  cooperatively, and closes both pools exactly once; verify lifecycle tests cover healthy startup,
  partial-startup failure, and bounded shutdown.
- [x] 12.2 Implement `GET /health` as dependency-free liveness and `GET /ready` as the bounded
  read-only readiness aggregate; verify a PostgreSQL outage keeps health successful and returns
  readiness `503` within the configured deadline.
- [x] 12.3 Remove the identity middleware, `get_principal`, and the development authorizer from
  `api/dependencies.py` and the routers; use the public `thread_id` as the saver thread ID and
  write `app`, `public_thread_id`, and `case_id` checkpoint metadata; verify no route depends on a
  principal and the HTTP contract test no longer references owner headers.
- [x] 12.4 Update `InvokeTurn.prepare` for the case-only binding: validation, `thread_full`,
  immutable case check with `409 thread_case_conflict`, completed/failed replay,
  `409 request_in_progress`, `409 thread_busy`, changed-payload `409`, interrupted resume, and new
  turn; verify each outcome returns the documented status/code with `Retry-After` where specified
  and never starts a second executor for one thread.
- [x] 12.5 Implement the versioned problem-details serializer and after-header failure mapping so a
  turn-close failure emits its safe code as non-retryable and a persistence/delivery failure
  emits a retryable `run.failed` without changing turn status; verify no internal exception,
  prompt, SQL, provider/database text, evidence, credential, or stack trace reaches HTTP, SSE, or
  history.
- [x] 12.6 Update the SSE adapter's `updates` allowlist to the `create_agent` node names (model,
  tools, and each middleware hook node) mapped to the coarse public phases, keep `custom` progress
  from tools, comment heartbeats, and exactly one terminal event for a connected stream; verify
  raw chunks are never serialized and an unknown node name emits nothing.
- [x] 12.7 Implement two-phase answer emission that slices only the committed assistant message
  from final state into bounded `answer.delta` events, then emits `run.completed`; verify
  concatenated deltas exactly equal history and no delta is emitted when validation or commit
  fails.
- [x] 12.8 Implement disconnect handling that propagates cancellation, stops new attempts, releases
  the thread lock, and preserves the last checkpoint; verify a subsequent read reports
  `interrupted`, the same `request_id` resumes, and a different `request_id` supersedes without
  duplicate messages.
- [x] 12.9 Update `ReadHistory` and the routers: `GET /v1/threads` over `checkpointer.alist` with
  the `app` metadata filter and `case_id` in each summary, `GET /v1/threads/{thread_id}/messages`
  over `agent.aget_state` with `404` for unknown threads, `interrupted` derivation from lock
  state, bounded keyset pages, and endpoint-scoped opaque cursors without an owner component;
  verify DTOs expose `turn_id` and status, page continuity holds across appends, and no checkpoint
  IDs, state, SQL, tool payloads, or diagnostics leak.
- [x] 12.10 Implement `DELETE /v1/threads/{thread_id}` through a `DeleteThread` application
  action: acquire the thread lock, `404` when no checkpoint exists, `409 thread_busy` when locked,
  `checkpointer.adelete_thread`, `204`; verify a deleted thread reads `404`, re-invocation binds a
  fresh thread, and other threads are untouched.

## 13. Turn Observability

- [x] 13.1 Re-parent the finite `invoke_workflow investigation_turn` root and its phases onto the
  `create_agent` hooks and nodes (guardrail, model attempts, tool executions, nested agents,
  grounding, turn close, streaming) using the existing observability library; verify normal turns
  have one root, resumed turns start a new root linked to persisted prior trace context, and no
  root spans the lifetime of a conversation.
- [x] 13.2 Keep each physical model/tool retry as its own child span with shared logical operation
  identity and aggregate API/agent/model TTFC, answer-ready, completion, call, retry, token,
  result, and cancellation measurements across the main and nested agents; verify SSE deltas and
  state fields produce no per-item spans, events, logs, or metric series.
- [x] 13.3 Add bounded structured boundary logs, error ownership, trace correlation, and safe
  cancellation status while excluding high-cardinality IDs from metrics; verify one representative
  failure yields one owning log and no duplicated error record.
- [x] 13.4 Enforce capture-off telemetry defaults independently of application PII storage and
  keep telemetry export failures non-fatal; verify capture-off fixtures contain no user/model,
  evidence, row, SQL, tool, checkpoint, or secret content and an exporter failure leaves the turn
  result unchanged.

## 14. Container, Compose, and Documentation Integration

- [x] 14.1 Add a lean multi-stage `services/investigation_agent/Dockerfile` with scoped uv install,
  non-root runtime, health/readiness support, and no development dependency or owner credential;
  verify the image builds and inspection shows only runtime dependencies and the intended entry
  point.
- [x] 14.2 Add `agent-db-init` and `investigation-agent` to Compose using the existing
  `postgres-app` service and volume, ordered database-healthy, ingestion complete-or-skip,
  initializer complete, then API; verify rendered Compose contains no second PostgreSQL service or
  volume and the serving container has no owner credential.
- [x] 14.3 Remove the development identity variables from Compose and the committed examples and
  add the new limit settings; verify `docker compose config` succeeds.
- [x] 14.4 Update `docs/DESIGN.md` and `docs/DATA_MODEL.md` for the single `create_agent`, nested
  tool agents, turn-close projection, evidence index, hybrid retrieval, guarded SQL with session
  case scope, checkpoint-backed history, thread deletion, SSE, and provenance semantics, and list
  authentication, case authorization, single-replica locking, forced RLS, and database leases
  under production evolution; verify documented contracts match the delta specs.
- [x] 14.5 Update `README.md`: remove the identity adapter paragraph and `DEVELOPMENT_*`
  variables, add the `DELETE /v1/threads/{thread_id}` example, describe the single-agent loop,
  and state that the prototype has no authentication; verify every command and environment name
  matches the resolved service and Compose configuration.

## 15. Integrated and Live Validation

- [x] 15.1 Run the investigation service unit suite and verify state, projection, guardrails,
  retry/limit logic, SQL policy, retrieval fusion, graph traversal, middleware, API serializers,
  and telemetry contracts pass without network or database access.
- [x] 15.2 Run the disposable-PostgreSQL integration suite with the real owner, reader, and writer
  roles; verify view scope isolation, pool reuse across cases, privilege matrix, saver setup
  idempotence, thread deletion, and index-plan checks for representative BM25, vector, structured,
  and graph queries pass.
- [x] 15.3 Run the pinned-framework and public API/SSE contract suites; verify hook jumps, limit
  exit behavior, nested custom events, sync durability, `None`-input resume, problem details
  including both `409` codes, replay, `204`/`404`/`409` deletion, and terminal-event invariants
  remain stable.
- [x] 15.4 Run an end-to-end test with scripted models against PostgreSQL from first message
  through multiple tools, an interruption after a mid-turn checkpoint, resume with the same
  `request_id`, verified commit, streamed answer, paginated history, a second turn using the
  projection, and deletion; verify no duplicate user or assistant message and byte-identical
  replay.
- [x] 15.5 Run adversarial end-to-end cases for prompt injection, embedded evidence instructions,
  structured-row/graph-label injection, hostile SQL, cross-case identifiers, and cancellation with
  late results; verify no cross-case evidence, private state, raw diagnostics, or partial draft is
  observable.
- [x] 15.6 Run repository Ruff check/format, strict mypy, the default pytest suite, and all service
  package/import contracts; verify every command completes with no new warning or unapproved
  skip.
- [ ] 15.7 Validate Compose, run `agent-db-init` twice, start the local stack, restart the serving
  container during an in-flight scripted test turn, and verify the thread reads as interrupted,
  resumes on retry, and database data survive without deleting volumes.
- [ ] 15.8 With explicit paid-provider opt-in and content capture off, run a live Bedrock
  multi-turn investigation that exercises hybrid search, one query correction, graph traversal,
  citations, progress SSE, replay, history pagination, and deletion; verify the committed answer
  is grounded and no application content appears in Tempo or Loki.
- [ ] 15.9 Run `openspec validate add-investigation-agent-service --strict` after recording
  implementation evidence, and verify the change is complete and ready for archive without
  dropping checkpoints, evidence, roles, or Compose volumes.

### Validation evidence (2026-09-02)

- `uv run pytest -q`: 355 passed, 16 deselected by the repository's default marker policy.
- `uv run pytest -m integration services/investigation_agent/tests/integration -q`: 6 passed
  against the disposable `app_test` ParadeDB database, including the scripted interruption,
  checkpoint resume, replay, history, projection, and deletion flow.
- The adversarial matrix (assembled-agent injection tests, guarded-SQL policy tests, real
  PostgreSQL case/role isolation, and public SSE privacy/cancellation tests): 40 passed.
- `uv run ruff format --check .`, `uv run ruff check .`, and strict repository `mypy`: passed;
  mypy checked 293 source files.
- `docker compose config --quiet` passed. `agent-db-init` completed twice with `--no-deps`,
  confirming the Compose-level idempotent path without restarting provider-backed ingestion.
- `openspec validate add-investigation-agent-service --strict` passed. Task 15.9 remains open
  because tasks 15.7 and 15.8 are intentionally incomplete, so the change is not yet archive-ready.
- The full-stack restart and paid-provider validation were not run: starting the dependency chain
  activated provider-backed ingestion from the local environment, which was stopped rather than
  continuing without explicit paid-provider opt-in.
