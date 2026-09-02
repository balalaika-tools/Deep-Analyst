# Evidence Store Specification

## Purpose

Define the PostgreSQL evidence store shared by ingestion and the future investigation agent:
three source-of-truth tables, the small ontology, provenance references, derived typed
projections, and the lexical and vector indexes over text chunks.

## Requirements

### Requirement: Source-of-truth tables
The store SHALL hold `records`, `entities`, and `relationships` as the only source-of-truth
tables. A record SHALL carry a globally unique identity, source system, source record identifier,
record type, nullable UTC event time and original time, nullable text, structured payload, source
path, and content hash. An entity SHALL carry a globally unique identity, entity type, label,
nullable normalized key, and source references. A relationship SHALL carry a globally unique
identity, subject and object entity references, predicate, status, method, nullable occurred-at and
validity interval, source references, and attributes. None of these tables or their keys SHALL
contain an evidence partition identifier.

#### Scenario: Records are unique per source item
- **WHEN** two records with the same source system and source record identifier are written
- **THEN** the second write updates the first row rather than creating a duplicate

#### Scenario: Normalized identifiers are unique per type
- **WHEN** two entities with the same entity type and normalized key are written
- **THEN** exactly one entity row exists for that key

### Requirement: Small ontology with endpoint rules
Entity types SHALL be limited to `PERSON`, `ORGANIZATION`, `DEVICE`, `FINANCIAL_ACCOUNT`,
`VESSEL`, `PHONE`, `EMAIL_ADDRESS`, `TRANSACTION`, `LOCATION`, and `INVOICE_REF`. Predicates
SHALL be limited to `USES`, `ASSOCIATED_WITH`, `DIRECTOR_OF`, `KIN_OF`, `HELD_BY`,
`COMMUNICATED_WITH`, `TRANSFERRED_TO`, and `REFERENCES`, each with the allowed subject and
object types defined in the data model. The store SHALL reject a relationship whose endpoint
types are not allowed for its predicate.

#### Scenario: Disallowed endpoint pair is rejected
- **WHEN** a `HELD_BY` relationship from a `PHONE` subject is written
- **THEN** the write fails with an explicit validation error

#### Scenario: Allowed endpoint pair is accepted
- **WHEN** a `USES` relationship from a `PERSON` to a `DEVICE` is written
- **THEN** the relationship is stored

### Requirement: Status and method semantics
Relationship status SHALL be `confirmed` or `proposed` and method SHALL be `deterministic` or
`llm`. A `confirmed` relationship SHALL only have method `deterministic`; every relationship
with method `llm` SHALL have status `proposed`.

#### Scenario: Model edges cannot be confirmed
- **WHEN** a relationship with method `llm` and status `confirmed` is written
- **THEN** the write fails with an explicit validation error

### Requirement: Provenance on every extracted fact
Every entity and relationship SHALL carry at least one source reference identifying a record
and a locator. A text-span locator SHALL include the field, character start, character end,
and quote; a field locator SHALL include the field name.

#### Scenario: Relationship without evidence is rejected
- **WHEN** a relationship with an empty source-reference list is written
- **THEN** the write fails with an explicit validation error

#### Scenario: Text-span quote is verifiable
- **WHEN** a text-span source reference is read together with its record
- **THEN** the record text sliced by the locator's offsets equals the locator's quote

### Requirement: Actors are never merged by name
Two `PERSON` or `ORGANIZATION` entities created from different sources with equal or similar
labels SHALL remain distinct entities; only exact normalized identifiers reuse an entity.

#### Scenario: Account holder and document mention stay separate
- **WHEN** `Alexandros Mavridis` appears as an account holder and as a document mention
- **THEN** two distinct `PERSON` entities exist, each with its own source references

### Requirement: Derived typed projections
The store SHALL provide rebuildable `transactions`, `accounts`, `communications`, and `chunks`
projections referencing globally identified parent records. Projections SHALL omit evidence
partition identity and SHALL be indexed for global time, amount, identifier, endpoint, source, and
similarity filters.

#### Scenario: Projection rows trace to records
- **WHEN** any projection row is read
- **THEN** it references an existing globally identified record and can be rebuilt from that
  record's payload

#### Scenario: Typed filters need no JSON access
- **WHEN** transactions are filtered by amount range and UTC time window
- **THEN** the query uses typed projection columns without a partition predicate

### Requirement: Lexical and vector text indexes
Chunks SHALL be searchable globally by BM25 ranking over text and cosine similarity over
embeddings, with optional source-system and event-time filters. Search SHALL NOT require or apply
an evidence partition filter.

#### Scenario: Exact reference is found lexically
- **WHEN** the lexical index is queried for `INV-2231`
- **THEN** chunks from document `R-05` and transaction `t_88` are returned and no chunk
  containing only `INV-2237` ranks above them

#### Scenario: Nearest chunks by embedding
- **WHEN** the vector index is queried with an embedding and a top-k
- **THEN** at most k chunks from the global corpus are returned in similarity order

### Requirement: Ingestion run ledger
The store SHALL record each ingestion run with its fingerprint, dataset version, embedding
model identifier, start and completion times, outcome, and summary counts.

#### Scenario: Completed run is discoverable by fingerprint
- **WHEN** a run completes successfully
- **THEN** a ledger row with outcome `completed` exists for its fingerprint

### Requirement: Read-only agent access surface
The evidence store SHALL expose model-authored queries only through a versioned `agent_read`
schema of plain views that project allowlisted columns from the global structured projections while
preserving stable evidence identifiers, source references, and content hashes. Trusted
retrieval and graph code MAY query the canonical tables and indexes directly with bound
parameters. The `agent_reader` role SHALL have `SELECT` only, SHALL default to read-only
transactions, and SHALL have no `TEMP` or `CREATE` privilege and no write path to evidence.

#### Scenario: Agent reads an approved view
- **WHEN** the `agent_reader` role selects allowlisted transaction fields from an `agent_read` view
- **THEN** the query can return every matching row in the evidence store with its provenance

#### Scenario: Agent cannot mutate evidence
- **WHEN** the `agent_reader` role attempts an insert, update, delete, truncate, or schema change
- **THEN** PostgreSQL rejects the operation and canonical evidence remains unchanged

#### Scenario: Temporary object cannot shadow a view
- **WHEN** the `agent_reader` role attempts to create a temporary relation
- **THEN** PostgreSQL denies temporary-object creation

#### Scenario: SQL cannot alter session state
- **WHEN** agent-authored SQL attempts `SET`, `set_config`, or `current_setting`
- **THEN** the SQL policy gate rejects the plan before any database round trip
