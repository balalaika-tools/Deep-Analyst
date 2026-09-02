## ADDED Requirements

### Requirement: Thread-only conversation identity

The public `thread_id` SHALL be the sole durable conversation identity. Creating a new thread SHALL create fresh conversation state; continuing an existing thread SHALL load only that thread's state. Checkpoints and checkpoint metadata SHALL NOT contain a case identifier or evidence partition binding.

#### Scenario: Two fresh conversations are created

- **WHEN** two distinct thread identifiers receive their first turns
- **THEN** each starts with independent history, projection, evidence index, and usage state

#### Scenario: A thread invokes an evidence tool

- **WHEN** any turn invokes a search, query, or graph tool
- **THEN** conversation identity does not restrict the globally readable evidence corpus

## MODIFIED Requirements

### Requirement: Idempotent turns keyed by request ID

Each turn SHALL record its client request ID. Repeating the latest request ID SHALL replay a completed answer, replay a stored safe failure, report an executing request, or resume an interrupted turn as appropriate. A repeated request ID with a changed message SHALL be rejected. No idempotency fingerprint or comparison SHALL include a case identifier.

#### Scenario: Completed request is retried

- **WHEN** a client repeats a completed turn's request ID with the same message
- **THEN** the service replays the committed response without new model, retrieval, query, or checkpoint work

#### Scenario: Interrupted request is retried

- **WHEN** a client repeats an interrupted turn's request ID
- **THEN** the agent resumes from its last checkpoint and does not duplicate the user message

#### Scenario: Idempotency message changes

- **WHEN** a client repeats a known request ID with a different message
- **THEN** the service returns a conflict before agent execution

#### Scenario: Idempotency key payload changes

- **WHEN** a client repeats a known request ID with a different message
- **THEN** the service returns a conflict before agent execution without comparing or requiring a case identifier

## REMOVED Requirements

### Requirement: Case-bound thread identity

**Reason**: Threads represent conversation memory only; binding them to an evidence partition contradicts global evidence access.

**Migration**: Bump the application state schema, remove case control and checkpoint metadata, and migrate compatible histories or explicitly rebuild incompatible local checkpoints with separate approval.
