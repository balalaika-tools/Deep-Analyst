"""Process entry points that validate configuration before any external constructor."""

from __future__ import annotations

import sys
from collections.abc import Callable

from investigation_agent.config.secrets import (
    InitializerSecrets,
    SecretsError,
    ServingSecrets,
    load_initializer_secrets,
    load_serving_secrets,
)
from investigation_agent.config.settings import Settings, SettingsError, load_settings

EXIT_CONFIGURATION = 2

type ServingLauncher = Callable[[Settings, ServingSecrets], int]
type InitializerLauncher = Callable[[Settings, InitializerSecrets], int]


def _serving_launcher(settings: Settings, secrets: ServingSecrets) -> int:
    from investigation_agent.bootstrap.app import run_serving

    return run_serving(settings, secrets)


def _initializer_launcher(settings: Settings, secrets: InitializerSecrets) -> int:
    from investigation_agent.bootstrap.app import run_initializer

    return run_initializer(settings, secrets)


def main(*, launcher: ServingLauncher | None = None) -> int:
    """Validate serving settings and its two DSNs, then hand off to bootstrap."""

    try:
        settings = load_settings()
        secrets = load_serving_secrets()
    except (SettingsError, SecretsError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIGURATION
    return (launcher or _serving_launcher)(settings, secrets)


def initializer_main(*, launcher: InitializerLauncher | None = None) -> int:
    """Validate initializer settings and owner-only secrets, then hand off to bootstrap."""

    try:
        settings = load_settings()
        secrets = load_initializer_secrets()
    except (SettingsError, SecretsError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIGURATION
    return (launcher or _initializer_launcher)(settings, secrets)


if __name__ == "__main__":
    raise SystemExit(main())
