# Scenario verification map

Every scenario in the three delta specs and where it is proved. `unit`, `contract`, and
`integration` entries are executable tests; `manual` entries are the Compose runs of tasks
11.3 and 11.4, which need Bedrock credentials in `.env` and are recorded below when run.

## evidence-store

| Scenario | Proof |
|---|---|
| Records are unique per source item | integration `test_running_the_same_batch_twice_leaves_identical_counts` |
| Normalized identifiers are unique per type | unit `test_keyed_entity_id_is_the_normalized_value_and_is_reusable`; integration same-batch-twice test (`uq_entities_key`) |
| Disallowed endpoint pair is rejected | unit `test_held_by_from_a_phone_is_rejected`, `test_relationship_with_disallowed_endpoints_is_rejected` |
| Allowed endpoint pair is accepted | unit `test_uses_from_person_to_device_is_accepted` |
| Model edges cannot be confirmed | unit `test_model_edges_cannot_be_confirmed`, `test_llm_relationship_cannot_be_confirmed` |
| Relationship without evidence is rejected | unit `test_relationship_without_evidence_is_rejected`, `test_at_least_one_source_reference_is_required` |
| Text-span quote is verifiable | unit `test_text_span_quote_slices_from_sample_text`, `test_identifier_entities_carry_text_span_evidence` |
| Account holder and document mention stay separate | unit `test_actor_mentions_from_different_records_stay_distinct` |
| Projection rows trace to records | unit `test_chunk_embedding_is_a_dimensionless_vector_and_payloads_are_jsonb` (FK DDL); integration same-batch-twice test (55 communications for 55 records) |
| Typed filters need no JSON access | unit `test_natural_key_constraints_and_projection_indexes_are_declared`; manual 11.3 (`t_88` by `amount_minor` and booking time) |
| Exact reference is found lexically | integration `test_bm25_query_for_the_invoice_reference_ranks_exact_matches_first`; manual 11.3 adds `t_88` |
| Nearest chunks by embedding | integration `test_bootstrap_creates_both_text_indexes_and_the_configured_vector_width` (HNSW cosine index); manual 11.3 |
| Completed run is discoverable by fingerprint | integration `test_run_ledger_round_trip` |

## ingestion-pipeline

| Scenario | Proof |
|---|---|
| Only raw evidence is consumed | unit fixture tests plus `test_materialization_reads_only_the_edition_contract_and_is_private`; real seed listing contains only `datasets/en/raw/` and `manifest.json` |
| Repository evidence is not mounted | task 11.2 rendered model: ingestion mounts only `config/ingestion` read-only; dataset mounts belong only to `evidence-seed` |
| Objects outside the edition prefix are refused | parametrized unit `test_materialization_refuses_keys_outside_the_allowed_layout`; adapter lists only `datasets/<edition>/` |
| Record counts match the manifest | unit `test_cdr_yields_55_records...`, `test_extraction_yields_18_records...`, `test_emails_yield_6_records...`, `test_documents_yield_10_records...`; integration `test_bank_sql_loads_18_accounts_and_35_transactions...`; container run persisted 142 records |
| Original values survive normalization | integration bank test (`t_88`: `amount_minor` 980000, `EUR`, `9800.00`, `INV-2231`, both timestamps) |
| Document metadata is separated from body text | unit `test_documents_yield_10_records_and_body_excludes_front_matter` |
| Phone variants share one key | unit `test_four_phone_variants_share_one_key` |
| Money never passes through binary floating point | unit `test_money_never_passes_through_binary_floating_point` |
| Local offsets normalize to UTC | unit `test_local_offset_normalizes_to_utc` |
| Exact identifiers reuse one entity | unit `test_first_run_loads_sources_indexes_chunks_extracts_and_writes_the_receipt_last` (one `PHONE:306971234567` with field and text-span refs) |
| Structured envelopes create confirmed edges | unit `test_edges.py` (four rule tests) |
| Identifier in prose is caught by rules | unit `test_phone_in_r01_is_found_at_the_right_offsets`, `test_identifier_entities_carry_text_span_evidence` |
| Whole-record chunk | unit `test_short_record_is_one_whole_chunk` |
| Chunk text is a faithful slice | unit `test_long_record_splits_into_verifiable_overlapping_windows`, `test_text_without_separators_still_progresses` |
| Embeddings are present and consistent | unit `test_wrong_dimension_is_a_permanent_failure`; manual 11.3 (no null embedding) |
| Quote must exist at the claimed offsets | unit `test_rejections_are_counted_by_outcome` (`rejected_span`) |
| Endpoint types must be allowed | unit `test_rejections_are_counted_by_outcome` (`rejected_type`) |
| Identifier endpoints resolve to rule entities | unit `test_identifier_endpoint_resolves_to_the_rule_entity_and_edge_is_proposed` |
| Semantic edges are proposed | unit application first-run test (`USES` from `PERSON:docs:R-01:alexandros-mavridis` to the phone, proposed); manual 11.3 with the real model |
| Embedded instructions remain data | unit `test_entity_candidates_are_translated_and_the_prompt_delimits_untrusted_text`; manual 11.3 (`A-D1`) |
| Co-occurrence is not a relationship | unit `test_rejections_are_counted_by_outcome` (empty quote rejected) |
| Transactions are queryable by typed columns | unit projection index DDL test; manual 11.3 |
| Communications unify three sources | unit `test_communications.py` (55, 18, 6 with normalized endpoints and UTC times); container run persisted 55 communication rows |
| Second start is a no-op | unit `test_matching_receipt_and_completed_ledger_row_skip_all_work`; manual 11.4 |
| Store reset triggers a re-run | unit `test_receipt_without_a_completed_ledger_row_re_runs`; manual 11.4 |
| Re-run does not duplicate | integration `test_running_the_same_batch_twice_leaves_identical_counts` |
| Failure leaves no receipt | unit `test_failure_leaves_a_failed_ledger_row_and_no_receipt`; container run without credentials (ledger `failed`, no receipt, non-zero exit) |
| Retries are visible as separate spans | unit `test_transient_provider_error_is_retried_then_permanent_after_the_budget` |
| Content is off by default | unit `test_content_is_absent_when_capture_is_off_and_present_when_on`, embedder span assertion |
| Collector unavailable | container run with `lgtm` reachable only via export; library shutdown never raises (`test_shutdown_is_idempotent_and_allows_reconfiguration`) |
| In-flight limit is respected | unit `test_in_flight_calls_never_exceed_the_limit_and_all_complete` |
| Rate limit is respected | unit `test_requests_beyond_the_rate_wait_and_are_never_rejected` |
| Missing model identifier | contract `test_missing_chat_model_id_fails_naming_the_setting`; container exit 2 naming the settings |
| Invalid throttle value | contract `test_zero_in_flight_limit_fails_naming_the_setting` |
| Missing bucket secret | contract `test_missing_bucket_secret_fails_naming_the_setting` |
| Service environment example matches settings | contract `test_required_optional_and_overridable_sections_match_the_settings_class` |
| Pooled database connections | integration `test_engine_reports_the_configured_pool` |

## local-app-database

| Scenario | Proof |
|---|---|
| Extensions are available | task 4.1: `CREATE EXTENSION` for `vector` 0.8.4 and `pg_search` 0.25.6 succeeded in `app` and `app_test` |
| Langfuse storage is untouched | `postgres-app` has its own image, volume, and network; the Langfuse `postgres` service is unchanged |
| Restart preserves evidence | named volume `app_postgres_data`; manual 11.4 |
| Host connection | integration suite connects to `127.0.0.1:5432/app_test` |
| Missing database password | task 4.2: `POSTGRES_APP_PASSWORD= docker compose config --quiet` fails naming the value |
| Missing evidence bucket secret | task 13.2: Compose and settings validation fail naming `EVIDENCE_S3_SECRET_KEY` |
| Seed uploads only raw evidence | task 13.1 real MinIO listing; unit boundary tests |
| Evidence key cannot reach Langfuse storage | task 13.1 real `mc ls` returned access denied for the Langfuse bucket |
| Seed is idempotent | task 13.1 second successful seed retained the exact listing fingerprint `12e055c...14b1f9` |
| Ingestion runs on first start | manual 11.3 |
| Ingestion is skipped on later starts | manual 11.4 |
| Dependents can wait for completion | `restart: "no"` and exit 0 on completed and skipped runs (`test_successful_run_exits_zero_with_outcome_on_the_root_span`) |

## Manual verification log

- 2026-09-02: digest-pinned `evidence-seed` exited 0; the dedicated key listed only the English
  manifest and 19 raw objects and was denied on the Langfuse bucket. A forced second seed retained
  the same complete JSON-listing SHA-256. The S3 integration test then passed against an isolated
  temporary MinIO bucket/user/policy and those test resources were removed.

- 2026-09-02, no Bedrock credentials available: `docker compose run --rm ingestion` with model
  identifiers set persisted 142 records across five sources, dropped the `bank_raw` staging
  schema, failed at the first embedding request with `NoCredentialsError` translated to a
  permanent failure, left a `failed` ledger row, wrote no receipt, and exited non-zero.
- Tasks 11.3 and 11.4 (completed run, `R-01` proposed `USES` edge, `INV-2231` query returning
  `R-05` and `t_88`, skip on restart, re-run after volume removal) are pending credentials.
