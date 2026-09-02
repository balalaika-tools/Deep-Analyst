## Purpose

Expose the investigation agent through a FastAPI contract with bounded lifecycle checks,
case-bound thread isolation, idempotent invocation, thread deletion, and a stable sanitized SSE
protocol suitable for a multi-turn analyst frontend. The prototype has no authentication or
authorization; every endpoint is reachable by any caller.

## ADDED Requirements

### Requirement: Bounded service lifecycle endpoints
The service SHALL expose `GET /health` as a process-liveness check and `GET /ready` as a bounded
dependency-readiness check. Liveness MUST NOT call an external dependency. Readiness SHALL remain
unsuccessful until configuration has been validated, the agent has been built, the recorded
initializer version matches the expected version, the `agent_read` views and search dependencies
are present, and both the reader and writer pools can complete a bounded probe. Schema creation
and checkpoint setup MUST run through the initialization step and MUST NOT run in an invocation
request.

#### Scenario: Live process has an unavailable dependency
- **WHEN** the process event loop is responsive but PostgreSQL is unavailable
- **THEN** `GET /health` returns success without waiting on PostgreSQL
- **AND** `GET /ready` returns `503` within its configured probe deadline

#### Scenario: Runtime schema has not been initialized
- **WHEN** the process starts against a database without the expected initializer version
- **THEN** `GET /ready` returns `503`
- **AND** an invocation is not accepted

#### Scenario: Service shuts down
- **WHEN** the ASGI application receives its shutdown signal
- **THEN** it stops accepting new invocations, cancels active turns cooperatively within a bounded
  drain period, and closes both owned connection pools

### Requirement: Validated case-bound invocation
The service SHALL expose `POST /v1/agent/invoke` with a Pydantic-validated request containing a
client-generated `request_id`, `thread_id`, `case_id`, and one non-empty user message, subject to
configured size limits. The public `thread_id` SHALL be the checkpoint thread identity. On the
first accepted invocation for a thread, the service SHALL bind that thread to the `case_id` in
immutable control state; a later invocation with a different `case_id` SHALL be rejected with
`409 thread_case_conflict` before any agent or evidence operation. No identity, ownership, or
authorization check SHALL be performed in this prototype.

#### Scenario: First turn binds the case
- **WHEN** a client invokes a new `thread_id` with a valid request body
- **THEN** the thread is bound to that case at the first checkpoint
- **AND** the response starts the SSE protocol for one turn

#### Scenario: Thread is reused for another case
- **WHEN** a client invokes an existing thread with a different `case_id`
- **THEN** the service returns `409 thread_case_conflict` before any agent or evidence operation

#### Scenario: Request validation fails
- **WHEN** the message is empty, an identifier is malformed, or a configured input limit is
  exceeded
- **THEN** the service returns a non-streaming validation problem before starting an agent turn

### Requirement: Pre-stream conflict and replay handling
Before sending SSE headers the service SHALL resolve idempotency and serialization from the
thread's latest state and in-process lock: replay a completed or failed matching `request_id`
through the SSE contract without agent execution; return `409 request_in_progress` with
`Retry-After` for a matching request that is executing; return `409 thread_busy` with `Retry-After`
for a different request on an executing thread; return `409` for a matching `request_id` with a
changed message or case; resume an interrupted matching request; and otherwise open a new turn.

#### Scenario: Completed request is retried
- **WHEN** a client repeats the `request_id` of a completed turn with the same message and case
- **THEN** the service streams `run.started`, the committed answer deltas, and `run.completed`
- **AND** performs no model, tool, agent, or checkpoint write

#### Scenario: Failed request is retried
- **WHEN** a client repeats the `request_id` of a turn recorded as failed
- **THEN** the service streams `run.started` and the stored safe `run.failed`
- **AND** performs no model, tool, or agent operation

#### Scenario: Thread is busy
- **WHEN** a different `request_id` arrives while a turn on that thread is executing
- **THEN** the service returns `409 application/problem+json` with code `thread_busy` and
  `Retry-After`

### Requirement: Sanitized SSE envelope
An accepted invocation SHALL respond as `text/event-stream` and translate internal LangGraph
`updates` and `custom` stream events into an application-owned envelope. Every non-heartbeat event
SHALL carry `schema_version`, `event`, `thread_id`, `turn_id`, `timestamp`, and event-specific
`data`. Event types SHALL be limited to `run.started`, `progress`, `answer.delta`, `run.completed`,
and `run.failed`; heartbeats MAY be sent as SSE comments. A connected stream SHALL receive exactly
one terminal event; a disconnected stream MAY receive none and MUST never receive more than one.

The server MUST NOT expose raw LangGraph state or update objects, model or nested-agent messages,
hidden reasoning, prompts, generated SQL, tool arguments or results, raw evidence chunks,
credentials, stack traces, or provider/database error text. `progress` data SHALL use only a
documented allowlist of coarse phases derived from node names and safe counters or public tool
labels. Authoritative turn status SHALL come from state, not from delivery events.

#### Scenario: Evidence search runs
- **WHEN** the agent enters and leaves the evidence-search tool
- **THEN** the stream may emit allowlisted progress such as `searching_evidence` and its bounded
  attempt number
- **AND** emits neither retrieval queries nor retrieved chunk content

#### Scenario: Internal update shape changes
- **WHEN** a compatible LangGraph upgrade changes its raw `updates` payload or node names
- **THEN** the public SSE event names and required envelope fields remain unchanged

#### Scenario: Events are received in order
- **WHEN** a client consumes a connected invocation stream
- **THEN** it observes `run.started` first, zero or more `progress` and `answer.delta` events, and
  exactly one terminal event last

### Requirement: Final answer release follows validation and commit
Planner, guardrail, nested-agent, projection, and verifier token streams SHALL remain private. The
service SHALL buffer the candidate final answer, complete grounding and citation validation, and
checkpoint the accepted assistant message in history before emitting any `answer.delta`. It SHALL
then split only that committed text into ordered deltas and finish with `run.completed`. Failed or
rejected drafts MUST NOT be partially exposed as answer text.

#### Scenario: Candidate answer fails grounding
- **WHEN** final validation rejects a candidate answer
- **THEN** no text from that candidate is emitted in an `answer.delta`
- **AND** the agent repairs it within its limit or emits a safe `run.failed`

#### Scenario: Accepted answer is streamed
- **WHEN** final validation and the turn-close checkpoint both succeed
- **THEN** concatenating the ordered `answer.delta` payloads exactly reproduces the committed
  assistant message
- **AND** `run.completed` is the final event

#### Scenario: Commit checkpoint fails
- **WHEN** the turn-close checkpoint cannot be persisted after bounded transient retries
- **THEN** no `answer.delta` is emitted
- **AND** the stream ends with a retryable `run.failed` while the turn remains resumable

### Requirement: Safe errors across the streaming boundary
Failures detected before SSE headers SHALL use a versioned problem-details response with a stable
application error code. Failures after SSE begins, while the transport remains writable, SHALL be
represented only by a terminal `run.failed` event containing a stable code, a safe user-facing
message, and whether a retry may succeed. A `run.failed` caused by a persistence or delivery
failure SHALL be retryable and SHALL NOT by itself mean the turn is durably `failed`. Neither form
SHALL expose internal exception, SQL, prompt, provider, host, credential, or evidence content.
Detailed diagnostics SHALL be recorded only through the redacted observability boundary.

#### Scenario: Case binding conflict before streaming
- **WHEN** an existing thread is invoked with a different `case_id`
- **THEN** the service returns a non-streaming `409` problem
- **AND** no SSE headers or agent work are started

#### Scenario: Model fails after streaming starts
- **WHEN** model retries are exhausted after `run.started` was emitted
- **THEN** the turn-close hook records a safe failure code, the turn becomes `failed`, and the
  service emits one `run.failed` with that code
- **AND** does not serialize the provider exception

### Requirement: Disconnect-aware execution
Client disconnect and task cancellation SHALL propagate into the running agent. Once cancellation
is observed, the service SHALL start no new model or tool attempt, close owned in-flight resources
where supported, release the thread lock, and end active spans with cancellation status. The last
checkpoint stands; the turn reads as `interrupted` until the same `request_id` resumes it or a
different request supersedes it. A disconnect after the turn-close checkpoint MUST NOT undo the
completed turn.

#### Scenario: Client disconnects during a tool loop
- **WHEN** the client disconnects before final validation and commit
- **THEN** no new tool or model attempt starts after cancellation is observed
- **AND** the turn reads as interrupted rather than completed with partial output

#### Scenario: Client disconnects while committed output is emitted
- **WHEN** the assistant message is already checkpointed and the client disconnects during
  `answer.delta` delivery
- **THEN** the turn remains completed
- **AND** retrying the same request replays the committed response without rerunning the agent

### Requirement: Cursor-based conversation transport
The service SHALL expose `GET /v1/threads` to list threads and
`GET /v1/threads/{thread_id}/messages` to read the messages of one thread. Both endpoints SHALL
use bounded keyset pagination with an opaque cursor and deterministic ordering, and SHALL return an
explicit `next_cursor` when another page exists. Each thread summary SHALL include its bound
`case_id`, latest `turn_id`, and turn status; each message DTO SHALL include its owning `turn_id`
and that turn's current status, so `running`, `interrupted`, `completed`, and `failed` work is
visible. An unknown thread SHALL return `404`. The responses MUST NOT expose checkpoint
identifiers, agent state beyond history, generated SQL, tool payloads, or private diagnostics.

#### Scenario: Messages span multiple pages
- **WHEN** a client reads a thread whose messages exceed the requested bounded page size
- **THEN** following each `next_cursor` returns every visible message once in stable sequence order

#### Scenario: Cursor is tampered with
- **WHEN** a client alters an opaque cursor
- **THEN** the endpoint returns a validation error or an empty page

#### Scenario: Frontend observes an interrupted turn
- **WHEN** a client reads messages for a thread whose latest user message belongs to an
  interrupted turn
- **THEN** the response includes that turn ID with status `interrupted` alongside the message

### Requirement: Thread deletion
The service SHALL expose `DELETE /v1/threads/{thread_id}`. It SHALL acquire the thread's
in-process lock, delete every checkpoint and checkpoint write for that thread through the public
checkpointer API, and return `204`. An unknown thread SHALL return `404`. A thread whose turn is
executing SHALL return `409 thread_busy` with `Retry-After`. Deletion SHALL NOT touch evidence
tables or any other thread and is not recoverable.

#### Scenario: Thread is deleted
- **WHEN** a client deletes an existing idle thread
- **THEN** the service returns `204`
- **AND** subsequent reads of that thread return `404` and a new invocation with the same
  `thread_id` starts a fresh thread

#### Scenario: Deletion targets an executing thread
- **WHEN** a client deletes a thread while one of its turns is executing
- **THEN** the service returns `409 thread_busy` and deletes nothing

#### Scenario: Deletion targets an unknown thread
- **WHEN** a client deletes a `thread_id` with no checkpoints
- **THEN** the service returns `404`
