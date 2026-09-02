# Ingestion Pipeline Specification

## Purpose

Turn the synthetic case fixture into queryable evidence: common records, normalized
identifiers, deterministic and constrained LLM-derived graph edges, and embedded text chunks,
through a one-shot, idempotent, observable run.

## Requirements

### Requirement: Runtime evidence boundary
The ingestion run SHALL read its evidence from the configured object-store bucket under
`datasets/<edition>/`, which SHALL contain only the `raw/` directory and `manifest.json` of one
dataset edition. It MUST NOT read `ground_truth.json`, `expected/`, or `fixtures/quarantine/`,
and the containerized run SHALL have no filesystem access to the repository dataset.

#### Scenario: Only raw evidence is consumed
- **WHEN** a run completes against the English edition
- **THEN** every stored record's source path is below `raw/` and no object outside
  `datasets/<edition>/raw/` or `datasets/<edition>/manifest.json` was read

#### Scenario: Repository evidence is not mounted
- **WHEN** the ingestion container is started by Compose
- **THEN** it has no bind mount below `data/` and reads evidence only through the bucket
  endpoint with the dedicated evidence access key

#### Scenario: Objects outside the edition prefix are refused
- **WHEN** the bucket also holds objects outside `datasets/<edition>/`
- **THEN** the run never lists or downloads them

### Requirement: Common record envelope
Every source item SHALL become exactly one record carrying the case identifier, source
system, stable source record identifier, record type, UTC event time with the original
timestamp retained, searchable text when the source has prose, a structured payload
preserving source-specific fields and original lexemes, the source path, and a content hash.

#### Scenario: Record counts match the manifest
- **WHEN** a run completes against the English edition
- **THEN** the store holds 55 CDR, 18 device-extraction, 6 email, 18 account, 35 transaction,
  and 10 document records for `case_trg_001`

#### Scenario: Original values survive normalization
- **WHEN** transaction `t_88` is ingested
- **THEN** its payload contains `amount_minor` 980000, currency `EUR`, the original amount
  lexeme, the remittance reference `INV-2231`, and both the UTC booking time and the original
  timestamp string

#### Scenario: Document metadata is separated from body text
- **WHEN** a Markdown document with YAML front matter is ingested
- **THEN** the front-matter fields are stored in the payload and the record text contains only
  the document body

### Requirement: Deterministic normalization
Hard fields SHALL be normalized by code, never by a model: phone numbers to a canonical
digits-only international form using Greece as the default region, email addresses to
lowercase, IBANs to uppercase without whitespace, invoice references to uppercase, decimal
money to integer minor units, and timestamps to UTC.

#### Scenario: Phone variants share one key
- **WHEN** the values `+30 697 123 4567`, `697 123 4567`, `306971234567`, and `+306971234567`
  are normalized
- **THEN** all four produce the same normalized key

#### Scenario: Money never passes through binary floating point
- **WHEN** the amount text `9800.00` with currency `EUR` is normalized
- **THEN** the result is the integer 980000 minor units and the original text is retained

#### Scenario: Local offsets normalize to UTC
- **WHEN** a CDR timestamp `2026-02-20T09:10:00+02:00` is normalized
- **THEN** the UTC event time is `2026-02-20T07:10:00Z` and the original string is retained

### Requirement: Deterministic entities and confirmed relationships
Rules SHALL create or reuse typed identifier, asset, event, and reference entities from
structured fields and from identifier patterns in free text, and SHALL create `confirmed`
relationships only from explicit structured fields. When a rule and a model cover the same
exact identifier, the rule's entity SHALL be used.

#### Scenario: Exact identifiers reuse one entity
- **WHEN** the same normalized phone appears in a document, an email signature, a CDR row,
  and a device-extraction row
- **THEN** exactly one `PHONE` entity exists for that key, with a source reference for each
  occurrence

#### Scenario: Structured envelopes create confirmed edges
- **WHEN** a CDR row, an email, an account row, and a transaction row are ingested
- **THEN** the store contains `COMMUNICATED_WITH`, `HELD_BY`, `TRANSFERRED_TO`, and
  `REFERENCES` relationships with status `confirmed`, method `deterministic`, and a
  field-kind source reference each

#### Scenario: Identifier in prose is caught by rules
- **WHEN** document `R-01` is ingested
- **THEN** a `PHONE` entity for `+30 697 123 4567` exists with a text-span source reference
  before any model output is considered

### Requirement: Text chunking with offsets
Every record with searchable text SHALL produce at least one chunk with character start and
end offsets into the record text, and the chunk text SHALL equal the record text at those
offsets. Records at fixture scale SHALL remain whole.

#### Scenario: Whole-record chunk
- **WHEN** a document shorter than the configured window is chunked
- **THEN** exactly one chunk exists with start 0 and end equal to the text length

#### Scenario: Chunk text is a faithful slice
- **WHEN** any chunk is read back
- **THEN** slicing the parent record text by the chunk's offsets yields the chunk text

### Requirement: Chunk embeddings
Every chunk SHALL receive an embedding from the configured embedding model, and all stored
embeddings SHALL share the configured dimension.

#### Scenario: Embeddings are present and consistent
- **WHEN** a run completes
- **THEN** every chunk has an embedding of the configured dimension and none is null

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

### Requirement: Structured projections are populated
The run SHALL populate the typed projection tables defined by the evidence store from the
records it creates, so that structured filters use typed columns rather than JSON payloads.

#### Scenario: Transactions are queryable by typed columns
- **WHEN** a run completes
- **THEN** filtering transactions by `amount_minor` equal to 980000 and a UTC booking time
  on 2026-03-05 returns `t_88` with its debtor and creditor IBANs

#### Scenario: Communications unify three sources
- **WHEN** a run completes
- **THEN** the communications projection contains 55 CDR, 18 device-extraction, and 6 email
  rows, each with normalized endpoints and a UTC event time

### Requirement: Idempotent and skippable runs
The run SHALL compute a fingerprint from the dataset manifest, the embedding model
identifier, the chunking configuration, and a pipeline version. It SHALL skip all work and
exit successfully when the receipt object `indexes/<edition>/receipt.json` in the evidence
bucket holds that fingerprint and the store records a completed run with the same
fingerprint. Otherwise it SHALL run, upserting on natural keys, and write the receipt object
only after the run succeeds.

#### Scenario: Second start is a no-op
- **WHEN** the run starts and both the receipt object and the completed-run row match the
  current fingerprint
- **THEN** no source is read, no model is called, and the process exits with status 0

#### Scenario: Empty index prefix runs
- **WHEN** the bucket holds no object under `indexes/<edition>/`
- **THEN** the pipeline runs fully and, on success, the receipt object exists afterwards

#### Scenario: Store reset triggers a re-run
- **WHEN** the receipt matches but the store has no completed run for that fingerprint
- **THEN** the pipeline runs fully

#### Scenario: Re-run does not duplicate
- **WHEN** the pipeline runs twice against the same store
- **THEN** record, entity, relationship, chunk, and projection counts are identical after
  both runs

#### Scenario: Failure leaves no receipt
- **WHEN** the run fails after retries
- **THEN** no receipt object is written, the process exits non-zero, and the next start re-runs

### Requirement: Run telemetry
Each run SHALL emit one coordinator root trace containing the ingestion lifecycle, source loading,
and run outcome. Every source record that produces chunks SHALL emit one bounded `ingest record`
root trace for its processing attempt. A `finalize ingestion` root trace SHALL own bulk chunk and
graph persistence. Record and finalization roots SHALL have no parent, SHALL use trace identities
different from the coordinator, and SHALL carry a span link to the coordinator. Tempo SHALL
receive this complete linked trace family.

Langfuse SHALL receive an ancestor-closed GenAI projection of each coordinator or record trace
that contains classified spans. Every retained Langfuse span SHALL keep the same trace ID, span
ID, and parent relationship it has in Tempo, and no retained span SHALL reference a parent that
is absent from Langfuse. Source-load, database, HTTP-client, persistence, finalization, and other
operational-only spans SHALL remain available in Tempo and SHALL be absent from Langfuse unless
one is a required ancestor of retained GenAI work.

Within each record trace, every prose chunk SHALL have one `invoke_workflow extract_chunk`
subtree containing its entity and relationship agent invocations and physical chat attempts. The
same record trace SHALL have one `invoke_workflow indexing_embeddings` subtree containing one
physical embedding-model span for each of the record's chunks. Coordinator, record-root, and
retained GenAI projection spans SHALL carry `app.workflow.run.id`; record and chunk metadata SHALL
appear only on the scopes to which they apply. The same run identifier SHALL be emitted as
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

#### Scenario: Record work starts linked bounded traces
- **WHEN** one ingestion run containing GenAI work is exported successfully
- **THEN** Tempo contains the coordinator, one root trace per processed record, and a finalization
  root trace
- **AND** every record and finalization root has no parent and links to the coordinator
- **AND** Langfuse contains an ancestor-closed projection for each classified coordinator or
  record trace
- **AND** every observation retained in both backends has the same trace and span identity

#### Scenario: Inspect one chunk extraction workflow
- **WHEN** entity and relationship extraction complete for one prose chunk
- **THEN** the record's Langfuse trace contains one `invoke_workflow extract_chunk` subtree with
  entity and relationship `invoke_agent` children and their physical `chat` children
- **AND** the trace is searchable by workflow run, record, and chunk identifiers

#### Scenario: Inspect one record's embedding workflow
- **WHEN** a source record produces multiple chunks that are embedded
- **THEN** the record's Langfuse trace contains one `invoke_workflow indexing_embeddings`
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

### Requirement: Explicit configuration
The service SHALL fail at startup with a message naming the missing value when the database
URL, evidence bucket endpoint, bucket name, evidence access key or secret, dataset edition,
Bedrock region, chat model identifier, or embedding model identifier is absent. Database pool
size, overflow, timeout, and pre-ping, the model throttle limits, chunking, embedding width,
and telemetry policy SHALL be application settings baselined in a committed YAML file with
documented defaults, overridable per process by an environment variable of the same name; the
YAML baseline SHALL be rejected when it names a deployment value. Every service directory
SHALL carry a `.env.example` listing its required, optional, and overridable variables. AWS
credentials SHALL be resolved by the AWS SDK credential chain and SHALL NOT be modeled as
service settings.

#### Scenario: Missing model identifier
- **WHEN** the chat model identifier is not set
- **THEN** startup fails before any database or network access and names the missing setting

#### Scenario: Invalid throttle value
- **WHEN** the in-flight limit is set to zero
- **THEN** startup fails naming the setting

#### Scenario: Deployment value in the policy baseline is rejected
- **WHEN** the YAML baseline names the chat model identifier
- **THEN** startup fails naming the offending key

#### Scenario: Service environment example matches the settings contract
- **WHEN** the service's `.env.example` is compared with its settings class
- **THEN** the required section names exactly the required variables, the optional section
  the defaulted deployment variables, and the overridable section the YAML policy keys

#### Scenario: Pooled database connections
- **WHEN** the run persists several sources concurrently
- **THEN** it reuses connections from one bounded pool sized by settings and never opens more
  than pool size plus overflow connections
