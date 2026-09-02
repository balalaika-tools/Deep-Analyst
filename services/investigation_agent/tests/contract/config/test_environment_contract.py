import re
from pathlib import Path

import yaml
from investigation_agent.config.secrets import InitializerSecrets, ServingSecrets
from investigation_agent.config.settings import POLICY_FIELDS, Settings

REPO_ROOT = Path(__file__).resolve().parents[5]
SERVICE_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ENV = SERVICE_ROOT / ".env.example"
ROOT_ENV = REPO_ROOT / ".env.example"
POLICY_YAML = REPO_ROOT / "config" / "investigation-agent" / "local.yaml"
SDK_CREDENTIAL_CHAIN = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}


def _documented_variables(path: Path) -> set[str]:
    return {
        match.group(1)
        for line in path.read_text().splitlines()
        if (match := re.match(r"^#?\s*([A-Z][A-Z0-9_]+)=", line))
    }


def _aliases(model: type[Settings] | type[ServingSecrets] | type[InitializerSecrets]) -> set[str]:
    return {str(field.alias) for field in model.model_fields.values()}


def test_yaml_keys_settings_fields_and_service_example_are_synchronized() -> None:
    yaml_values = yaml.safe_load(POLICY_YAML.read_text())
    assert isinstance(yaml_values, dict)
    assert set(yaml_values) == POLICY_FIELDS
    assert yaml_values["capture_ai_content"] is False

    expected = (
        _aliases(Settings)
        | _aliases(ServingSecrets)
        | _aliases(InitializerSecrets)
        | SDK_CREDENTIAL_CHAIN
        | {"INVESTIGATION_AGENT_CONFIG_DIR"}
    )
    assert _documented_variables(SERVICE_ENV) == expected


def test_root_example_contains_every_deployment_and_secret_input() -> None:
    documented = _documented_variables(ROOT_ENV)
    required_settings = {
        str(field.alias)
        for name, field in Settings.model_fields.items()
        if name not in POLICY_FIELDS and name != "service_instance_id"
    }
    required_secrets = _aliases(ServingSecrets) | _aliases(InitializerSecrets)

    assert required_settings | required_secrets | SDK_CREDENTIAL_CHAIN <= documented
