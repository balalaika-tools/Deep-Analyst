## MODIFIED Requirements

### Requirement: Constrained LLM extraction
For each text chunk the run SHALL request entity candidates only for `PERSON`,
`ORGANIZATION`, and `LOCATION` (with aliases) and relationship candidates only for `USES`,
`ASSOCIATED_WITH`, `DIRECTOR_OF`, and `KIN_OF`. Every candidate SHALL carry exact text and
character offsets, and every relationship SHALL carry typed endpoints and an exact supporting
quote from the same chunk. Before persisting, the run SHALL verify quoted text at the claimed
offsets, resolve endpoints to entities known for that chunk, check allowed endpoint types, and
discard any candidate that fails. Accepted relationships SHALL be stored with status
`proposed` and method `llm`. The run SHALL request schema-constrained output through the
configured model's supported structured-output mechanism and MUST NOT require tool selection
from a model that supports native structured output but not `tool_choice`. Transient model
failures SHALL be retried a bounded number of times; a model failure after retries SHALL fail
the run.

#### Scenario: Quote must exist at the claimed offsets
- **WHEN** a relationship candidate's supporting quote does not equal the chunk text at its
  offsets
- **THEN** the candidate is discarded and counted as rejected

#### Scenario: Endpoint types must be allowed
- **WHEN** a candidate proposes `DIRECTOR_OF` with a `PHONE` object
- **THEN** the candidate is discarded and counted as rejected

#### Scenario: Identifier endpoints resolve to rule entities
- **WHEN** `R-01` yields a `USES` candidate whose object text is `+30 697 123 4567`
- **THEN** the stored relationship's object is the rule-created `PHONE` entity, and no
  model-created phone entity exists

#### Scenario: Semantic edges are proposed
- **WHEN** `R-01` is processed
- **THEN** the store contains a `proposed` `USES` relationship from a `PERSON` entity labeled
  `Alexandros Mavridis` to the `PHONE` entity, with a text-span source reference whose quote is
  a substring of `R-01`

#### Scenario: Embedded instructions remain data
- **WHEN** document `A-D1` is processed
- **THEN** the run still extracts from every other document and no candidate references
  instructions from `A-D1`

#### Scenario: Co-occurrence is not a relationship
- **WHEN** a relationship candidate has no supporting quote
- **THEN** the candidate is discarded

#### Scenario: Native structured output does not require tool selection
- **WHEN** the configured Bedrock model advertises native structured output and rejects
  `tool_choice`
- **THEN** entity and relationship extraction use native schema-constrained responses without
  sending a tool-selection request

### Requirement: Run telemetry
Each run SHALL emit exactly one bounded root trace containing the ingestion lifecycle, source
loading, indexing, persistence, extraction workflows, and every physical model operation. Tempo
SHALL receive the complete span tree. Langfuse SHALL receive the same trace identity as an
ancestor-closed GenAI projection: the root, GenAI workflow, agent, model, embedding, retrieval,
and tool spans, plus only the minimal logical ancestors required to preserve their parent chain.
Every retained Langfuse span SHALL keep the same trace ID, span ID, and parent relationship it has
in Tempo, and no retained span SHALL reference a parent that is absent from Langfuse. Source-load,
database, HTTP-client, persistence, and other operational-only spans SHALL remain available in
Tempo and SHALL be absent from Langfuse unless one is a required ancestor of retained GenAI work.

Within that trace, each prose chunk SHALL have one `invoke_workflow extract_chunk` subtree
containing its entity and relationship agent invocations and physical chat attempts. Each source
record SHALL have one `invoke_workflow indexing_embeddings` subtree containing one physical
embedding-model span for each of the record's chunks. The root and retained GenAI projection
spans SHALL carry `app.workflow.run.id`; record and chunk metadata SHALL appear only on the
workflow and model scopes to which they apply. The same run identifier SHALL be emitted as
`workflow_run_id` on the small set of structured logs used for business-run and retry search.
Every log SHALL retain its actual current trace and span identifiers. Record, chunk, run, and
trace identifiers MUST NOT be metric attributes.

The run SHALL record standard GenAI operation-duration and token-usage metrics plus counters for
extraction candidates by kind and outcome and for indexed chunks by source. Prompt, completion,
embedding input, tool arguments/results, and document content SHALL be collected only when
content capture is explicitly enabled. When collected, approved GenAI content SHALL be retained
on the Langfuse projection but MUST be removed from the Tempo path and MUST NOT appear in logs.
Shared secret-redaction policy SHALL apply before either trace destination. Telemetry export
failure SHALL NOT fail the run. The single owning failure log SHALL remain ingestible when an
exception group contains a large traceback; detailed local failure text MUST NOT be encoded as
unbounded OTLP structured metadata that causes Loki to reject the record.

#### Scenario: Tempo and Langfuse share one trace identity
- **WHEN** one ingestion run containing GenAI work is exported successfully
- **THEN** Tempo contains its complete `run ingestion` trace
- **AND** Langfuse contains a projection with the same root trace ID
- **AND** every observation retained in both backends has the same trace and span identity

#### Scenario: Inspect one chunk extraction workflow
- **WHEN** entity and relationship extraction complete for one prose chunk
- **THEN** the Langfuse ingestion trace contains one `invoke_workflow extract_chunk` subtree with
  entity and relationship `invoke_agent` children and their physical `chat` children
- **AND** the trace is searchable by workflow run, record, and chunk identifiers

#### Scenario: Inspect one record's embedding workflow
- **WHEN** a source record produces multiple chunks that are embedded
- **THEN** the Langfuse ingestion trace contains one `invoke_workflow indexing_embeddings`
  subtree for that record
- **AND** the subtree contains one `embeddings` child span per chunk with its chunk index and
  character offsets

#### Scenario: Langfuse projection stays connected and focused
- **WHEN** a GenAI span is nested below a logical business ancestor and has operational-only
  sibling spans
- **THEN** Langfuse retains the root, the required ancestor, and the GenAI subtree
- **AND** Langfuse omits the operational-only siblings
- **AND** no retained Langfuse observation is orphaned

#### Scenario: Correlate a structured log with both trace views
- **WHEN** a structured log is emitted during an ingestion run
- **THEN** its `trace_id` identifies the same run in Tempo and Langfuse
- **AND** its `span_id` identifies its actual current span when that span belongs to the Langfuse
  projection
- **AND** `workflow_run_id` identifies the business run independently of trace retention or retry
  boundaries

#### Scenario: Retries are visible as separate spans
- **WHEN** a model request fails transiently and succeeds on retry
- **THEN** the owning GenAI workflow subtree contains one failed and one successful model span
  under the same agent in both trace views

#### Scenario: Captured GenAI content is destination-specific
- **WHEN** content capture is enabled for a run
- **THEN** Langfuse retains the approved GenAI inputs and outputs on the corresponding observations
- **AND** Tempo and Loki contain none of those payloads

#### Scenario: Content is off by default
- **WHEN** content capture is not enabled
- **THEN** no exported span or log in any destination contains prompt text, model output text,
  embedding input, tool arguments/results, or chunk text

#### Scenario: Collector unavailable
- **WHEN** an OTLP endpoint is unreachable
- **THEN** the run completes, the receipt is written, and the export failure appears in logs

#### Scenario: Large failure detail remains observable
- **WHEN** concurrent model work fails with an exception group whose rendered traceback exceeds
  Loki's structured-metadata limit
- **THEN** Loki retains exactly one correlated `ingestion.run_failed` record with bounded
  searchable attributes and the environment-appropriate failure detail

### Requirement: Model request throttling
The run SHALL limit physical Bedrock traffic process-wide to a configured number of requests
per minute (default 60) and a configured number of in-flight requests (default 60), applied to
chat extraction and embedding requests together, including retry attempts. Each physical model
request SHALL consume one rate-limit token, and no physical embedding request SHALL share an
in-flight slot with another request merely because their texts came from the same source record.
Requests beyond either limit SHALL wait rather than fail. Each Bedrock client's reusable HTTP
connection pool SHALL have capacity no smaller than the configured in-flight limit. The
single-text Titan embedding path MUST NOT expose an embedding batch-size setting because it
does not issue provider-side batch requests.

#### Scenario: In-flight limit is respected
- **WHEN** more chat and embedding requests are ready than the in-flight limit
- **THEN** at no moment are more physical Bedrock requests outstanding than the configured
  limit and every request is eventually processed

#### Scenario: Each embedding call is independently limited
- **WHEN** one source record produces more chunks than the in-flight limit
- **THEN** every chunk consumes its own in-flight slot and excess embedding calls wait

#### Scenario: Rate limit is respected
- **WHEN** the run issues more physical model requests than the per-minute limit within one
  minute
- **THEN** later requests are delayed until the limit allows them and none is rejected by the
  run itself

#### Scenario: HTTP connections cover permitted concurrency
- **WHEN** the service constructs the chat and embedding Bedrock clients
- **THEN** each client can retain at least as many reusable HTTP connections as the configured
  physical in-flight limit

#### Scenario: Legacy embedding batch configuration is removed
- **WHEN** a developer prepares ingestion configuration for the single-text Titan embedding path
- **THEN** no `EMBEDDING_BATCH_SIZE` policy key or environment override is documented or required
