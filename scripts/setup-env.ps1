# Creates .env from .env.example, generates a distinct value for every secret, and pre-fills
# AWS_REGION / BEDROCK_CHAT_MODEL_ID with the values this project was built and tested against.
# You only need to add your own AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
# afterward. Safe to re-run: it refuses to touch an existing .env.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Test-Path .env) {
    Write-Error ".env already exists -- leaving it untouched. Delete it first if you want a fresh one."
    exit 1
}

Copy-Item .env.example .env

function New-HexSecret {
    # Uses the RandomNumberGenerator.Create()/GetBytes() instance API rather than the
    # newer static Fill() method, so this also runs on Windows PowerShell 5.1 (.NET Framework).
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 32
    $rng.GetBytes($bytes)
    -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Set-EnvVar([string]$Name, [string]$Value) {
    $pattern = "^$Name="
    (Get-Content .env) | ForEach-Object {
        if ($_ -match $pattern) { "$Name=$Value" } else { $_ }
    } | Set-Content .env
}

$secretVars = @(
    "NEXTAUTH_SECRET", "SALT", "ENCRYPTION_KEY", "POSTGRES_PASSWORD", "CLICKHOUSE_PASSWORD",
    "REDIS_AUTH", "MINIO_ROOT_PASSWORD", "POSTGRES_APP_PASSWORD", "EVIDENCE_S3_SECRET_KEY",
    "AGENT_READER_PASSWORD", "AGENT_WRITER_PASSWORD"
)
foreach ($var in $secretVars) {
    Set-EnvVar -Name $var -Value (New-HexSecret)
}

# The region and chat model (a Bedrock cross-region inference profile) this project was
# built and tested against.
Set-EnvVar -Name "AWS_REGION" -Value "eu-west-2"
Set-EnvVar -Name "BEDROCK_CHAT_MODEL_ID" -Value "global.openai.gpt-5.6-terra"

Write-Host "Wrote .env with every generated secret filled in."
Write-Host ""
Write-Host "Now add your own AWS credentials to .env:"
Write-Host "  AWS_ACCESS_KEY_ID="
Write-Host "  AWS_SECRET_ACCESS_KEY="
Write-Host "  AWS_SESSION_TOKEN=   (only needed for temporary STS credentials -- leave blank otherwise)"
Write-Host ""
Write-Host "Make sure this account has access to BEDROCK_CHAT_MODEL_ID and BEDROCK_EMBEDDING_MODEL_ID"
Write-Host "in the Bedrock console for AWS_REGION before starting the stack."
