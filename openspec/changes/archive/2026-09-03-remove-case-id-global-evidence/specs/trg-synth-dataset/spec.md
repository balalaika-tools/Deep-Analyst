## MODIFIED Requirements

### Requirement: Deterministic canonical generation

The generator SHALL produce the canonical dataset from seed `20260305` with globally unique stable source IDs, fixed generated metadata, and reproducible content under the pinned toolchain. Generated artifacts and identifiers SHALL NOT contain a case identifier or case namespace.

#### Scenario: Repeated canonical builds are equivalent

- **WHEN** the canonical edition is generated twice with the same locale
- **THEN** corresponding files, records, IDs, and manifest hashes match

#### Scenario: A non-canonical seed is isolated

- **WHEN** generation uses a seed other than `20260305`
- **THEN** the output uses a distinct variant path and globally unique source identities rather than overwriting or aliasing the canonical output

### Requirement: Parallel English and Greek editions

The generator SHALL provide an English primary edition at `dataset/data/` and a meaning-equivalent Greek edition at `dataset/editions/el/data/`. Both editions SHALL preserve the same globally unique stable source IDs, timestamps, amounts, accounts, devices, communication endpoints, planted relationships, and expected safety behavior without encoding a case identifier.

#### Scenario: A stable source ID aligns across editions

- **WHEN** the same source ID is read from both editions
- **THEN** it describes the same event or evidence item while translated text, hashes, and character offsets may differ

## ADDED Requirements

### Requirement: Raw artifacts contain no case identity

Every generated source schema, row, document front matter block, manifest entry, expected preview, provenance artifact, and ground-truth artifact SHALL omit `case_id` and any equivalent case-partition field.

#### Scenario: Generated editions are inspected

- **WHEN** the contract validator checks both generated editions
- **THEN** no generated field or identifier encodes a case identity

