"""CLI entrypoints: process one email, or eval all claims@ emails."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from meridian_claims.eval import run_eval
from meridian_claims.pipeline import run_process_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meridian_claims",
        description=(
            "Meridian Freight claims triage: email → FreightPro lookup → "
            "POD compare → human-review action packet."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    process_p = sub.add_parser("process", help="Process one .eml into an action packet")
    process_p.add_argument("eml", type=Path, help="Path to an .eml file")
    process_p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Path to data/ (default: ./data next to the package)",
    )
    process_p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write JSON/MD packets (default: ./output)",
    )
    process_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Anthropic calls; deterministic lookup + heuristic draft only",
    )

    eval_p = sub.add_parser("eval", help="Run all claims@ sample emails and print a table")
    eval_p.add_argument("--data-dir", type=Path, default=None)
    eval_p.add_argument("--emails-dir", type=Path, default=None)
    eval_p.add_argument("--output-dir", type=Path, default=None)
    eval_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Anthropic calls for offline scoring of load resolution",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "process":
        if not args.eml.exists():
            print(f"File not found: {args.eml}", file=sys.stderr)
            return 1
        return run_process_cli(
            args.eml,
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )

    if args.command == "eval":
        return run_eval(
            data_dir=args.data_dir,
            emails_dir=args.emails_dir,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )

    parser.print_help()
    return 1
