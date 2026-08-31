from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from autocomplete.api import configure_default_engine
from autocomplete.cli import run_cli
from autocomplete.models import SentenceRecord
from autocomplete.search_engine import SearchEngine

if TYPE_CHECKING:
    from autocomplete.index import SearchIndex


def _load_snapshot(
    snapshot_path: Path,
) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    from autocomplete.snapshot_loader import load_snapshot

    return load_snapshot(snapshot_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local autocomplete search.")
    parser.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="Path to the local autocomplete snapshot.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    snapshot_path = arguments.snapshot

    try:
        records_by_id, index = _load_snapshot(snapshot_path)
    except Exception as error:
        print(
            f"Snapshot loading failed for {snapshot_path}: {error}",
            file=sys.stderr,
        )
        return 1

    engine = SearchEngine(records_by_id, index)
    configure_default_engine(engine)
    run_cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
