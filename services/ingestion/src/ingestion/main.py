"""Process entry point: load settings, then hand off to the bootstrap runtime."""

from __future__ import annotations

import asyncio
import sys

from ingestion.bootstrap.runtime import run
from ingestion.config.settings import SettingsError, load_settings

EXIT_CONFIGURATION = 2


def main() -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIGURATION
    return asyncio.run(run(settings))


if __name__ == "__main__":
    raise SystemExit(main())
