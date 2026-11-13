"""
Command-Line Interface (CLI) for the pipeline.

Usage:
    python -m solution.cli ingest <drop_directory> [--db <db_path>]
    python -m solution.cli report --out <output_directory> [--db <db_path>]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from solution.pipeline import Pipeline


def build_parser() -> argparse.ArgumentParser:
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--db",
        type=str,
        default=argparse.SUPPRESS,
        help="Path to SQLite database file (default: ledger.db)",
    )

    parser = argparse.ArgumentParser(
        prog="au-pipeline",
        description="Agentic Universe - Interaction Ledger & Reporting Pipeline",
        parents=[parent_parser],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest command
    ingest_parser = subparsers.add_parser(
        "ingest",
        parents=[parent_parser],
        help="Ingest a data drop directory",
    )
    ingest_parser.add_argument(
        "drop_dir",
        type=str,
        help="Path to drop directory (e.g. data/drop_1)",
    )

    # Report command
    report_parser = subparsers.add_parser(
        "report",
        parents=[parent_parser],
        help="Generate reporting CSVs and JSON",
    )
    report_parser.add_argument(
        "--out",
        type=str,
        default="reports",
        help="Output directory for reports (default: reports/)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "db"):
        args.db = None

    start_time = time.perf_counter()
    pipeline = Pipeline(db_path=args.db)

    if args.command == "ingest":
        drop_path = Path(args.drop_dir)
        print(f"Starting ingestion from: {drop_path}...")
        res = pipeline.ingest_drop(drop_path)
        elapsed = time.perf_counter() - start_time
        print(
            f"Ingestion completed in {elapsed:.2f}s "
            f"({res['leads_rows']} lead rows, {res['events_lines']} event lines)."
        )
        return 0

    elif args.command == "report":
        out_path = Path(args.out)
        print(f"Generating reports into: {out_path}...")
        pipeline.generate_reports(out_path)
        elapsed = time.perf_counter() - start_time
        print(f"Reports successfully written to {out_path} in {elapsed:.2f}s.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
