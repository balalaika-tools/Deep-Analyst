## MODIFIED Requirements

### Requirement: Validated and bounded tool boundary

Each investigation tool SHALL accept and return a versioned validated contract. Model-authored input SHALL be validated before database, embedding, or graph work. Tools SHALL operate over the global evidence store and SHALL neither accept nor inherit a hidden case scope. Results SHALL identify status, tool-call identifier, consumed budget, warnings, and evidence references or a safe failure code without exposing credentials, raw diagnostics, or stack traces.

#### Scenario: Invalid input performs no I/O

- **WHEN** a tool receives an argument that fails its contract
- **THEN** it returns a bounded validation failure and performs no database, embedding, or model operation

#### Scenario: Investigation combines differently sourced evidence

- **WHEN** relevant evidence records have different legacy provenance groupings
- **THEN** the tool can return all matching records in one bounded result

#### Scenario: Model-supplied scope is not authoritative

- **WHEN** model-authored tool input includes the obsolete case field or attempts to introduce hidden scope
- **THEN** strict validation rejects the value and no evidence partition is applied

### Requirement: Hybrid self-correcting evidence search

`search_evidence` SHALL run its bounded lexical and vector retrieval loop over the complete evidence corpus, combine rankings deterministically, deduplicate by globally stable chunk identity, preserve provenance, and apply only source, time, exclusion, and other relevance filters explicitly selected through the tool intent. No retrieval attempt SHALL apply an implicit case filter.

#### Scenario: Global lexical and semantic evidence are fused

- **WHEN** lexical and vector search find relevant chunks anywhere in the store
- **THEN** the tool returns one deterministically ordered candidate per chunk with modality contributions and provenance preserved

#### Scenario: Lexical and semantic evidence are fused deterministically

- **WHEN** both retrieval modalities return overlapping and distinct global chunks
- **THEN** one deterministically ordered candidate per chunk is returned with both modality contributions preserved

#### Scenario: An insufficient first attempt is reformulated

- **WHEN** the nested agent judges the first global retrieval insufficient
- **THEN** it may issue a distinct bounded query excluding chunks already observed

#### Scenario: A later tool invocation excludes prior evidence

- **WHEN** a later invocation supplies globally stable chunk identifiers already indexed for the thread
- **THEN** every retrieval modality excludes those chunks without limiting the remaining corpus

#### Scenario: Repeated retrieval plans do not execute

- **WHEN** a normalized retrieval fingerprint repeats within an invocation
- **THEN** the tool rejects it without I/O and preserves the attempt bound

#### Scenario: Exhausted retrieval has an explicit meaning

- **WHEN** three distinct global retrieval attempts do not produce sufficient support
- **THEN** the tool returns an incomplete or no-support status and does not represent the miss as proof of absence

#### Scenario: Nested agent selects an unretrieved identifier

- **WHEN** the nested result names a chunk not returned during that invocation
- **THEN** the tool drops it and records a warning

### Requirement: Deterministic SQL policy enforcement

Every statement submitted to `execute_sql` SHALL pass the existing deterministic read-only AST policy and resource limits. The executor SHALL run approved statements against global `agent_read` views without setting or requiring case-related session state. Only validated bounded rows with globally resolvable provenance SHALL leave the executor.

#### Scenario: A safe parameterized select executes

- **WHEN** the nested agent proposes one parameterized select over allowlisted columns
- **THEN** the executor runs it read-only across the global evidence store within configured row, byte, lock, and time limits

#### Scenario: An attempted mutation is blocked

- **WHEN** the nested agent proposes a mutation, setting change, additional statement, or unqualified relation
- **THEN** the policy gate rejects the plan before database execution

#### Scenario: An attempted copy is blocked

- **WHEN** a statement proposes `COPY`, a data-changing CTE, or an additional statement
- **THEN** the policy gate rejects it before a database round trip

#### Scenario: Select cannot create or shadow an object

- **WHEN** a statement proposes `SELECT INTO`, a temporary relation, or an unqualified relation
- **THEN** the policy gate rejects it before execution

#### Scenario: A query cannot weaken its resource limit

- **WHEN** authored SQL omits a limit or requests more than the configured maximum
- **THEN** server-owned row, byte, and time limits remain effective

#### Scenario: Database diagnostics are safe to revise from

- **WHEN** an eligible database error occurs
- **THEN** the nested agent receives only a bounded safe classification and no raw internal diagnostic

### Requirement: Bounded sourced graph traversal

`find_connections` SHALL execute deterministic bounded traversal over all canonical entities and relationships. Every returned vertex and edge SHALL carry a globally stable identifier and resolvable source references, and no hidden partition predicate SHALL exclude a valid path.

#### Scenario: Traversal remains within bounds

- **WHEN** a seed belongs to a highly connected global component
- **THEN** traversal stops at the effective depth, path, and row limits

#### Scenario: A path spans differently sourced records

- **WHEN** a supported connection uses records from different source collections
- **THEN** the complete path is returned with provenance for every vertex and edge

#### Scenario: Proposed relationships remain qualified

- **WHEN** a returned path contains an LLM-derived proposed relationship
- **THEN** the result labels it proposed and includes its supporting source references

#### Scenario: Global seed is resolvable

- **WHEN** a globally identified seed exists anywhere in the configured evidence store
- **THEN** traversal may resolve it without applying or revealing a legacy partition boundary
