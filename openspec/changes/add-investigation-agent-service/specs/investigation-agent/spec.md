## Purpose

Define a bounded, checkpointed, and observable investigation agent built on one LangChain
`create_agent` that coordinates evidence sources across turns while exposing only grounded,
citation-verified answers to an analyst.

## ADDED Requirements

### Requirement: Single-agent investigation strategy
The investigation workflow SHALL be one LangChain `create_agent` configured with exactly the
`search_evidence`, `query_records`, and `find_connections` tools, an `AsyncPostgresSaver`
checkpointer, a custom state schema, and a structured `AnswerDraft` response format. That agent
SHALL be the sole owner of cross-source planning, tool selection, synthesis, and the decision to
finalize a turn. Its built-in tool node SHALL execute the tools; the model MAY request one or more
tool calls per step. Case, thread, request, deadline, and cancellation SHALL reach tools only
through trusted runtime context and MUST NOT be model-authored.

`search_evidence` and `query_records` SHALL be nested, checkpointer-free `create_agent` instances
invoked inside their tool. Each nested agent SHALL receive a typed, bounded intent and MAY run its
own bounded correction loop, but SHALL NOT retain memory across invocations, call another domain
tool, choose work for another evidence source, or author the user-facing answer.
`find_connections` SHALL remain a bounded deterministic tool.

#### Scenario: Query sub-agent repairs a semantic failure
- **WHEN** the main agent requests a structured-record query and the first policy-compliant query
  returns a repairable schema or semantic failure
- **THEN** the nested query agent uses its typed intent and local attempt history to create a
  distinct corrected query within its limits
- **AND** the main agent is not required to reconstruct that tactical correction context

#### Scenario: Main agent requests several tools in one step
- **WHEN** one main-agent response contains more than one domain-tool call
- **THEN** the built-in tool node executes each call under the configured tool-call limit
- **AND** every result crosses the same evidence boundary before the next model call

#### Scenario: Cross-source follow-up remains a main-agent decision
- **WHEN** a nested agent returns an incomplete result that could be investigated through a
  different evidence source
- **THEN** the nested agent returns a typed incomplete outcome to the main agent
- **AND** only the main agent decides whether to invoke the different source

#### Scenario: Nested invocations do not acquire hidden memory
- **WHEN** the same nested agent is invoked in two different turns
- **THEN** the second invocation can access only its new typed input and its invocation-local
  message history
- **AND** no checkpoint or message history from the first invocation is loaded

### Requirement: Compact checkpointed agent state
The agent state SHALL extend the framework agent state with system-owned immutable control state,
current-turn state, a bounded evidence index, one replaceable `WorkingProjection`, and the product
`history` transcript. Control state SHALL bind the case scope, state schema version, and policy
configuration and MUST NOT be changed by a model. `history` SHALL be written only by the intake
and turn-close hooks and MUST NOT be model context. The framework `messages` list SHALL contain
only the current turn's working conversation and SHALL be cleared by the turn-close hook after the
answer is committed.

The evidence index SHALL be written only by the tool-call hook from validated tool outcomes,
keyed by stable evidence identifier, carrying trusted case, `SourceRef`, content hash, status, and
bounded display fields. It SHALL NOT promote status or overwrite provenance, and it SHALL be
bounded by configuration with an explicit coverage notice when cards are dropped. Raw model
messages and raw tool payloads MUST NOT become the authoritative evidence state.

Agent execution and streaming SHALL set LangGraph `durability="sync"`, so every model, tool, and
hook node is checkpointed before the next node begins. A resumed turn SHALL continue from its last
checkpoint with `None` input and SHALL NOT repeat a completed tool solely to reconstruct model
context.

#### Scenario: Crash occurs between tool execution and the next model call
- **WHEN** a tool result has been checkpointed but execution stops before the next model call
- **THEN** resuming the turn continues from that checkpoint
- **AND** the completed tool is not invoked again solely because the model call was interrupted

#### Scenario: Checkpoint write fails
- **WHEN** the saver cannot persist a node's checkpoint under synchronous durability
- **THEN** the next node does not begin and the turn remains at its last checkpoint
- **AND** no assistant message is committed from that state

#### Scenario: Model output cannot change trusted scope
- **WHEN** any model output proposes a different case, thread, schema version, or policy limit
- **THEN** the value is ignored and the system-owned control state is preserved

#### Scenario: Evidence index reaches its bound
- **WHEN** more evidence cards are added than the configured index bound permits
- **THEN** the hook drops the oldest cards not referenced by the projection or the current turn
- **AND** records an explicit coverage notice

### Requirement: Validated working projection refreshed at turn close
The `WorkingProjection` SHALL be a bounded LLM-authored semantic projection, replaced as a whole at
the end of each turn rather than accumulated with message reducers. Its typed schema SHALL include
the current user goal, compact dialogue summary, referent bindings with confidence status, focused
evidence and entity identifiers, active findings, explicitly qualified hypotheses, open questions,
suggested next steps, its source turn, and a stale flag. Every identifier and factual status in a
candidate projection SHALL be validated against the evidence index and control state. The
projection model SHALL have no tools and MUST NOT create evidence identifiers, promote an
unverified claim, convert a retrieval miss into factual absence, or change trusted scope.

The turn-close hook SHALL attempt the projection replacement only while the reserved closure-model
budget remains and the model provider is available. One invalid structured result MAY receive one
bounded repair. If no valid replacement is produced, the hook SHALL preserve the last validated
projection and mark it stale. If cancellation has been observed, no further model call SHALL
start. Projection failure MUST NOT discard validated evidence or the committed answer.

#### Scenario: Turn close replaces the projection
- **WHEN** a turn commits an answer while closure budget remains
- **THEN** the hook produces and validates a replacement projection from the prior projection, the
  exact utterance, the evidence added this turn, and the answer
- **AND** the next turn's first model call receives that projection rather than prior messages

#### Scenario: Projection invents an evidence reference
- **WHEN** a candidate projection names an evidence, entity, or finding identifier absent from the
  evidence index
- **THEN** deterministic validation rejects the candidate before it becomes state

#### Scenario: Projection remains usable after compactor failure
- **WHEN** projection retries and the permitted repair do not produce a valid replacement
- **THEN** the last validated projection remains active and is marked stale
- **AND** the committed answer and evidence index are unchanged

### Requirement: Bounded per-turn model context
Before every main-agent model call, the model-call hook SHALL build the system prompt from trusted
instructions, control state, the latest valid `WorkingProjection`, and bounded evidence-index
cards, and SHALL pass only the current turn's messages. It MUST NOT pass `history`, prior turns'
messages, nested-agent message histories, raw database rows, or raw retrieval payloads. Context
assembly SHALL enforce a configurable token bound using deterministic trimming with stable
ordering; trimming SHALL preserve the user message and record an explicit notice rather than
silently changing evidentiary meaning.

#### Scenario: A later turn resolves a prior referent
- **WHEN** an analyst asks a follow-up using a referent captured in the validated projection
- **THEN** the model input contains the exact follow-up utterance and the typed referent binding
- **AND** it does not contain the prior turn's messages

#### Scenario: Turn messages exceed the context bound
- **WHEN** the current turn's tool messages exceed the configured token bound
- **THEN** the hook trims the oldest tool messages deterministically and includes a truncation notice
- **AND** the evidence index still contains every card those messages produced

### Requirement: LLM guardrails at untrusted-input boundaries
A `before_agent` guardrail hook SHALL classify the current user utterance for prompt injection and
investigation-scope relevance before any evidence tool can run. A blocked or indeterminate verdict
after bounded transient retries SHALL produce a typed safe refusal or guardrail-unavailable answer
and end the run without evidence access. Every model-visible textual value returned by any
tool—including retrieved chunks, structured-row strings, and graph labels or properties—SHALL be
normalized, labeled, and delimited as untrusted evidence before it reaches the main model, a nested
agent, the projection model, or the answer verifier. Guardrail models SHALL return
schema-validated verdicts and SHALL NOT have tools. These guardrails SHALL retain PII in
application state and evidence; PII retention MUST NOT disable scope, injection, or
telemetry-content protections.

#### Scenario: User attempts to override system policy
- **WHEN** the user utterance asks the agent to ignore its rules, reveal hidden instructions, or
  escape the investigation scope
- **THEN** the guardrail hook ends the run before any model planning or tool execution
- **AND** the turn returns a safe typed refusal without echoing hidden or malicious content

#### Scenario: User asks an unrelated question
- **WHEN** the guardrail determines that a request is outside the configured investigation domain
- **THEN** the agent performs no evidence read and returns a concise scope refusal

#### Scenario: Retrieved source contains embedded instructions
- **WHEN** retrieved evidence includes text instructing a model to change policy, call a tool, or
  treat unsupported text as fact
- **THEN** every downstream model receives that text only through the untrusted-evidence boundary
- **AND** the embedded instruction cannot alter control state, scope, or become a verified finding

#### Scenario: Structured row or graph label contains embedded instructions
- **WHEN** `query_records` returns an instruction-like string column or `find_connections` returns
  an instruction-like label or property
- **THEN** the value crosses the same untrusted-evidence boundary as a retrieved chunk before any
  model sees it

#### Scenario: Evidence contains PII
- **WHEN** case-scoped evidence contains personally identifiable information relevant to the
  investigation
- **THEN** the guardrail does not redact it from application state or grounded output
- **AND** telemetry content capture remains governed independently and disabled by default

### Requirement: Layered retry ownership and hard turn limits
Model invocations SHALL use `ModelRetryMiddleware` or an equivalent bounded policy only for
explicitly classified transient provider failures, with configured backoff and jitter. Nested-agent
tools and the deterministic graph read SHALL use `ToolRetryMiddleware` or the same bounded policy
only for transient database, embedding, or transport failures. Validation, policy, schema,
empty-result, relevance, and other semantic outcomes MUST NOT be retried blindly by infrastructure
middleware; a nested agent MAY revise them only within its own tool-call limit.

The main agent SHALL enforce `ModelCallLimitMiddleware` and `ToolCallLimitMiddleware` per turn,
an elapsed-time limit, a context token bound, and a final-answer size bound. Each nested agent
SHALL enforce its own model-call and tool-call limits. When a limit ends the run without an
accepted answer, the turn-close hook MAY make at most one no-tool closure call from indexed
evidence while the closure reserve remains; otherwise it SHALL record a typed failure. Limit
exhaustion SHALL be an explicit state transition rather than a prompt asking a model to stop.

#### Scenario: Database connection fails transiently
- **WHEN** a nested tool attempt receives a classified transient connection error within its retry
  and turn limits
- **THEN** tool retry middleware repeats the same physical operation with bounded backoff
- **AND** the nested agent does not spend a tool-call on that retry

#### Scenario: Query violates deterministic policy
- **WHEN** the query executor rejects generated SQL for a policy reason
- **THEN** generic retry middleware does not execute the same rejected SQL again
- **AND** only the nested query agent may submit a distinct corrected candidate within its
  tool-call limit

#### Scenario: Turn limit is exhausted
- **WHEN** the main agent reaches its model-call or tool-call limit without an accepted answer
- **THEN** no new model or evidence operation governed by that limit is started
- **AND** the turn ends with a grounded closure answer or a typed failure naming the exhausted limit

### Requirement: Grounded finalization and two-phase public release
The main agent SHALL produce its final answer as a private structured `AnswerDraft`. An
`after_model` grounding hook SHALL validate it before any answer text is released as public
events. Deterministic verification SHALL require every material factual claim to map to evidence
identifiers that resolve in the evidence index for the bound case, SHALL reject invented or stale
references, SHALL preserve source locators, and SHALL distinguish verified findings from proposed
relationships, hypotheses, and unresolved questions. A retrieval miss, incomplete coverage, empty
query, or exhausted tool MUST NOT be expressed as proof that a fact does not exist. A bounded
no-tool LLM grounding verifier SHALL return a structured verdict for each material claim against
only its cited, untrusted evidence; both layers SHALL pass before release. One bounded
answer-repair attempt MAY correct a failed check by returning to the model without acquiring new
evidence.

Only a final answer that passes verification and has been appended to `history` by the turn-close
hook and checkpointed SHALL be released incrementally as final-answer deltas. Progress events MAY
be emitted while private work proceeds, but planner, guardrail, nested-agent, projection, and
unvalidated answer tokens MUST NOT be exposed. If verification, repair, or the durable commit
fails, the workflow SHALL emit no candidate-answer deltas and SHALL end with a typed safe failure
or no-support result.

#### Scenario: Candidate answer has complete support
- **WHEN** every material factual claim resolves to valid case-scoped evidence and the turn-close
  checkpoint has persisted the answer
- **THEN** the service may release that exact validated answer through incremental final-answer
  deltas followed by completion

#### Scenario: Candidate answer invents a citation
- **WHEN** a candidate answer cites an evidence identifier absent from the evidence index
- **THEN** the grounding hook withholds the draft and returns the violations to the model once
- **AND** an unrepaired candidate ends in a typed safe failure with no answer text exposed

#### Scenario: Search finds no relevant support
- **WHEN** bounded evidence search returns an explicit no-retrieved-support result
- **THEN** the final response describes the search limitation without asserting factual absence

#### Scenario: Proposed graph relationship is included
- **WHEN** a candidate answer relies on a relationship whose indexed status is `proposed`
- **THEN** the answer labels the relationship as proposed or hypothetical and cites its supporting
  source references

### Requirement: One finite observable trace per execution attempt
Each agent invocation for a turn SHALL create one finite `invoke_workflow investigation_turn` root
trace that ends when that attempt completes, fails, is refused, observes cancellation, or reaches a
limit. A resumed turn SHALL create a new root linked to the preceding attempt's trace context
when it was persisted in turn state and correlated by the same thread and turn identifiers; it
SHALL NOT continue or become a child of a root owned by a crashed or disconnected process. The
agent SHALL NOT keep a trace open for the lifetime of a conversation.

Each attempt trace SHALL contain the guardrail, main-agent model attempts, tool executions,
nested-agent invocations, retrievals, physical model attempts, grounding verification, turn close,
and final streaming boundaries that actually occurred in that attempt. Every physical retry SHALL
have its own model or tool span, while SSE tokens and state fields MUST NOT create one span or log
per item. Thread, turn, run, and trace identifiers SHALL support correlation, but high-cardinality
identifiers MUST NOT be metric attributes.

Prompt text, model output, evidence content, database rows, tool arguments, and tool results SHALL
be excluded from traces and logs by default even though PII is retained in application data.
Errors SHALL use bounded classifications with one owning correlated failure log, and telemetry
export failure SHALL NOT change the investigation outcome. Observed cancellation and client
disconnect SHALL close active spans and the attempt root with the correct non-success status; no
contract SHALL require a process that terminated abruptly to export a closing span.

#### Scenario: Two turns share a thread
- **WHEN** an analyst completes two turns in the same conversation thread
- **THEN** observability contains one finite root trace for each agent invocation, correlated by
  the conversation and turn identifiers
- **AND** neither root is the parent of the other or remains open between turns

#### Scenario: A turn resumes after an interruption
- **WHEN** a repeated `request_id` resumes an interrupted turn from its last checkpoint
- **THEN** it starts a new root trace carrying the same turn correlation and an OpenTelemetry link
  to the prior trace context when that context was persisted

#### Scenario: Model retry is inspected
- **WHEN** a transient model failure succeeds on its next permitted attempt
- **THEN** the owning operation contains one failed and one successful physical model span under
  the same finite execution-attempt trace

#### Scenario: Final answer is streamed in many deltas
- **WHEN** a committed final answer is emitted through multiple public deltas
- **THEN** the trace records the streaming phase and aggregate first-token and completion timing
- **AND** it contains no span or structured log per delta

#### Scenario: Telemetry content capture is not enabled
- **WHEN** an investigation turn processes PII-bearing evidence under the default configuration
- **THEN** no trace or log contains the user text, evidence content, model content, raw tool data,
  or database rows
