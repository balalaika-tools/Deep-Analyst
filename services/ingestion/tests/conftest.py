from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENGLISH_EDITION = REPO_ROOT / "data" / "dataset" / "editions" / "en" / "data"


@pytest.fixture(scope="session")
def edition_dir() -> Path:
    return ENGLISH_EDITION
