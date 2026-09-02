## Purpose

Turn the synthetic case fixture into queryable evidence: common records, normalized
identifiers, deterministic and constrained LLM-derived graph edges, and embedded text chunks,
through a one-shot, idempotent, observable run.

## ADDED Requirements

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
`proposed` and method `llm`. Transient model failures SHALL be retried a bounded number of
times; a model failure after retries SHALL fail the run.

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
Each run SHALL emit one root trace containing one span per source adapter, one span per
physical model request with GenAI semantic-convention attributes and token usage, and one
span per embedding batch. It SHALL record standard GenAI operation-duration and token-usage
metrics plus counters for extraction candidates by kind and outcome and for indexed chunks by
source. Logs SHALL be structured JSON events carrying the current trace and span identifiers.
Prompt, completion, and document content SHALL be captured only when content capture is
explicitly enabled. Telemetry export failure SHALL NOT fail the run.

#### Scenario: Retries are visible as separate spans
- **WHEN** a model request fails transiently and succeeds on retry
- **THEN** the trace contains one failed and one successful model span under the same parent

#### Scenario: Content is off by default
- **WHEN** content capture is not enabled
- **THEN** no exported span or log contains prompt text, model output text, or chunk text

#### Scenario: Collector unavailable
- **WHEN** the OTLP endpoint is unreachable
- **THEN** the run completes, the receipt is written, and the export failure appears in logs

### Requirement: Model request throttling
The run SHALL limit model traffic process-wide to a configured number of requests per minute
(default 60) and a configured number of in-flight requests (default 60), applied to chat
extraction and embedding calls together, including retry attempts. Requests beyond the limits
SHALL wait rather than fail.

#### Scenario: In-flight limit is respected
- **WHEN** more chunks are ready for extraction than the in-flight limit
- **THEN** at no moment are more model calls outstanding than the limit and every chunk is
  eventually processed

#### Scenario: Rate limit is respected
- **WHEN** the run issues more model requests than the per-minute limit within one minute
- **THEN** later requests are delayed until the limit allows them and none is rejected by the
  run itself

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
