#!/usr/bin/env bash
# Creates .env from .env.example, generates a distinct value for every secret, and pre-fills
# AWS_REGION / BEDROCK_CHAT_MODEL_ID with the values this project was built and tested against.
# You only need to add your own AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
# afterward. Safe to re-run: it refuses to touch an existing .env.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -e .env ]; then
  echo ".env already exists -- leaving it untouched. Delete it first if you want a fresh one." >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required but was not found on PATH." >&2
  exit 1
fi

cp .env.example .env

set_var() {
  local name="$1" value="$2"
  awk -v name="$name" -v value="$value" -F'=' '
    BEGIN { OFS="=" }
    $1 == name { print name, value; next }
    { print }
  ' .env > .env.tmp && mv .env.tmp .env
}

for var in NEXTAUTH_SECRET SALT ENCRYPTION_KEY POSTGRES_PASSWORD CLICKHOUSE_PASSWORD \
           REDIS_AUTH MINIO_ROOT_PASSWORD POSTGRES_APP_PASSWORD EVIDENCE_S3_SECRET_KEY \
           AGENT_READER_PASSWORD AGENT_WRITER_PASSWORD; do
  set_var "$var" "$(openssl rand -hex 32)"
done

# The region and chat model (a Bedrock cross-region inference profile) this project was
# built and tested against.
set_var AWS_REGION "eu-west-2"
set_var BEDROCK_CHAT_MODEL_ID "global.openai.gpt-5.6-terra"

cat <<'EOF'
Wrote .env with every generated secret filled in.

Now add your own AWS credentials to .env:
  AWS_ACCESS_KEY_ID=
  AWS_SECRET_ACCESS_KEY=
  AWS_SESSION_TOKEN=   (only needed for temporary STS credentials -- leave blank otherwise)

Make sure this account has access to BEDROCK_CHAT_MODEL_ID and BEDROCK_EMBEDDING_MODEL_ID
in the Bedrock console for AWS_REGION before starting the stack.
EOF
