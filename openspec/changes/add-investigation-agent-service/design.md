## Context

The current repository ends at a populated PostgreSQL evidence store. Ingestion owns canonical
`records`, `entities`, and `relationships`, rebuildable structured projections, BM25 and vector
indexes, and exact `SourceRef` locators. There is no request-serving process, retrieval runtime,
conversation model, or durable investigation agent. See [proposal.md](proposal.md) for the product
motivation and the delta specs for the binding behavior.

Several existing constraints shape the implementation:

- The evidence model remains authoritative. Agent state may cache bounded references and semantic
  projections, but may not become another evidence source of truth.
- The active design treats source content as untrusted, preserves `confirmed` versus `proposed`
  relationship semantics, and defines a retrieval miss as lack of retrieved support rather than
  proof of absence.
- `SourceRef` currently contains only `record_id` and a field or exact text-span locator. Tool
  envelopes therefore carry trusted `case_id` and `content_hash` alongside each reference instead
  of silently changing the shared provenance type in this change.
- The repository pins Python 3.13, LangChain 1.3.x, LangGraph 1.2.x,
  `langgraph-checkpoint-postgres` 3.1.x, Bedrock integrations, and an internal OpenTelemetry
  library. FastAPI, SSE transport, PostgreSQL checkpointing, connection pooling, and a
  PostgreSQL-dialect AST parser are new service dependencies.
- `postgres-app` already provides PostgreSQL 17, pg_search, pgvector, and persistent local storage.
  Ingestion connects with the database owner credential (`POSTGRES_APP_USER`).
- This is a three-day take-home prototype that runs as one service replica in Docker Compose with
  no authentication. The design invests in grounding, safe SQL, bounded agent behavior, and
  durable multi-turn memory, and explicitly defers identity, authorization, multi-replica
  coordination, lease fencing, and database-enforced row isolation to the production evolution
  described in `docs/DESIGN.md`.

## Goals / Non-Goals

**Goals:**

- Give every turn a bounded, recoverable execution owned by one LangChain `create_agent` with
  three case-scoped evidence tools and a PostgreSQL checkpointer.
- Keep the durable model memory between turns a validated semantic projection rather than an
  ever-growing message list; inside a turn, use the framework message list as-is.
- Serve frontend history from the same checkpointed state through the public LangGraph API, so one
  persistence model covers recovery and product history.
- Treat model-authored SQL as hostile input and enforce a deterministic policy gate, a read-only
  role, and server-set case scope before execution.
- Stream useful, sanitized progress while ensuring no unverified answer text or private model
  stream reaches the client.
- Prefer built-in LangChain middleware and hooks over custom orchestration wherever they provide
  the required behavior.

**Non-Goals:**

- Authentication, ownership, or case authorization of callers. Any caller may address any thread
  or case in this prototype.
- A second PostgreSQL deployment, a dedicated graph database, or a generic semantic-memory store.
- Multi-replica execution, distributed leases, fencing generations, or cross-process recovery
  coordinators. One replica owns all threads in this scope.
- Forced row-level security, signed scope tokens, or per-case database roles. The prototype
  boundary is the AST gate plus a read-only role plus server-controlled session scope.
- Direct frontend database access or a public raw-checkpoint inspection API.
- General SQL access, write-capable agent tools, cross-case investigation, or autonomous case
  creation.
- PII redaction from evidence, checkpoint state, history messages, or grounded answers.
  Telemetry content capture still defaults off.
- Persisted memory inside the retrieval and query sub-agents.
- A custom outer state graph, a one-decision planner boundary, a deterministic evidence-ledger
  reducer, or projection compaction after every tool call.
- Live pass-through of planner, reasoning, guardrail, sub-agent, projection, or unverified
  final-model tokens.
- Production identity-provider selection, retention/legal-hold policy, horizontal autoscaling
  policy, or changing the existing Collector routing topology.

## Decisions

### 1. Add one independently deployable, capability-oriented service

Create `services/investigation_agent` as its own uv workspace member and ASGI image. The service
owns HTTP transport, turn orchestration, checkpoint-backed history, and all investigation-specific
GenAI code. It imports shared evidence/provenance types and generic observability primitives, but
does not import ingestion internals or a sibling service package.

The source layout is capability-first and flat within each capability:

```text
services/investigation_agent/
  pyproject.toml
  Dockerfile
  src/investigation_agent/
    main.py
    bootstrap/{app.py,runtime.py}
    config/{settings.py,secrets.py}
    core/{context.py,errors.py}
    api/{dependencies.py,problems.py,sse.py}
    api/routers/{health.py,investigations.py,threads.py}
    application/{invoke_turn.py,read_history.py,delete_thread.py,thread_locks.py}
    domain/{investigation_state.py,history.py,tool_outcome.py}
    ports/{evidence_reader.py}
    adapters/postgres/{pools.py,evidence_reader.py,initializer.py,checkpointer.py}
    genai/investigation/{agent.py,middleware.py,tools.py,connections.py,grounding.py,prompts.py,schemas.py}
    genai/evidence_search/{agent.py,retrieval.py,prompts.py,schemas.py}
    genai/record_query/{agent.py,executor.py,policy.py,prompts.py,schemas.py}
    genai/state_projection/{compactor.py,prompts.py,schemas.py}
    genai/guardrails/{middleware.py,prompts.py,schemas.py}
    genai/shared/{budgets.py,models.py,retries.py}
    observability/{events.py,instrumentation.py}
  tests/{unit,integration,contract,e2e}/
```

`bootstrap/runtime.py` is the composition root: it validates settings, starts telemetry, creates
the two purpose-specific pools, constructs the evidence reader, model clients, the two nested tool
agents, and the three tools, creates the `AsyncPostgresSaver` on the writer pool, builds the main
`create_agent` once with that saver, and returns one immutable runtime container. FastAPI
dependencies retrieve already-built interfaces from application state. Domain and application
modules never import FastAPI, psycopg, LangChain, or provider clients.

Alternatives rejected:

- Adding the endpoint to ingestion couples a one-shot batch lifecycle to a long-running API.
- Creating a broad shared `genai` package before a second consumer exists would hide ownership and
  freeze abstractions prematurely.

### 2. Use one PostgreSQL deployment with two runtime roles and two pools

The service needs two kinds of database access: read-only evidence access for the tools, and
read/write access to LangGraph checkpoint tables. They share `postgres-app` physically and are
separated by role, schema, and pool:

| Purpose | Schema/surface | Runtime role | Pool | Created by |
|---|---|---|---|---|
| Evidence tools | evidence tables plus `agent_read` views | `agent_reader` | read-only pool | `agent-db-init` |
| LangGraph checkpoints and history | `agent_runtime` | `agent_writer` | saver pool | `agent-db-init` |

`agent_reader` has `SELECT` on the canonical evidence tables and projections that trusted retrieval
and graph code query with bound parameters, and on the `agent_read` views that model-authored SQL
may reference. It has `default_transaction_read_only = on`, no `TEMP`, no `CREATE`, and no
privilege on `agent_runtime`. `agent_writer` owns nothing outside `agent_runtime` and has no
privilege on evidence. Ingestion keeps its owner credential and receives no agent-schema grant.

Two pools rather than one ensure a model-authored query can never be executed with the writer role,
and let the saver keep its own connection settings (`autocommit=True`, `prepare_threshold=0`,
`dict_row`, `agent_runtime` search path).

Alternatives rejected:

- One role for both purposes turns an SQL gate regression into a checkpoint-write bug.
- Three or four roles with security-definer cross-schema functions, forced RLS, and signed scope
  tokens were designed and rejected for this scope: they protect against a compromised writer or a
  bypassed gate in a multi-tenant deployment, which is not the prototype's threat model.
- A second PostgreSQL container adds operational cost without improving any trust boundary here.

### 3. Initialize schemas outside request-serving processes

A one-shot `agent-db-init` Compose step, running with the existing database owner credential,
owns every database change introduced by this proposal. It runs after ingestion has completed or
skipped and, in order: verifies the expected evidence schema and index objects exist; creates the
`agent_reader` and `agent_writer` login roles with passwords from its environment; creates the
`agent_read` schema and its views; creates the `agent_runtime` schema; runs
`AsyncPostgresSaver.setup()` with `search_path` set to `agent_runtime`; applies grants; and records
the applied initializer version in a small `agent_runtime.schema_version` table. The step is
idempotent and never drops data.

The serving process never calls `setup()` and has no DDL permission. Readiness performs bounded,
read-only checks for the recorded initializer version, the `agent_read` views, the BM25/vector
dependencies, and both pool connections. It does not invoke a paid model. `GET /health` checks
only that the process/event loop is alive.

### 4. Make the HTTP contract unauthenticated, case-bound, idempotent, and thread-serialized

The transport surface is:

```text
GET    /health
GET    /ready
POST   /v1/agent/invoke
GET    /v1/threads
GET    /v1/threads/{thread_id}/messages
DELETE /v1/threads/{thread_id}
```

The invocation body contains `request_id`, `thread_id`, `case_id`, and one bounded `message`.
There is no principal, identity middleware, or authorization port. The public `thread_id` is the
saver `thread_id`. A new `thread_id` binds its case into the immutable `control` section at the
first checkpoint; an existing thread invoked with a different `case_id` receives `409
thread_case_conflict`. The binding protects state integrity and tool scoping, not access. Case
scope for every tool comes from that binding through trusted runtime context, never from model
arguments.

**Idempotency.** Every accepted turn records its `request_id` in state. If the latest state shows
the same `request_id` as a completed or failed turn, the service replays the stored assistant
message or safe failure code through the SSE contract without running the agent. If it shows the
same `request_id` on an `interrupted` turn, the service resumes the agent from the last checkpoint
with `None` input. A different `request_id` arriving after an interrupted turn starts a new turn;
the intake hook records the abandoned turn as `interrupted` in history first.

**Serialization.** The application holds one `asyncio.Lock` per thread ID for the lifetime of a
turn or deletion. A second request for the same thread while the lock is held receives `409
thread_busy` (different `request_id`) or `409 request_in_progress` (same `request_id`), both with
`Retry-After`. Different threads run concurrently. This is explicitly a single-replica mechanism;
the production evolution replaces it with a database lease.

**Crash semantics.** A turn whose latest checkpoint says `running` while no in-process lock exists
is by definition interrupted, because only this process can run turns. History reads report it as
`interrupted`; no sweep or reconciler is needed. Tools are read-only and hooks are idempotent, so
a resumed run may repeat the last unfinished step.

**Deletion.** `DELETE /v1/threads/{thread_id}` acquires the thread lock, calls
`AsyncPostgresSaver.adelete_thread`, and returns `204`. An unknown thread returns `404`; a thread
with an executing turn returns `409 thread_busy`. Deletion removes every checkpoint and write for
the thread and is not recoverable. Evidence is never touched.

Cancellation is cooperative and checked before every new model/tool attempt. A disconnect before
the answer is committed cancels the running task and lets the last checkpoint stand; the next
request on the thread sees the turn as interrupted. A disconnect after the turn-close checkpoint
does not undo the turn; a repeat of the same request replays the committed answer.

Authentication and case authorization are the first production additions: a FastAPI dependency in
front of these routes that resolves a principal and checks case access, plus an owner metadata
filter on thread listing. Neither requires a change inside the agent.

### 5. Translate LangGraph streaming into a stable, safe SSE protocol

Run the agent with LangGraph v2 streaming, internal modes `updates` plus `custom`, and
`durability="sync"` so each checkpoint completes before the next node begins. `updates` provides
node-level lifecycle signals for the model node, the tool node, and each middleware hook; `custom`
carries deliberately emitted safe counters from tools. The HTTP adapter maps node names through an
allowlist to coarse public phases and never serializes raw LangGraph chunks. `messages` mode is
deliberately not connected to the public route because it contains tokens from every model call,
including guardrails, nested agents, projection, and rejected drafts.

The public envelope has `schema_version`, `event`, `thread_id`, `turn_id`, `timestamp`, and
bounded `data`. The event types are:

```text
run.started
progress       # allowlisted phase/tool label, attempt/count only
answer.delta   # exact slice of the already committed assistant message
run.completed  # message ID, citations, status
run.failed     # stable code, safe text, retryability
```

SSE comments may provide heartbeats. Progress never includes prompts, reasoning, generated SQL,
query parameters, raw rows, chunks, tool input/output, or internal error text. Before response
headers, versioned problem details represent validation, idempotency, case-binding, and
busy-thread failures. After `run.started`, a connected stream receives exactly one terminal event;
a disconnected stream may receive none. Authoritative turn status always comes from state, never
from a delivery event: a `run.failed` emitted because checkpointing itself failed is retryable and
leaves the turn interrupted rather than failed.

Final text uses two-phase release. The model produces a complete private `AnswerDraft` as its
structured response; the grounding hook validates and, if necessary, repairs it, and the
turn-close hook appends the exact final assistant message to history. The checkpoint written when
the agent run ends is the durable product commit. Only then does the transport split that persisted
string into bounded `answer.delta` chunks. This is real streaming at the API boundary, but
intentionally not raw model token pass-through: once a token is sent it cannot be retracted after
a citation failure.

No internal span is left current across an asynchronous generator `yield`. Streaming telemetry is
recorded around finite preparation/emission sections and aggregated over the answer, never once per
delta.

### 6. Use one `create_agent` with three tools and middleware-owned turn control

The durable owner is a single LangChain `create_agent` built once with the `AsyncPostgresSaver`,
a custom `state_schema`, `response_format=ToolStrategy(AnswerDraft)`, and exactly the three tools
`search_evidence`, `query_records`, and `find_connections`. Runtime context (case, thread,
request, deadline, cancellation, clients) is passed through `context` and `ToolRuntime`; none of it
is serialized into state, and the model can neither read nor write it.

```text
create_agent(
  model=bedrock_chat,
  tools=[search_evidence, query_records, find_connections],
  state_schema=InvestigationAgentState,
  response_format=ToolStrategy(AnswerDraft),
  checkpointer=AsyncPostgresSaver,
  middleware=[
    TurnIntakeMiddleware,        # before_agent: mark abandoned turn interrupted, open turn, append user message
    InputGuardrailMiddleware,    # before_agent: LLM verdict; blocked -> refusal answer, jump_to end
    ModelRetryMiddleware, ToolRetryMiddleware,
    ModelCallLimitMiddleware(run_limit=..., exit_behavior="end"),
    ToolCallLimitMiddleware(run_limit=...),
    ContextMiddleware,           # wrap_model_call: system prompt from projection + trimmed turn messages
    EvidenceIndexMiddleware,     # wrap_tool_call: normalize outcome, guard text, index evidence
    GroundingMiddleware,         # after_model: verify AnswerDraft; one repair via jump_to model
    TurnCloseMiddleware,         # after_agent: closure, projection refresh, history commit, message cleanup
    telemetry middleware
  ],
)
```

The ReAct loop of `create_agent` is used as-is: the model may call one or several tools per step,
the built-in tool node executes them, and the loop continues until the model returns the
structured `AnswerDraft` or a limit ends the run. Every model, tool, and hook node is checkpointed
by the saver under `durability="sync"`, so crash recovery has the same granularity as a custom
graph without a second orchestration layer. Hard limits are `ModelCallLimitMiddleware` and
`ToolCallLimitMiddleware` on the main agent (`run_limit` per turn, optional `thread_limit`
across the thread), an `asyncio.timeout` around the invocation, and the LangGraph
`recursion_limit` as a safety net.

Middleware responsibilities, all in `genai/investigation/middleware.py`:

- `TurnIntakeMiddleware.before_agent`: if `turn.status` is `running` with a different
  `request_id`, mark that turn `interrupted` in history; open the new turn, append the exact user
  message to `history`, and reset the working `messages` list to that user message.
- `InputGuardrailMiddleware.before_agent`: run the structured input guardrail; on `allowed`
  continue; on refusal or `guardrail_unavailable`, write the refusal as the turn's answer and
  `jump_to="end"` so no evidence tool runs. `TurnCloseMiddleware` still commits it.
- `ContextMiddleware.wrap_model_call`: build the system prompt from trusted instructions, control
  state, the current `WorkingProjection`, and the evidence index; pass only the current turn's
  messages, trimmed deterministically to the configured token bound with an explicit truncation
  notice. `history` is never model input.
- `EvidenceIndexMiddleware.wrap_tool_call`: execute the tool, normalize its `ToolOutcome`, pass
  every model-visible string through the untrusted-evidence boundary, upsert bounded evidence
  cards into `evidence`, record consumption in `usage`, and return a compact `ToolMessage`.
- `GroundingMiddleware.after_model`: when the model response carries the `AnswerDraft` structured
  output, run deterministic citation verification and the LLM entailment verifier. On failure with
  no repair used, append a bounded repair instruction and `jump_to="model"`; on second failure,
  record `failed` with a safe code and `jump_to="end"`.
- `TurnCloseMiddleware.after_agent`: if the run ended by call limit without an accepted answer,
  make at most one no-tool closure call from indexed evidence when the closure reserve allows,
  otherwise record a typed failure. Then refresh the `WorkingProjection` from the previous
  projection, the exact utterance, the evidence added this turn, and the final answer; validate it
  or keep the prior projection marked stale. Finally append the verified assistant message (or
  refusal/failure) to `history`, set the turn status, and remove the turn's working messages.

Alternatives rejected:

- A custom outer `StateGraph` with a one-decision `create_agent` invocation per step, a
  deterministic ledger reducer, and projection compaction after every tool. It duplicated the
  framework loop, doubled model calls, and required after-model hacks to prevent tool execution.
  Its guarantees (checkpoint per step, trusted tool execution, budget accounting) are provided by
  `create_agent` with a checkpointer and limit middleware.
- Independent persistent agents per source fragment cross-source strategy and create competing
  memories.
- Making the main agent repair SQL or reformulate retrieval wastes cross-source context on a
  bounded, schema-local loop; those loops stay inside the nested tool agents.

### 7. Persist compact state and replace semantic memory at turn close

`InvestigationAgentState` extends the framework `AgentState` (`messages`,
`structured_response`) with five application sections:

| Section | Writer | Contents |
|---|---|---|
| `control` | service only | case binding, state schema and policy versions |
| `turn` | intake, grounding, close hooks | turn/request IDs, request fingerprint, user message ID, status, safe failure code, repair count, exhausted limit, prior trace carrier |
| `evidence` | evidence-index hook only | bounded map of evidence ID to card: `SourceRef`, trusted case, content hash, status, tool, short display fields |
| `projection` | close hook only, after validation | user goal, dialogue summary, referent bindings, focus IDs, active findings, qualified hypotheses, open questions, next steps, source turn, stale flag |
| `history` | intake and close hooks only | ordered product transcript: message ID, sequence, turn ID, request ID, role, exact content, citations, turn status, timestamp |

`messages` holds only the current turn's working conversation. `TurnCloseMiddleware` removes those
messages after committing the answer, so the next turn starts from the projection plus the new
utterance. `history` is storage for the frontend, never model input, and is bounded by the
configured maximum turns per thread; a thread at the bound rejects a new turn with a typed
`thread_full` error.

The evidence index contains bounded cards, not raw result sets. Cards are upserted by stable
evidence ID; status is never promoted and provenance is never overwritten. When the configured
bound is reached, older cards not referenced by the projection or the current turn are dropped and
a coverage notice is recorded. Full evidence remains recoverable from the evidence store by
reference.

The projection compactor is a no-tool structured-output call made once per turn at close. It
receives the previous projection, the exact utterance, the cards added this turn, and the accepted
answer, and produces a full replacement. Deterministic validation rejects unknown IDs, status
promotion, changed control values, unbounded fields, and absence claims derived from misses. One
malformed projection may receive one repair. If no valid replacement is produced, or the closure
reserve or provider prevents the call, the prior projection is kept and marked stale. No new model
call begins after cancellation.

Alternatives rejected:

- Compacting after every tool result keeps the planner from ever seeing raw tool output but costs
  one model call per tool call and a second orchestration layer. Inside a turn, bounded typed
  `ToolMessage`s with normalized text are sufficient context.
- A generic message summarizer instead of the typed projection produces free text that cannot be
  validated against evidence IDs.

### 8. Implement hybrid `search_evidence` as a nested agent with one retrieval tool

The main agent supplies a bounded `SearchIntent` containing the exact question, objective, stable
hard/soft constraints, selected evidence IDs, and previously seen chunk IDs from the evidence
index. The tool injects trusted case, deadline, and cancellation from `ToolRuntime.context` and
invokes a nested checkpointer-free `create_agent` whose only tool is `retrieve`. `retrieve` fetches
BM25 and vector candidates for one proposed query through the trusted evidence reader, which binds
`case_id` as a parameter. The two modalities run concurrently with the same case/source/time
filters and configured top-k. A deterministic reciprocal-rank fusion policy combines configured
modality weights, deduplicates by `chunk_id`, and breaks ties by stable ID. It does not ask the
model to merge scores.

The nested agent judges relevance and sufficiency against the exact question and typed objective
from the fused candidates returned as a delimited untrusted `ToolMessage`, and may reformulate.
`retrieve` keeps an invocation-local set of normalized query fingerprints and seen chunk IDs: a
repeated fingerprint returns a typed rejection without I/O, later attempts exclude every chunk
seen earlier, and `ToolCallLimitMiddleware(run_limit=3)` plus `ModelCallLimitMiddleware` bound the
loop. The nested agent's structured response is a `SearchOutcome` with `sufficient`,
`no_retrieved_support`, or `retrieval_incomplete`, selected evidence IDs, and attempt summaries.
The tool validates that every selected ID was actually retrieved in this invocation and builds the
`ToolOutcome`; the model cannot manufacture a reference or change the envelope. The nested message
history is discarded on return.

Alternatives rejected:

- Vector-only search misses identifiers, exact references, amounts, and names.
- A single fixed hybrid query does not meet the agreed self-correction behavior.
- A hand-written Python loop around a structured planner call reimplements the agent loop that
  `create_agent` already provides.

### 9. Give `query_records` the whole SQL correction loop, behind a gate and a read-only role

The main agent supplies a `QueryIntent`, never raw SQL. It contains the exact question, one query
objective, hard/soft constraints, relevant evidence IDs, and desired result shape. The tool injects
a versioned, server-owned description of the allowlisted `agent_read` views and columns and
invokes a nested checkpointer-free `create_agent` whose only tool is `execute_sql`. Credentials,
role names, unrestricted schema metadata, and the parent's messages are never nested-agent context.

`execute_sql` accepts one statement plus a typed parameter map. It runs the deterministic policy
gate, then the guarded executor, and returns either bounded rows or a safe failure class (parse,
policy, schema, execution, empty) as a delimited `ToolMessage`, so the nested agent can revise.
It keeps an invocation-local set of plan fingerprints computed from the canonical parsed statement,
typed parameters, and allowlist version; a repeat is rejected without I/O.
`ToolCallLimitMiddleware(run_limit=3)` bounds distinct plans. The nested structured response is a
`QueryOutcome` with status, selected row evidence IDs, and attempt summaries; the tool validates
selected IDs against rows actually returned in this invocation.

Use a pinned PostgreSQL-dialect AST parser and walk the complete tree. The policy accepts exactly
one `SELECT` with read-only CTEs and versioned allowlisted views, columns, operators, casts, and
functions. It rejects DML/DDL, data-changing CTEs, `COPY`, `SELECT INTO`, `SET`, `set_config`,
`current_setting`, transaction control, locks, multiple statements, temporary or unqualified
relations, catalog/information-schema access, unapproved functions, suspicious comments, and
constructs the policy walker does not understand. Parse ambiguity fails closed. Every authored
relation must use its schema-qualified `agent_read` allowlist identity; model values are a separate
Pydantic parameter map and are never interpolated into SQL.

**Case scope.** Every `agent_read` view filters on `case_id = current_setting('app.case_id', true)`.
Before executing an approved statement, trusted executor code opens a read-only transaction on the
reader pool, runs `SET LOCAL app.case_id = $1` with the case from runtime context, and then runs
the authored statement wrapped in a server-owned outer query with an unweakenable row cap. Because
the reader role is `default_transaction_read_only`, the gate denies every setting-changing
construct, and `SET LOCAL` dies with the transaction, the model cannot observe another case through
this path. Statement, lock, and idle timeouts, row and encoded-byte limits, and cancellation apply.
Database details are mapped to a small safe taxonomy before returning to the nested agent.

This is the prototype isolation boundary and is documented as such. Forced RLS, signed scope
tokens, and a facade owner role are the production evolution if the service becomes multi-tenant
or the reader role ever gains a broader surface.

Alternatives rejected:

- A typed filter DSL instead of model-authored SQL loses the agreed self-correcting query
  behavior; it remains the documented fallback if the gate cannot be made trustworthy.
- Injecting a `case_id` predicate into the AST is fragile across joins and subqueries; a view-level
  filter on a server-set session variable is simpler to reason about and test.
- Requiring each plan to prove hard-constraint coverage before execution was dropped: the policy
  gate and the nested agent's own judgement of the returned rows are sufficient for the prototype.

### 10. Keep `find_connections` deterministic and source-preserving

`find_connections` receives seed entity IDs plus explicit relationship status, predicate, and
time filters. Server maxima cap depth, path count, nodes, edges, and rows regardless of requested
values. Traversal prevents cycles and runs as trusted parameterized queries on the reader pool with
`case_id` bound on every hop. Every vertex and edge carries stable IDs and source references; an
unresolved reference removes the path from supported results. `proposed` edges retain that status
and are never worded as confirmed later.

The graph remains relational. Current traversal depth and fixture scale do not justify a separate
graph platform, and the bounded function is easier to scope and test.

### 11. Use LLM guardrail middleware without applying PII redaction

`InputGuardrailMiddleware` is a `before_agent` hook backed by a no-tool structured guard model.
It classifies the current user utterance for prompt injection and investigation-domain relevance.
`allowed` proceeds; `prompt_injection` and `off_topic` produce a stable refusal; an indeterminate
result after transient retries produces `guardrail_unavailable`. A refusal is a completed product
response, not a provider exception: the hook records it as the turn's answer and jumps to the end
of the run, where the close hook appends it to history like any other safe answer.

Every model-visible textual value originating in evidence passes through deterministic
normalization/labeling and the batched evidence guard inside `EvidenceIndexMiddleware` before it
enters the main model, the projection, or the answer verifier. Nested tool agents apply the same
boundary to their own `ToolMessage`s. This includes retrieved chunks, string-valued structured-query
columns, and graph labels/properties, not only document text. Suspicious text is retained as
evidence and may still be cited, but is marked untrusted, strongly delimited, and never interpreted
as an instruction. Case scope, tool selection limits, and state transitions never come from source
text, so the LLM guardrail is an additional semantic layer rather than the only defense. Dataset
case `A-D1` plus structured-row and graph-label injection variants are required regressions.

No `PIIMiddleware` is installed and no application-level masking is performed. Exact accepted
messages and evidence may include phones, accounts, names, and other PII. This decision does not
put that content into telemetry: GenAI content capture defaults off, secrets are always excluded,
and logs use IDs/counts/error classes only.

### 12. Separate infrastructure retries, semantic retries, and hard limits

Every `create_agent` instance and no-tool structured model call uses `ModelRetryMiddleware` (or
the equivalent bounded retry policy for direct structured calls) with an explicit transient
provider exception tuple, bounded exponential backoff, jitter, and the turn deadline.
`ToolRetryMiddleware` on the nested agents wraps only `retrieve` and `execute_sql` for transient
database, embedding, and transport failures, and the same policy wraps `find_connections`' single
read. A physical retry repeats the identical operation; each physical attempt consumes time budget
and receives its own span. The main agent never retries a whole nested invocation after partial
work; it records the typed outcome and lets the model decide what comes next.

Pydantic failures, SQL policy, missing references, empty results, relevance misses, and semantic
insufficiency are normal typed outcomes. Generic retry does not repeat them. Only the nested agent
that owns the loop may formulate a new semantic attempt, within its tool-call limit.

Hard limits per turn: `ModelCallLimitMiddleware` and `ToolCallLimitMiddleware` on the main agent,
`ModelCallLimitMiddleware` and `ToolCallLimitMiddleware` on each nested agent, `asyncio.timeout`
for elapsed time, row/byte/path caps inside the tools, a context token bound in
`ContextMiddleware`, and an answer size bound in `GroundingMiddleware`. When a limit ends the run
without an accepted answer, `TurnCloseMiddleware` may make one no-tool closure call from indexed
evidence if the closure reserve allows; otherwise it records a typed failure. Exhaustion is an
explicit state transition, never a prompt asking the model to stop.

The core error taxonomy separates validation, conflict, policy-rejected, no-support/incomplete,
transient-exhausted, budget-exhausted, cancelled, dependency-unavailable, incompatible-state, and
internal failures. Adapters translate library and provider errors once. Public errors expose only
stable codes, safe text, and retryability.

### 13. Verify grounding before committing or streaming an answer

The final answer is the main agent's structured `AnswerDraft` containing renderable sections and
material claims, each with evidence IDs from the evidence index. `GroundingMiddleware.after_model`
runs deterministic verification: every ID resolves in the current index, case/content hash/source
locator match, material facts have citation coverage, `proposed` qualification is preserved, and an
absence conclusion based only on empty/incomplete retrieval is rejected. A bounded no-tool
grounding verifier then evaluates each claim against only its cited evidence and returns a
structured entailment verdict; source text remains delimited and untrusted. Both checks must pass.
Answer size and safe formatting are enforced deterministically.

One repair is allowed: the hook appends a bounded message with the violations and jumps back to the
model node, which may return a corrected `AnswerDraft` without new tool calls being accepted. If
verification still fails, no draft text is persisted or streamed and the turn ends with a typed
safe failure. The close hook appends the verified message with its citations to `history`, sets
the turn to `completed`, and the final checkpoint is the product commit. Public `answer.delta`
emission happens only after the run has returned that state.

Typed terminal failures follow the same path: the close hook records the safe code on the turn,
sets it to `failed`, and the final checkpoint persists it. If checkpointing itself fails, the turn
remains at its last checkpoint as `running`, which the service reads as interrupted; the writable
stream may end with a retryable `run.failed` that reports delivery failure only.

### 14. Serve and delete product history through the public LangGraph API

There are no application-owned conversation tables. The `history` section of the checkpointed
state is the product transcript, and the endpoints are thin application services over the public
saver/agent API:

- `GET /v1/threads/{thread_id}/messages` calls `agent.aget_state` for the thread and pages the
  `history` list in memory with keyset order `(sequence, message_id)`. Each message DTO carries its
  owning `turn_id` and that turn's status, where a `running` status without a live in-process lock
  is reported as `interrupted`.
- `GET /v1/threads` calls `checkpointer.alist(None, filter={"app": "investigation"})`, keeps the
  newest checkpoint per thread, and pages the summaries by `(created_at, thread_id)`.
- `DELETE /v1/threads/{thread_id}` takes the thread lock and calls
  `checkpointer.adelete_thread(thread_id)`.

Cursors are opaque base64 keyset positions scoped to the endpoint; tampering yields a validation
error or an empty page. Responses expose no checkpoint IDs, graph state beyond history, generated
SQL, tool payloads, or private diagnostics. The frontend talks only to the FastAPI endpoints and
receives no database credential.

Alternatives rejected:

- Three application tables (`threads`, `turns`, `messages`) with a separate commit transaction were
  designed and removed: they required a second writer role, a two-phase commit between saver and
  tables, idempotency fingerprints, and recovery reconciliation, all to keep history independent of
  the state schema. The state schema is already versioned; a thread list scan over checkpoint
  metadata is acceptable at this scale.
- Reading raw checkpoint tables with SQL would couple the API to framework-private rows; the
  public `aget_state`/`alist`/`adelete_thread` API does not.

### 15. Reuse the existing telemetry pipeline with one trace per turn attempt

Each agent invocation for a turn owns one finite `invoke_workflow` root span named
`investigation_turn`. A resumed turn starts a new root and uses an OpenTelemetry Link to the prior
trace context stored in `turn` state. Thread and turn IDs correlate attempts but are never parent
spans or metric dimensions. The expected structure for one attempt is:

```text
invoke_workflow investigation_turn
  input_guardrail
  invoke_agent main
    chat                                   # each physical model attempt
    execute_tool search_evidence | query_records | find_connections
      invoke_agent nested
        chat
        retrieval | db.query
      embeddings
    verify_grounding
  turn_close (closure, projection, commit)
  sse.emit
```

Use the shared observability provider/exporters and existing GenAI span vocabulary. Keep
agent-specific event names and LangChain hooks local until another service demonstrates the same
need. Each physical model/tool attempt has its own span; logical retries share operation IDs. The
attempt root closes when the turn completes, fails, is refused, observes cancellation, or reaches a
limit. Do not create spans/events/logs per SSE delta or state field. Capture aggregate latency,
time to first safe progress, answer-ready time, time to first public delta, tool/model counts,
retry counts, token usage when reported by the provider, result counts, failure class, and
cancellation.

Trace/log content capture is off by default independently of application PII retention.
High-cardinality thread/turn/run/trace IDs are logs/span attributes only, never metric labels.
Errors set span status and a bounded `error.type`; one owning boundary emits the correlated
structured failure log. Telemetry failure never changes the turn outcome. No Collector change is
needed for this proposal.

### 16. Keep configuration typed, layered, and secret-safe

Mirror the repository settings convention: explicit constructor values, environment, `.env`, then
`config/investigation-agent/<environment>.yaml`, followed by code defaults. YAML is restricted to
allowlisted non-secret application policy. Deployment endpoints, model IDs, expected initializer
version, and credential URLs are environment or secret-manager inputs with no unsafe fallback.

`settings.py` owns typed non-secret policy: pool bounds, deadlines, retries, call limits, retrieval
weights/top-k, graph limits, history bounds, SSE heartbeat/chunk size, model behavior, and
telemetry switches. `secrets.py` owns the two `SecretStr` runtime DSNs for the serving process and
the owner DSN plus the two role passwords for the initializer; each entry point loads only its
subset. AWS credentials remain in the SDK credential chain and are not application settings.
Validation runs before telemetry, pools, or model clients are constructed, and errors name fields
without revealing secret values. The committed YAML sets `capture_ai_content: false`.

## Risks / Trade-offs

- **[Main model sees accumulated tool messages within a turn]** → Tools return bounded,
  normalized, typed cards rather than raw rows; `ContextMiddleware` trims to a token bound with an
  explicit notice; the projection at turn close keeps cross-turn memory compact.
- **[A full-replacement projection may omit useful semantic context]** → Validate all IDs and
  bounds, retain the evidence index as deterministic reference, and surface stale/incomplete
  coverage explicitly.
- **[No authentication or authorization]** → Documented as prototype scope; every endpoint is
  reachable by any caller. The production evolution adds a FastAPI dependency and an owner filter
  in front of the routes without changing the agent.
- **[Agent-authored SQL expands attack and resource-exhaustion surface]** → Combine fail-closed AST
  allowlisting, bound parameters, `agent_read` views only, a read-only role, server-set session
  case scope, time/row/byte limits, cancellation, and adversarial integration tests. Fall back to
  a typed DSL if the gate cannot be proven. Forced RLS is the documented next step, not part of
  this scope.
- **[Single-replica serialization]** → The per-thread `asyncio.Lock` is correct only for one
  process. Compose runs one replica; the README and design doc state the limit and the lease-based
  replacement.
- **[History lives inside checkpoint state]** → Every read deserializes the whole state and thread
  listing scans checkpoint metadata. Bounded history and small state keep this cheap at prototype
  scale; a `threads` index table is the first optimization if listing becomes slow.
- **[Checkpoint write failure leaves a turn "running"]** → The service reads a running turn without
  a live lock as interrupted, the same `request_id` resumes it from the last checkpoint, and a
  different `request_id` supersedes it. Hooks and read-only tools are idempotent so a repeated step
  is safe.
- **[Post-validation answer streaming has later first answer text]** → Stream safe progress during
  work and measure answer-ready latency. The trade-off is intentional because unverified public
  tokens cannot be recalled.
- **[LLM guardrails can false-positive or become unavailable]** → Use schema-validated verdicts,
  bounded retry, a stable refusal/fail-closed policy, evaluation cases for legitimate investigative
  language, and deterministic scope/tool controls independent of the classifier.
- **[Framework middleware/stream/checkpointer behavior changes]** → Pin versions, keep a focused
  compatibility contract for hook jumps, nested custom events, limit exit behavior, and resume,
  adapt internal events at one boundary, version application state, and never expose framework
  serialization publicly.

## Migration Plan

1. Add and lock the service dependencies and run the LangChain/LangGraph compatibility contract
   before building on framework-specific hooks.
2. After the existing ingestion job has completed or skipped, run `agent-db-init` with the owner
   credential: create the two runtime roles, the `agent_read` views, the `agent_runtime` schema,
   saver tables, grants, and the recorded initializer version. Re-running is a no-op.
3. Make Compose order ingestion before `agent-db-init` and the initializer before the API; keep
   readiness false until both pools connect, the initializer version matches, and the views and
   search dependencies are present.
4. Run cross-case SQL adversarial tests, resume-after-interrupt tests, SSE contract tests,
   prompt-injection regressions, and an end-to-end cited investigation before exposing the route to
   the frontend.
5. Enable traffic gradually, monitor turn latency, projection cost, retry rates, pool pressure,
   retrieval sufficiency, and guardrail refusals. Tune bounded policy only through configuration
   and measured evaluation.

Rollback stops the investigation service and leaves `agent_runtime` and `agent_read` intact for
diagnosis or redeployment. Nothing in this change alters evidence tables or ingestion behavior.
