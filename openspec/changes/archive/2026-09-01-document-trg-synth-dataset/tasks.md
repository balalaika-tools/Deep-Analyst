## 1. Verify the spec against the implemented dataset

- [x] 1.1 Run `python3 dataset/make_dataset.py --seed 20260305 --check` and `python3 dataset/make_dataset.py --seed 20260305 --locale el --check` and confirm both pass, verifying "Deterministic, Reproducible Generation" and "Output Layout and Exact Feed Totals" — both editions verified 142/142 source records
- [x] 1.2 Run `python3 -m unittest discover -s dataset/tests -p 'test_*.py'` and confirm all contract tests pass, verifying "Build, Verify, and Contract-Test CLI" — 8/8 tests passed
- [x] 1.3 Diff the raw schema requirements (CDR, extraction, email, bank, documents) against the actual column/field lists produced by `dataset/make_dataset.py` and the sample files under `dataset/data/raw/`, and record any drift as a follow-up — CDR header, extraction JSONL fields, email `X-*` headers, and `bank.sql` `accounts`/`transactions` DDL match the spec exactly; document front matter matches (plus one additional `synthetic_data: true` field, consistent with the spec's "at minimum" wording); no drift found
- [x] 1.4 Confirm `data/manifest.json` in both editions contains file and record SHA-256 hashes matching a fresh recomputation, verifying "Manifest and Provenance Hashing" — covered by the `--check` runs in 1.1 and by `test_public_verifier_rejects_raw_file_hash_tampering`/`test_public_verifier_rejects_record_hash_tampering`
- [x] 1.5 Confirm `data/fixtures/quarantine/*` fixtures are excluded from `data/manifest.json` corpus totals and from contract-test corpus counts, verifying "Quarantine Fixtures" — quarantine fixtures are tracked under a separate `quarantine` manifest key, absent from `non_corpus_artifacts`/corpus totals; `test_quarantine_and_runtime_exclusions_are_outside_corpus` passes
- [x] 1.6 Confirm no code path in `dataset/` or its consumers reads `data/ground_truth.json` or `data/expected/` outside the test suite, verifying "Ground-Truth and Test-Only Artifact Isolation" — repo-wide grep found references only in `dataset/tests/` and the generator itself; no other consumer exists yet

## 2. Sync the spec into openspec/specs

- [x] 2.1 Run `/opsx:sync` (or `openspec archive document-trg-synth-dataset`) to publish the delta spec to `openspec/specs/trg-synth-dataset/spec.md` — done via `/opsx:sync document-trg-synth-dataset`
- [x] 2.2 Confirm `openspec/specs/trg-synth-dataset/spec.md` exists, carries the `## Purpose` section, and lists all requirements added in this change — confirmed, 15/15 requirements present
- [x] 2.3 Run `openspec validate document-trg-synth-dataset --strict` before archiving and resolve any reported issues — passed (`Change 'document-trg-synth-dataset' is valid`); `openspec validate --specs` also passed
