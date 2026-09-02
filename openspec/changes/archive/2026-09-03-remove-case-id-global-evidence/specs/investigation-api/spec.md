## ADDED Requirements

### Requirement: Validated thread invocation

`POST /v1/agent/invoke` SHALL accept a client-generated `request_id`, a client-generated `thread_id`, and one bounded non-empty message. The request and execution context SHALL NOT accept or derive a case identifier. A new thread SHALL begin a fresh conversation with access to the global evidence store; an existing thread SHALL continue only that thread's durable conversation state.

#### Scenario: First request creates a conversation

- **WHEN** a valid request uses an unknown `thread_id`
- **THEN** the service opens a fresh thread and starts the turn without requiring evidence-scope selection

#### Scenario: Existing thread continues

- **WHEN** a valid request uses an existing idle `thread_id`
- **THEN** the service continues that thread's history and working projection while retaining global evidence access

#### Scenario: Request supplies removed scope

- **WHEN** a request contains the removed `case_id` field
- **THEN** strict request validation rejects the obsolete contract with a clear migration-safe validation response

## MODIFIED Requirements

### Requirement: Pre-stream conflict and replay handling

Before SSE begins, the service SHALL resolve idempotency and serialization from `thread_id`, `request_id`, the exact message, checkpoint state, and the in-process thread lock. Repeating a completed or failed request SHALL replay its stored public result without agent execution; an in-progress or busy thread SHALL return the documented conflict. A repeated request ID with changed message content SHALL be rejected.

#### Scenario: Completed request is retried

- **WHEN** a client repeats a completed turn's `request_id` with the same thread and message
- **THEN** the service replays the committed response without model, tool, or checkpoint work

#### Scenario: Failed request is retried

- **WHEN** a client repeats the request ID of a durably failed turn
- **THEN** the service replays the stored safe failure without agent execution

#### Scenario: Idempotency payload changes

- **WHEN** a client repeats a known `request_id` with changed message content
- **THEN** the service returns a conflict before agent execution

#### Scenario: Thread is busy

- **WHEN** a different request ID arrives while a turn on that thread is executing
- **THEN** the service returns `409 thread_busy` with retry guidance

### Requirement: Safe errors across the streaming boundary

Failures before SSE headers SHALL use versioned problem details with stable application codes. Failures after streaming begins SHALL use one terminal safe failure event when transport remains writable. Errors SHALL NOT expose internal exceptions, SQL, prompts, credentials, evidence content, or database details.

#### Scenario: Request fails validation before streaming

- **WHEN** a request is malformed or uses the obsolete invocation contract
- **THEN** the service returns a non-streaming validation response and starts no agent work

#### Scenario: Case binding conflict before streaming

- **WHEN** a client submits the obsolete case-bound request shape
- **THEN** strict validation returns a non-streaming error and starts no agent or evidence work

#### Scenario: Model fails after streaming starts

- **WHEN** model retries are exhausted after the run begins
- **THEN** the service emits one sanitized terminal failure and no provider exception

### Requirement: Cursor-based conversation transport

The service SHALL expose bounded keyset-paginated thread and message endpoints with stable ordering and opaque cursors. Thread summaries SHALL contain `thread_id`, latest turn identity, status, and creation metadata without a case identifier. Unknown threads SHALL return `404`, and responses SHALL not expose private checkpoint or tool state.

#### Scenario: Threads are listed

- **WHEN** a client requests the conversation list
- **THEN** each summary identifies an independent thread and contains no evidence-scope field

#### Scenario: Messages span multiple pages

- **WHEN** a thread has more messages than the requested bounded page size
- **THEN** following cursors returns every visible message once in stable sequence order

#### Scenario: Cursor is tampered with

- **WHEN** a client alters an opaque cursor
- **THEN** the endpoint returns a validation error or an empty page

#### Scenario: Frontend observes an interrupted turn

- **WHEN** a client reads a thread whose latest turn is interrupted
- **THEN** the response includes that turn identity and interrupted status without a case field

## REMOVED Requirements

### Requirement: Validated case-bound invocation

**Reason**: Evidence access is global and a conversation must not be bound to a caller-selected data partition.

**Migration**: Remove `case_id` from invocation requests, request fingerprints, conflict checks, runtime metadata, SSE-adjacent contracts, and clients. Continue to use `thread_id` for conversation identity.
