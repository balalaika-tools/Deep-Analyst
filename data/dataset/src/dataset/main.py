"""CLI entry point and public API for the TRG synthetic dataset generator.

The packaged ``make-dataset`` console script and the contract tests
(``from dataset import main``) both resolve through this module.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from dataset.core.constants import (
    CANONICAL_SEED,
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    SYNTHETIC_NOTICE,
)
from dataset.core.util import _require
from dataset.pipeline import generate_dataset, verify_manifest

__all__ = ["CANONICAL_SEED", "generate_dataset", "main", "verify_manifest"]


def print_previews(root: Path) -> None:
    for name in ["cdr", "extraction", "email", "account", "transaction", "document"]:
        preview = json.loads((root / f"expected/previews/{name}.json").read_text(encoding="utf-8"))
        print(f"\n=== {name} ===")
        print(json.dumps(preview, ensure_ascii=False, sort_keys=True, indent=2))


def _default_output(dataset_root: Path, seed: int, locale: str) -> Path:
    if seed == CANONICAL_SEED:
        return dataset_root / "editions" / locale / "data"
    return dataset_root / "variants" / locale / str(seed) / "data"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the deterministic TRG synthetic dataset")
    parser.add_argument(
        "--seed", type=int, default=CANONICAL_SEED, help="RNG seed (canonical: 20260305)"
    )
    parser.add_argument(
        "--locale",
        choices=SUPPORTED_LOCALES,
        default=DEFAULT_LOCALE,
        help="dataset language (default: en)",
    )
    parser.add_argument(
        "--output", type=Path, help="output directory (default: editions/<locale>/data)"
    )
    parser.add_argument(
        "--check", action="store_true", help="verify an existing build without rewriting it"
    )
    parser.add_argument(
        "--preview", action="store_true", help="print one raw/canonical preview per feed"
    )
    args = parser.parse_args(argv)

    # src/dataset/main.py -> src/dataset -> src -> dataset (project root)
    dataset_root = Path(__file__).resolve().parents[2]
    output = (
        args.output
        if args.output is not None
        else _default_output(dataset_root, args.seed, args.locale)
    )
    print(SYNTHETIC_NOTICE.strip())
    try:
        if args.check:
            manifest = verify_manifest(output.resolve(), expected_seed=args.seed)
            _require(manifest["language"] == args.locale, "manifest locale mismatch")
            print(
                "Verified {} source records in {}".format(
                    manifest["source_totals"]["all_source_records"], output.resolve()
                )
            )
        else:
            manifest = generate_dataset(output, args.seed, args.locale)
            print(
                "Built {} source records in {}".format(
                    manifest["source_totals"]["all_source_records"], output.resolve()
                )
            )
        if args.preview:
            print_previews(output.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
