"""Static constants shared across the TRG synthetic dataset generator."""

CANONICAL_SEED = 20260305
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "el")


DATASET_VERSIONS = {
    "en": "trg-synth-en-v1.0.0",
    "el": "trg-synth-el-v1.0.0",
}


POLICY_VERSION = "trg-policy-v1.0.0"
GENERATED_AT = "2026-03-11T00:00:00Z"


CDR_COLUMNS = [
    "record_id",
    "seq",
    "record_type",
    "subscriber_msisdn",
    "calling_msisdn",
    "called_msisdn",
    "imei",
    "cell_id",
    "ts_local",
    "duration_s",
    "sms_len",
    "source_version",
]


EXTRACTION_COLUMNS = [
    "msg_id",
    "imei",
    "subscriber_msisdn",
    "direction",
    "peer",
    "app",
    "ts_utc",
    "body",
    "source_version",
]


ACCOUNT_COLUMNS = [
    "account_id",
    "iban",
    "holder_name",
    "holder_type",
    "bic",
    "opened_date",
    "source_version",
]


TRANSACTION_COLUMNS = [
    "txn_id",
    "booking_ts_utc",
    "value_date",
    "debtor_name",
    "debtor_iban",
    "debtor_bic",
    "creditor_name",
    "creditor_iban",
    "creditor_bic",
    "amount_text",
    "currency",
    "status",
    "remittance_info",
    "source_version",
]


SYNTHETIC_NOTICE = (
    "SYNTHETIC DATA ONLY\n"
    "This fixture contains no real people, accounts, devices, or events.\n"
    "Format-valid identifiers exist only for deterministic software testing.\n"
)
