# Conversation History Specification

## Purpose

Provide durable multi-turn memory and a frontend-safe conversation read model from one
checkpointed persistence model, with thread-only conversations, idempotent turns, per-thread
serialization, explicit interruption semantics, and thread deletion.

## Requirements

### Requirement: One checkpointed persistence model
The investigation service SHALL persist all durable per-thread state, including the product
transcript, in LangGraph checkpoints written by `AsyncPostgresSaver` into the `agent_runtime`
schema of the existing application PostgreSQL deployment. The service SHALL NOT maintain separate
application-owned conversation tables. Checkpoint schema setup SHALL run once in the controlled
initialization step; request-serving credentials SHALL have no DDL privilege and request paths
MUST NOT call saver setup. Agent calls SHALL use `durability="sync"` so a checkpoint completes
before the next node begins. Checkpoint state SHALL include an explicit application state-schema
version, and a resume SHALL reject an unsupported persisted version with a typed failure rather
than silently reinterpret it.

#### Scenario: Restart preserves the thread
- **WHEN** a thread has completed turns and the service process is restarted without deleting the
  database volume
- **THEN** the thread's history, evidence index, and projection are readable and the next turn
  continues from the last checkpoint

#### Scenario: Request role encounters a missing schema
- **WHEN** checkpoint tables have not been initialized and the service runs with request-serving
  credentials
- **THEN** readiness fails without attempting to create tables

#### Scenario: Persisted application state is incompatible
- **WHEN** a request loads a checkpoint with an unsupported application state-schema version
- **THEN** the service returns a typed `incompatible_state` failure for that thread
- **AND** does not reset, migrate in place, or reinterpret the stored state

### Requirement: Thread-only conversation identity
The public `thread_id` SHALL be the sole durable conversation identity. Creating a new thread
SHALL create fresh conversation state; continuing an existing thread SHALL load only that
thread's state. Checkpoints and checkpoint metadata SHALL NOT contain an evidence partition
binding.

#### Scenario: Two fresh conversations are created
- **WHEN** two distinct thread identifiers receive their first turns
- **THEN** each starts with independent history, projection, evidence index, and usage state

#### Scenario: A thread invokes an evidence tool
- **WHEN** any turn invokes a search, query, or graph tool
- **THEN** conversation identity does not restrict the globally readable evidence corpus

### Requirement: History channel is the product transcript
Agent state SHALL contain a `history` section holding the ordered product transcript. Each entry
SHALL have a stable message ID, a monotonic thread-local sequence, its turn ID and request ID,
role, exact accepted content, validated citations where applicable, the turn's status, and a
creation timestamp. Only the intake hook MAY append user messages and only the turn-close hook MAY
append assistant messages or set a turn's terminal status. `history` MUST NOT be supplied to any
model as context. History SHALL be bounded by a configured maximum number of turns per thread; a
thread at the bound SHALL reject a new turn with a typed `thread_full` error.

#### Scenario: Draft is emitted internally
- **WHEN** the model produces a draft that has not passed grounding and citation validation
- **THEN** no history entry containing that draft is written

#### Scenario: Stored message contains PII
- **WHEN** an accepted user or final assistant message contains a phone number or account
  identifier
- **THEN** history preserves that accepted content without PII masking
- **AND** no direct database access is granted to the frontend

#### Scenario: Long thread receives another message
- **WHEN** a thread with many historical messages begins a new turn
- **THEN** the main-agent request contains the latest projection and current message rather than
  the history entries or prior turns' messages

### Requirement: Compact checkpoint state replaces transcript prompting
Durable agent state SHALL hold the versioned control state, current-turn data, bounded evidence
index, replaceable working projection, the current turn's working messages, and the history
required by the workflow. On each turn the service SHALL assemble the main-agent input from the
latest valid projection, the exact current user message, and bounded evidence cards. The
turn-close hook SHALL clear the turn's working messages after committing the answer.

#### Scenario: Working projection is temporarily stale
- **WHEN** projection replacement failed at turn close
- **THEN** the checkpoint retains the prior valid projection marked stale together with the
  committed answer and evidence index
- **AND** history remains readable

### Requirement: Idempotent turns keyed by request ID
Each turn SHALL record its client request ID. Repeating the latest request ID SHALL replay a
completed answer, replay a stored safe failure, report an executing request, or resume an
interrupted turn as appropriate. A repeated request ID with a changed message SHALL be rejected.
No idempotency fingerprint or comparison SHALL include an evidence partition identifier.

#### Scenario: Completed request is retried
- **WHEN** a client repeats a completed turn's request ID with the same message
- **THEN** the service replays the committed response without new model, retrieval, query, or
  checkpoint work

#### Scenario: Interrupted request is retried
- **WHEN** a client repeats an interrupted turn's request ID
- **THEN** the agent resumes from its last checkpoint and does not duplicate the user message

#### Scenario: Idempotency message changes
- **WHEN** a client repeats a known request ID with a different message
- **THEN** the service returns a conflict before agent execution

#### Scenario: Idempotency key payload changes
- **WHEN** a client repeats a known request ID with a different message
- **THEN** the service returns a conflict before agent execution without comparing or requiring an
  evidence partition identifier

### Requirement: Serialized execution per thread
The service SHALL hold one in-process lock per thread identity for the duration of a turn or a
deletion. A different `request_id` for a locked thread SHALL receive `409 thread_busy` with retry
guidance; the same `request_id` SHALL receive `409 request_in_progress`. Turns for different
threads MAY execute concurrently. This mechanism is defined for a single service replica; the
production evolution is a database lease.

#### Scenario: Two requests target one thread
- **WHEN** two different request IDs for the same thread arrive while the first is executing
- **THEN** exactly one runs the agent
- **AND** the other receives `409` with a stable `thread_busy` code

#### Scenario: Independent threads run concurrently
- **WHEN** a client invokes two different threads
- **THEN** thread serialization does not prevent those turns from executing concurrently

### Requirement: Interruption is derived from state, not tracked separately
A turn whose latest checkpoint records status `running` while no in-process lock is held for that
thread SHALL be treated as `interrupted` by every read and invocation path. When a different
`request_id` arrives for such a thread, the intake hook SHALL first record the abandoned turn as
`interrupted` in history and then open the new turn. The service SHALL NOT run a background
reconciler or startup sweep.

#### Scenario: Process stops mid-turn
- **WHEN** the process stops after a checkpoint but before the turn-close hook and is restarted
- **THEN** history reads report that turn as `interrupted` with its user message intact
- **AND** no assistant message exists for it

#### Scenario: New request follows an interrupted turn
- **WHEN** a different request ID arrives for a thread whose latest turn is interrupted
- **THEN** the intake hook marks the earlier turn interrupted and accepts the new turn
- **AND** the evidence index and projection from before the interruption remain available

### Requirement: Stable bounded keyset pagination
Message pages SHALL use immutable `(sequence, message_id)` order over the `history` list; thread
pages SHALL use immutable `(created_at, thread_id)` order over the newest checkpoint per thread
found through the checkpointer's metadata filter. Cursors SHALL be opaque, bounded, and
endpoint-scoped. Page sizes SHALL be bounded by configuration. Offset pagination MUST NOT be used.

#### Scenario: Message is appended between page reads
- **WHEN** a client reads one message page, a new message is appended, and the client follows the
  previous `next_cursor`
- **THEN** previously returned messages are not repeated
- **AND** every later message is reachable in sequence order

#### Scenario: Requested page size is excessive
- **WHEN** a client requests more than the configured maximum page size
- **THEN** the endpoint applies the documented bound or returns a validation error

### Requirement: Durable status and public-message semantics
Turns SHALL expose a bounded public status vocabulary of `running`, `interrupted`, `completed`, and
`failed`. Only the turn-close hook, after grounding validation, SHALL set `completed` together
with the assistant message. Only the turn-close hook SHALL set `failed`, storing a bounded safe
code and never internal error text. A failure that prevents the terminal checkpoint itself SHALL
leave the turn at its last checkpoint, which reads as `interrupted`.

#### Scenario: Partial stream terminates
- **WHEN** transport or workflow failure occurs before the turn-close checkpoint
- **THEN** the user message remains in history and the turn reads as interrupted or failed
- **AND** no partial assistant message appears in history

#### Scenario: Checkpoint write fails at turn close
- **WHEN** the turn-close hook's checkpoint cannot be persisted
- **THEN** the turn is not recorded as completed or failed
- **AND** the next request for the thread treats it as interrupted

### Requirement: Thread deletion through the checkpointer
Deleting a thread SHALL remove every checkpoint, checkpoint write, and blob for that thread through
the public `AsyncPostgresSaver` thread-deletion API while the thread lock is held. It SHALL NOT
touch evidence tables, `agent_read` views, or any other thread. After deletion the thread identity
MAY be reused for a fresh thread.

#### Scenario: Deleted thread leaves no state
- **WHEN** a thread is deleted and the same `thread_id` is invoked again
- **THEN** the new invocation finds no checkpoint and binds the thread as new

#### Scenario: Deletion does not affect other threads or evidence
- **WHEN** one thread is deleted
- **THEN** other threads' checkpoints and all evidence rows remain unchanged
