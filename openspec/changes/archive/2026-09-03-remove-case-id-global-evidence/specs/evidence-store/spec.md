## MODIFIED Requirements

### Requirement: Source-of-truth tables

The store SHALL hold `records`, `entities`, and `relationships` as the only source-of-truth tables. A record SHALL carry a globally unique identity, source system, source record identifier, record type, nullable UTC event time and original time, nullable text, structured payload, source path, and content hash. An entity SHALL carry a globally unique identity, entity type, label, nullable normalized key, and source references. A relationship SHALL carry a globally unique identity, subject and object entity references, predicate, status, method, nullable occurred-at and validity interval, source references, and attributes. None of these tables or their keys SHALL contain a case identifier.

#### Scenario: Records are unique per source item

- **WHEN** two records with the same source system and source record identifier are written
- **THEN** the second write updates the first row rather than creating a duplicate

#### Scenario: Normalized identifiers are unique per type

- **WHEN** two entities with the same entity type and normalized key are written
- **THEN** exactly one entity row exists for that key

### Requirement: Derived typed projections

The store SHALL provide rebuildable `transactions`, `accounts`, `communications`, and `chunks` projections referencing globally identified parent records. Projections SHALL omit case identity and SHALL be indexed for global time, amount, identifier, endpoint, source, and similarity filters.

#### Scenario: Projection rows trace to records

- **WHEN** any projection row is read
- **THEN** it references an existing globally identified record and can be rebuilt from that record's payload

#### Scenario: Typed filters need no JSON access

- **WHEN** transactions are filtered by amount range and UTC time window
- **THEN** the query uses typed projection columns without a case predicate

### Requirement: Lexical and vector text indexes

Chunks SHALL be searchable globally by BM25 ranking over text and cosine similarity over embeddings, with optional source-system and event-time filters. Search SHALL NOT require or apply a case filter.

#### Scenario: Exact reference is found lexically

- **WHEN** the lexical index is queried for `INV-2231`
- **THEN** chunks from document `R-05` and transaction `t_88` are returned and no chunk containing only `INV-2237` ranks above them

#### Scenario: Nearest chunks by embedding

- **WHEN** the vector index is queried with an embedding and a top-k
- **THEN** at most k chunks from the global corpus are returned in similarity order

### Requirement: Read-only agent access surface

The evidence store SHALL expose model-authored queries only through versioned `agent_read` views that project allowlisted columns from the global structured projections while preserving stable evidence identifiers, source references, and content hashes. Trusted retrieval and graph code MAY query canonical tables and indexes directly with bound parameters. The reader role SHALL remain read-only with no temporary-object or schema-creation privilege.

#### Scenario: Agent reads an approved view

- **WHEN** the reader selects allowlisted transaction fields from an `agent_read` view
- **THEN** the query can return every matching row in the evidence store with its provenance

#### Scenario: Agent cannot mutate evidence

- **WHEN** the reader attempts a data or schema mutation
- **THEN** PostgreSQL rejects it and canonical evidence remains unchanged

#### Scenario: Temporary object cannot shadow a view

- **WHEN** the reader attempts to create a temporary relation
- **THEN** PostgreSQL denies temporary-object creation

#### Scenario: SQL cannot alter session state

- **WHEN** agent-authored SQL attempts `SET`, `set_config`, or `current_setting`
- **THEN** the SQL policy gate rejects the plan before any database round trip

## REMOVED Requirements

### Requirement: Server-controlled case scope for agent reads

**Reason**: The product has one global evidence corpus, and hidden case scope prevents the agent from seeing relevant stored evidence.

**Migration**: Remove the session setting, view predicates, bound case parameters, case columns, case indexes, and cross-case denial tests; retain the existing read-only and bounded-query controls.
