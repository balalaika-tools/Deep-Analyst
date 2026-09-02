## Why

`dataset/` already contains a working, deterministic synthetic investigation dataset (generator, two language editions, contract tests) built against the binding contract in `docs/DATASET_SPEC.md`. That contract predates the implementation and still frames itself as forward-looking ("nothing here claims they are already implemented"), and no OpenSpec capability currently describes the dataset's observable contract. Without a spec, there is no OpenSpec-tracked source of truth to diff future dataset changes against, and no `openspec/specs/` entry shows up when scanning capabilities for this project. This change captures the dataset's current, already-implemented behavior as an OpenSpec capability spec, so `docs/DATASET_SPEC.md` and `dataset/README.md` gain an OpenSpec-tracked counterpart.

## What Changes

- Add a new `trg-synth-dataset` capability spec describing the dataset generator's observable contract: deterministic/reproducible generation, dual-language (en/el) edition equivalence, output layout and exact feed totals, raw source schemas, the canonical envelope and derived-table schemas, manifest/provenance hashing, versioned policy artifact, quarantine fixtures, ground-truth/test-only artifact isolation, and the build/verify CLI.
- No code changes. This is documentation/spec-capture of existing behavior in `dataset/make_dataset.py`, `dataset/data/`, `dataset/editions/el/data/`, and `dataset/tests/test_dataset_contract.py`.
- Exhaustive fixture-level detail (exact personas, pinned messages/transactions/documents, golden-question wording, ground-truth assertion tables) remains authoritative in `docs/DATASET_SPEC.md`; the new spec states the testable structural contract and references that document for pinned narrative content rather than duplicating ~700 lines of fixture text.

## Capabilities

### New Capabilities
- `trg-synth-dataset`: The deterministic synthetic investigation dataset — its generation guarantees, dual-language edition contract, output schemas, provenance/hashing, versioned policy, quarantine handling, and test-only ground-truth isolation.

### Modified Capabilities
(none — no existing `openspec/specs/` capabilities yet)

## Impact

- Affected paths (read-only reference, no edits): `dataset/make_dataset.py`, `dataset/data/`, `dataset/editions/el/data/`, `dataset/tests/test_dataset_contract.py`, `dataset/README.md`, `docs/DATASET_SPEC.md`.
- New paths: `openspec/changes/document-trg-synth-dataset/**`, and after archive, `openspec/specs/trg-synth-dataset/spec.md`.
- No runtime systems, APIs, or dependencies are affected; this is a planning/documentation artifact only.
