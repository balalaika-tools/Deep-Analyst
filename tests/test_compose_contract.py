from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "compose.yaml"
INDEX_SEED_PATH = REPO_ROOT / "data" / "dataset" / "indexes" / "en"


def test_evidence_seed_preserves_ingestion_receipt_before_ingestion() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    seed = compose["services"]["evidence-seed"]
    ingestion = compose["services"]["ingestion"]
    command = "\n".join(seed["command"])

    assert INDEX_SEED_PATH.is_dir()
    assert "./data/dataset/indexes/en:/seed/indexes:ro" in seed["volumes"]
    assert "mc mirror --overwrite --exclude '.gitkeep' /seed/indexes/" in command
    assert "mc mirror --overwrite --remove --exclude '.gitkeep' /seed/indexes/" not in command
    assert '"local/$${EVIDENCE_S3_BUCKET}/indexes/$${DATASET_EDITION}/"' in command
    assert ingestion["depends_on"]["evidence-seed"]["condition"] == (
        "service_completed_successfully"
    )


def test_agent_waits_for_successful_ingestion_completion() -> None:
    services = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]

    assert services["agent-db-init"]["depends_on"]["ingestion"] == {
        "condition": "service_completed_successfully"
    }
    assert services["investigation-agent"]["depends_on"]["ingestion"] == {
        "condition": "service_completed_successfully"
    }
