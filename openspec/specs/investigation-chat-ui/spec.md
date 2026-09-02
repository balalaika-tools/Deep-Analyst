# investigation-chat-ui Specification

## Purpose

Provide an accessible conversation interface where every fresh thread can investigate the complete
evidence store without exposing or requesting internal dataset scope identifiers.

## Requirements

### Requirement: Conversation home without scope selection
The application SHALL present a conversation home with a primary `New conversation` action and
recent conversations. It SHALL NOT display, request, route by, or transmit an evidence partition
identifier.

#### Scenario: User opens the application
- **WHEN** the conversation home loads
- **THEN** the user can start a new conversation or open a recent thread without entering an
  identifier

### Requirement: Focused and responsive chat layout
The workspace SHALL present thread navigation and the active conversation as its two primary
regions on wide viewports. On narrow viewports, thread navigation SHALL remain available without
permanently reducing the chat width. All primary actions, status updates, confirmation controls,
and message content MUST be usable with keyboard navigation and assistive technology.

#### Scenario: Desktop workspace is opened
- **WHEN** the viewport has room for both primary regions
- **THEN** the thread list remains visible beside the active conversation
- **AND** the composer remains associated with the active thread

#### Scenario: Mobile workspace is opened
- **WHEN** the viewport cannot accommodate both regions
- **THEN** the conversation uses the available width
- **AND** the analyst can open and close thread navigation with a labelled keyboard-accessible
  control

### Requirement: Paginated thread navigation
The application SHALL list thread summaries in the server-provided order and display enough of
each summary to distinguish its identity, creation time, and current status. It SHALL support the
opaque `next_cursor` without interpreting or modifying it, expose a clear empty state, and avoid
requesting every thread's messages merely to render the sidebar.

#### Scenario: More threads are available
- **WHEN** a thread page includes `next_cursor`
- **THEN** the sidebar offers a load-more action that requests the next page with that cursor
- **AND** newly returned summaries are appended without duplicating existing thread IDs

#### Scenario: No threads exist
- **WHEN** the first thread page is empty
- **THEN** the sidebar presents an empty state and a new-conversation action

### Requirement: Fresh conversation workspace
Starting a new conversation SHALL generate a new thread identity and open an empty transcript.
Existing conversations SHALL use a thread-only route. Legacy scope-bearing routes SHALL not be part
of the supported application surface.

#### Scenario: New conversation is selected
- **WHEN** the analyst activates the new-conversation action
- **THEN** an empty composer-ready conversation is shown
- **AND** no backend turn or empty persisted thread is created before message submission

#### Scenario: Existing conversation is selected
- **WHEN** the analyst activates a thread summary
- **THEN** that thread becomes the active route and its persisted messages are shown

#### Scenario: Obsolete deep link is opened
- **WHEN** a user opens a legacy scope-bearing URL
- **THEN** the application redirects to the conversation home without treating the path value as
  evidence scope

### Requirement: Confirmed thread deletion
The application SHALL provide a delete action for persisted threads and SHALL require explicit
confirmation before invoking `DELETE /v1/threads/{thread_id}`. It MUST remove a thread from the
visible list only after a successful response. Deletion of the currently streaming thread MUST be
unavailable until the turn reaches a terminal state or is cancelled.

#### Scenario: Analyst confirms deletion
- **WHEN** the analyst confirms deletion and the backend returns success
- **THEN** the thread is removed from the sidebar
- **AND** if it was active, the application navigates to a fresh conversation

#### Scenario: Thread deletion fails
- **WHEN** the delete request returns a problem response or network failure
- **THEN** the thread remains visible
- **AND** the application shows an actionable error without exposing internal response content

#### Scenario: Analyst cancels deletion
- **WHEN** the analyst dismisses the confirmation
- **THEN** no delete request is sent and the conversation remains unchanged

### Requirement: Durable paginated transcript
The application SHALL render user and assistant messages in ascending sequence order using the
history DTO as authoritative persisted state. It SHALL follow opaque message cursors without
interpreting them, avoid duplicate message IDs across pages, distinguish interrupted or failed
turns without inventing assistant content, and present assistant citation metadata when supplied.

#### Scenario: Older messages span multiple pages
- **WHEN** the active thread's history response includes `next_cursor`
- **THEN** the analyst can load the next page and every message is displayed once in sequence order

#### Scenario: History contains an interrupted turn
- **WHEN** a user message has `turn_status` equal to `interrupted` and no committed assistant
  message
- **THEN** the transcript identifies the interruption and does not fabricate an answer

#### Scenario: Assistant message has citations
- **WHEN** a history or completion payload contains citations
- **THEN** the message exposes the evidence and source-reference metadata in an accessible form

### Requirement: Thread-only turn submission
For each submitted message, the application SHALL send only `request_id`, `thread_id`, and
the non-empty message to `POST /v1/agent/invoke`. The composer MUST prevent duplicate concurrent
submissions for the active thread. A retry of the same interrupted or retryable attempt MUST reuse
the original request ID and exact payload; a deliberate new turn MUST generate a new request ID.

#### Scenario: Analyst submits a message
- **WHEN** the active thread has no running request and the analyst submits non-blank content
- **THEN** one invocation begins with newly generated request and thread identifiers where needed
- **AND** the composer prevents another submission on that thread until the attempt ends

#### Scenario: Analyst retries a retryable attempt
- **WHEN** an attempt ends without a durable terminal result and the analyst activates retry
- **THEN** the application resubmits the exact payload with the same `request_id`

### Requirement: Ordered POST SSE consumption
The application SHALL consume an accepted invocation as an incremental `text/event-stream`
response to the POST request. It SHALL ignore heartbeat comments, validate `schema_version`, event
name, thread and turn identity, and event-specific data, and process only `run.started`, `progress`,
`answer.delta`, `run.completed`, and `run.failed`. Answer deltas MUST be assembled by contiguous,
zero-based `index` and rendered incrementally. A connected stream MUST be considered successful
only after exactly one `run.completed` terminal event.

#### Scenario: Successful streamed answer
- **WHEN** the client receives a valid start event, progress events, contiguous answer deltas, and
  one completion event
- **THEN** it displays safe progress while the turn runs
- **AND** the concatenated delta text becomes the assistant message with completion citations

#### Scenario: Heartbeat crosses arbitrary network chunks
- **WHEN** comment heartbeats, UTF-8 text, or SSE fields are split across transport chunks
- **THEN** the parser preserves event boundaries and text without displaying the heartbeat

#### Scenario: Stream violates the contract
- **WHEN** an envelope has an unsupported schema version, mismatched thread or turn identity,
  duplicate terminal event, unknown event, or non-contiguous delta index
- **THEN** the application stops consuming that stream
- **AND** reports that the conversation must be reconciled from persisted history

### Requirement: Visible progress, cancellation, and failure recovery
The application SHALL map every documented progress phase to concise user-facing status text and
MUST NOT expose fields outside the public event data. The analyst SHALL be able to cancel the
active request. Non-streaming problem responses and terminal `run.failed` events SHALL display safe
messages and offer retry only when the response says retry is allowed. If a stream ends without a
terminal event, the application SHALL treat the result as uncertain, reload persisted history,
and MUST NOT automatically start a new request.

#### Scenario: Backend returns a problem before streaming
- **WHEN** invocation returns `application/problem+json` instead of an SSE response
- **THEN** the application displays the problem's safe detail and retryability
- **AND** does not attempt to parse the body as SSE

#### Scenario: Stream reports retryable failure
- **WHEN** the terminal event is `run.failed` with `retryable` equal to true
- **THEN** the failure is shown beside the attempted turn and a same-request retry is available

#### Scenario: Analyst cancels a running turn
- **WHEN** the analyst activates cancel while a stream is open
- **THEN** the request is aborted, the composer becomes usable after cleanup, and persisted history
  is reloaded to determine authoritative status

#### Scenario: Connection ends without a terminal event
- **WHEN** the stream closes after starting but before `run.completed` or `run.failed`
- **THEN** the application marks the delivery outcome as uncertain and reloads the transcript
- **AND** it does not invent a terminal status or automatically create another turn
