## ADDED Requirements

### Requirement: Read-only agent access surface
The evidence store SHALL expose model-authored queries only through a versioned `agent_read`
schema of plain views that project allowlisted columns from the structured projections while
preserving stable evidence identifiers, `case_id`, source references, and content hashes. Trusted
retrieval and graph code MAY query the canonical tables and indexes directly with bound
parameters. The `agent_reader` role SHALL have `SELECT` only, SHALL default to read-only
transactions, and SHALL have no `TEMP` or `CREATE` privilege and no write path to evidence.

#### Scenario: Agent reads an approved view
- **WHEN** the `agent_reader` role selects allowlisted transaction fields from an `agent_read` view
- **THEN** the query returns rows for the bound case together with their evidence identifiers and
  provenance

#### Scenario: Agent cannot mutate evidence
- **WHEN** the `agent_reader` role attempts an insert, update, delete, truncate, or schema change
- **THEN** PostgreSQL rejects the operation and canonical evidence remains unchanged

#### Scenario: Temporary object cannot shadow a view
- **WHEN** the `agent_reader` role attempts to create a temporary relation
- **THEN** PostgreSQL denies temporary-object creation

### Requirement: Server-controlled case scope for agent reads
Every `agent_read` view SHALL filter rows by `case_id = current_setting('app.case_id', true)`.
Before executing an approved model-authored statement, trusted executor code SHALL open a read-only
transaction on the reader pool, set `app.case_id` with `SET LOCAL` from invocation context, and
run the statement inside that transaction so the setting cannot outlive it. Retrieval and graph
queries issued by trusted code SHALL bind `case_id` as a query parameter. Model-authored SQL SHALL
NOT be accepted as the authorization source; the SQL policy gate SHALL reject `SET`, `set_config`,
`current_setting`, and any construct that could alter or read session state. A missing case
setting SHALL yield no rows.

#### Scenario: Missing case predicate remains isolated
- **WHEN** an approved agent query selects from `agent_read` without a case predicate
- **THEN** PostgreSQL returns only rows for the case set by the trusted executor

#### Scenario: Contradictory case predicate cannot cross the scope
- **WHEN** an approved agent query explicitly filters for a different case identifier
- **THEN** PostgreSQL returns no rows from that other case

#### Scenario: SQL cannot replace the trusted scope
- **WHEN** agent-authored SQL attempts `SET`, `set_config`, or `current_setting`
- **THEN** the SQL policy gate rejects the plan before any database round trip

#### Scenario: Pooled connection is reused for another case
- **WHEN** a connection previously used for case A is checked out for case B
- **THEN** the new transaction sets only case B and cannot return case A rows

#### Scenario: Executor omits the case setting
- **WHEN** a defect causes the executor to run a statement without setting `app.case_id`
- **THEN** the `agent_read` views return no rows
