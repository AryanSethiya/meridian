"""CLI entrypoints: process one email, eval, or serve the coordinator review UI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from meridian_claims.eval import run_eval, run_eval_all
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

    eval_p = sub.add_parser(
        "eval",
        help=(
            "Evaluation harness. Default: known-sample claims@ regression. "
            "Use --all for behavioral run over all 60 emails."
        ),
    )
    eval_p.add_argument("--data-dir", type=Path, default=None)
    eval_p.add_argument("--emails-dir", type=Path, default=None)
    eval_p.add_argument("--output-dir", type=Path, default=None)
    eval_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Anthropic calls (no API key required)",
    )
    eval_p.add_argument(
        "--all",
        action="store_true",
        help=(
            "Process all 60 sample .eml files through the existing pipeline and "
            "write output/eval_all.json + output/eval_all.md. "
            "Without --dry-run this makes Anthropic calls when the model path runs."
        ),
    )

    review_p = sub.add_parser(
        "review",
        help=(
            "Serve the minimal coordinator review UI (local only). "
            "Loads ActionPacket JSON from output/. Does not send email or call models."
        ),
    )
    review_p.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Local HTTP port (default 8765)",
    )
    review_p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory of ActionPacket JSON files (default: ./output)",
    )
    review_p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default 127.0.0.1)",
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
        if args.all:
            return run_eval_all(
                data_dir=args.data_dir,
                emails_dir=args.emails_dir,
                output_dir=args.output_dir,
                dry_run=args.dry_run,
            )
        return run_eval(
            data_dir=args.data_dir,
            emails_dir=args.emails_dir,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )

    if args.command == "review":
        from meridian_claims.review_server import serve_review_ui

        return serve_review_ui(
            host=args.host,
            port=args.port,
            output_dir=args.output_dir,
        )

    parser.print_help()
    return 1
