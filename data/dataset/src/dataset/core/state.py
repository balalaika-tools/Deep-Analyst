"""Mutable locale-selection state for the build in progress.

Other modules read ``ACTIVE_LOCALE``, ``DATASET_VERSION``, and
``SOURCE_VERSIONS`` through this module object (``state.NAME``) rather than
importing the names directly, since `_activate_locale` rebinds them here for
the duration of a build.
"""

from dataset.core.constants import DATASET_VERSIONS, DEFAULT_LOCALE, SUPPORTED_LOCALES
from dataset.core.util import _require

DATASET_VERSION = DATASET_VERSIONS[DEFAULT_LOCALE]


ACTIVE_LOCALE = DEFAULT_LOCALE


def _source_versions(locale: str) -> dict[str, str]:
    return {
        source: f"{source}@1-{locale}" for source in ("cdr", "extraction", "email", "bank", "docs")
    }


SOURCE_VERSIONS = _source_versions(DEFAULT_LOCALE)


def _activate_locale(locale: str) -> None:
    """Select release metadata and translated literals for one build."""
    global ACTIVE_LOCALE, DATASET_VERSION, SOURCE_VERSIONS
    _require(locale in SUPPORTED_LOCALES, f"unsupported locale: {locale}")
    ACTIVE_LOCALE = locale
    DATASET_VERSION = DATASET_VERSIONS[locale]
    SOURCE_VERSIONS = _source_versions(locale)


def _tr(greek: str, english: str) -> str:
    """Return a meaning-preserving source literal for the active locale."""
    return english if ACTIVE_LOCALE == "en" else greek
