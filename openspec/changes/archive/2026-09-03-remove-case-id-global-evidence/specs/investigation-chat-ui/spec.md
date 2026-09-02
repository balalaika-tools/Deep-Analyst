## Purpose

Provide an accessible conversation interface where every fresh thread can investigate the complete evidence store without exposing or requesting internal dataset scope identifiers.

## ADDED Requirements

### Requirement: Conversation home without case selection

The application SHALL present a conversation home with a primary `New conversation` action and recent conversations. It SHALL NOT display, request, route by, or transmit a case identifier.

#### Scenario: User opens the application

- **WHEN** the conversation home loads
- **THEN** the user can start a new conversation or open a recent thread without entering an identifier

### Requirement: Fresh conversation workspace

Starting a new conversation SHALL generate a new thread identity and open an empty transcript. Existing conversations SHALL use a thread-only route such as `/threads/<thread_id>`. Routes under `/cases/...` SHALL not be part of the supported application surface.

#### Scenario: User starts a new conversation

- **WHEN** the user activates `New conversation`
- **THEN** the workspace contains no prior messages and the first submission uses a newly generated thread ID

#### Scenario: User opens recent history

- **WHEN** the user selects a recent conversation
- **THEN** the application opens that thread's route and renders only its durable transcript

### Requirement: Thread-only turn submission

Each submitted turn SHALL send only the request identifier, thread identifier, and message required by the investigation API. The UI SHALL preserve existing ordered SSE progress, answer, completion, cancellation, and failure behavior without attaching an evidence-scope value.

#### Scenario: User submits a question

- **WHEN** the composer submits a valid message
- **THEN** the client sends the thread-only invocation contract and renders the ordered streamed response

#### Scenario: Obsolete deep link is opened

- **WHEN** a user opens a legacy `/cases/...` URL
- **THEN** the application redirects to the conversation home without treating the path value as evidence scope

### Requirement: Conversation management

Recent-conversation pagination, interruption status, retry behavior, and confirmed deletion SHALL operate by thread identity alone. Deleting one thread SHALL not affect evidence or any other conversation.

#### Scenario: User deletes a conversation

- **WHEN** the user confirms deletion of an idle thread
- **THEN** that thread disappears from recent conversations while all evidence and other threads remain available

