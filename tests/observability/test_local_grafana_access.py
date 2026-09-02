from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "compose.yaml"


def test_anonymous_explore_access_is_restricted_to_host_loopback() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    lgtm = compose["services"]["lgtm"]
    environment = lgtm["environment"]

    assert environment["GF_AUTH_ANONYMOUS_ENABLED"] == "true"
    assert environment["GF_AUTH_ANONYMOUS_ORG_ROLE"] == "Editor"
    assert environment["GF_AUTH_DISABLE_LOGIN_FORM"] == "true"
    assert lgtm["ports"]
    assert all(port.startswith("127.0.0.1:") for port in lgtm["ports"])
