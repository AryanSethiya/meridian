"""Eval harness: known-sample claims@ regression + optional all-60 behavioral run."""

from __future__ import annotations

import json
import statistics
import time
import traceback
from pathlib import Path
from typing import Any

from meridian_claims.agent import MissingAPIKeyError
from meridian_claims.email_parser import parse_eml
from meridian_claims.pipeline import process_email

# Spot-check expectations from the sample snapshot (2026-08-26).
# This is a known-sample regression map — not a production accuracy benchmark.
EXPECTED_LOADS = {
    "035": "MF-10487",
    "036": "MF-10005",
    "037": "MF-10055",
    "038": "MF-10088",
    "039": "MF-10027",
    "040": "MF-10031",
    "041": "MF-10032",
    "042": "MF-10034",
}


def find_claims_emails(emails_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(emails_dir.glob("*.eml")):
        parsed = parse_eml(path, attachment_dir=None)
        if "claims@" in parsed.to_addr.lower():
            paths.append(path)
    return paths


def find_all_emails(emails_dir: Path) -> list[Path]:
    return sorted(emails_dir.glob("*.eml"))


def _inbox_bucket(to_addr: str) -> str:
    t = (to_addr or "").lower()
    for box in ("claims@", "quotes@", "tracking@", "ops@"):
        if box in t:
            return box.rstrip("@")
    return "other"


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return float(ordered[f])
    return float(ordered[f] + (ordered[c] - ordered[f]) * (k - f))


def run_eval(
    *,
    data_dir: Path | None = None,
    emails_dir: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Known-sample claims@ load-resolution regression (EXPECTED_LOADS fixtures)."""
    repo = Path(__file__).resolve().parent.parent
    data = Path(data_dir) if data_dir else repo / "data"
    emails = Path(emails_dir) if emails_dir else data / "emails"
    out = Path(output_dir) if output_dir else repo / "output" / "eval"

    claims = find_claims_emails(emails)
    if not claims:
        print(f"No claims@ emails found under {emails}")
        return 1

    print(
        f"{'id':<5} {'type':<10} {'resolve':<12} {'method':<12} "
        f"{'load':<10} {'expect':<10} {'ok':<4} {'pod':<12} human"
    )
    print("-" * 95)

    correct = 0
    total_checked = 0
    for path in claims:
        packet = process_email(
            path,
            data_dir=data,
            output_dir=out,
            use_llm=not dry_run,
            use_vision=not dry_run,
        )
        got = (packet.resolution.load or {}).get("LoadNumber", "—")
        expect = EXPECTED_LOADS.get(packet.email_id, "?")
        ok = "—"
        if packet.email_id in EXPECTED_LOADS:
            total_checked += 1
            match = got == expect
            ok = "Y" if match else "N"
            if match:
                correct += 1
        print(
            f"{packet.email_id:<5} "
            f"{packet.classification.claim_type:<10} "
            f"{packet.resolution.status:<12} "
            f"{(packet.resolution.method or '—'):<12} "
            f"{got:<10} "
            f"{expect:<10} "
            f"{ok:<4} "
            f"{packet.pod.mode:<12} "
            f"{packet.needs_human}"
        )

    print("-" * 95)
    if total_checked:
        print(
            f"Load resolution (known-sample regression): {correct}/{total_checked} "
            "(not a production accuracy estimate)"
        )
    print(f"Packets written under {out}")
    if total_checked and correct < total_checked:
        return 1
    return 0


def run_eval_all(
    *,
    data_dir: Path | None = None,
    emails_dir: Path | None = None,
    output_dir: Path | None = None,
    report_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """
    Run the existing pipeline over all .eml files under data/emails/.

    Continues on per-email failures. Writes machine-readable + markdown reports.
    Does not claim accuracy for unlabeled emails.
    """
    repo = Path(__file__).resolve().parent.parent
    data = Path(data_dir) if data_dir else repo / "data"
    emails = Path(emails_dir) if emails_dir else data / "emails"
    out = Path(output_dir) if output_dir else repo / "output" / "eval_all"
    if report_dir is not None:
        report_root = Path(report_dir)
    elif output_dir is not None:
        # Keep reports next to custom packet output (e.g. CI temp dirs).
        report_root = Path(output_dir)
    else:
        report_root = repo / "output"
    report_root.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    paths = find_all_emails(emails)
    if not paths:
        print(f"No .eml files found under {emails}")
        return 1

    mode = "dry_run" if dry_run else "live"
    if not dry_run:
        print(
            "LIVE all-60 evaluation: Anthropic model calls will be made when the "
            "pipeline reaches the model boundary (requires ANTHROPIC_API_KEY)."
        )

    wall_t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    model_latencies: list[float] = []
    cost_total = 0.0
    cost_n = 0

    counts = {
        "total": len(paths),
        "processed": 0,
        "escalated": 0,
        "failed": 0,
        "non_claims_inbox": 0,
        "claims_inbox": 0,
        "model_calls": 0,
        "no_model_call": 0,
        "model_errors": 0,
        "pipeline_errors": 0,
        "pii_blocked": 0,
    }
    labeled = {
        "known_labeled_cases": 0,
        "correct_load": 0,
        "incorrect_load": 0,
        "unresolved": 0,
        "ambiguous": 0,
        "details": [],
    }
    by_inbox: dict[str, int] = {}
    by_resolve: dict[str, int] = {}
    by_claim_type: dict[str, int] = {}

    for path in paths:
        email_id = path.stem
        row: dict[str, Any] = {
            "email_id": email_id,
            "path": str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path),
            "status": "pending",
        }
        t0 = time.perf_counter()
        try:
            parsed_meta = parse_eml(path, attachment_dir=None)
            inbox = _inbox_bucket(parsed_meta.to_addr)
            row["inbox"] = inbox
            by_inbox[inbox] = by_inbox.get(inbox, 0) + 1
            if inbox == "claims":
                counts["claims_inbox"] += 1
            else:
                counts["non_claims_inbox"] += 1

            packet = process_email(
                path,
                data_dir=data,
                output_dir=out,
                use_llm=not dry_run,
                use_vision=not dry_run,
            )
            duration_ms = int((time.perf_counter() - t0) * 1000)
            # Prefer pipeline decision duration (also perf_counter-based).
            if packet.decision and packet.decision.get("processing_duration_ms") is not None:
                duration_ms = int(packet.decision["processing_duration_ms"])
            latencies.append(float(duration_ms))

            got_load = (packet.resolution.load or {}).get("LoadNumber")
            row.update(
                {
                    "status": "ok",
                    "claim_type": packet.classification.claim_type,
                    "resolution_status": packet.resolution.status,
                    "resolution_method": packet.resolution.method,
                    "resolved_load": got_load,
                    "pod_mode": packet.pod.mode,
                    "needs_human": packet.needs_human,
                    "llm_status": packet.llm_status,
                    "llm_error": packet.llm_error,
                    "latency_ms": duration_ms,
                    "model_called": bool(
                        packet.usage is not None and packet.llm_status == "ok"
                    ),
                    "model_name": packet.usage.model if packet.usage else None,
                    "input_tokens": packet.usage.input_tokens if packet.usage else None,
                    "output_tokens": packet.usage.output_tokens if packet.usage else None,
                    "estimated_total_cost_usd": (
                        packet.usage.estimated_total_cost_usd if packet.usage else None
                    ),
                    "model_latency_ms": packet.usage.latency_ms if packet.usage else None,
                }
            )
            counts["processed"] += 1
            if packet.needs_human:
                counts["escalated"] += 1

            by_resolve[packet.resolution.status] = (
                by_resolve.get(packet.resolution.status, 0) + 1
            )
            by_claim_type[packet.classification.claim_type] = (
                by_claim_type.get(packet.classification.claim_type, 0) + 1
            )

            if packet.llm_status == "ok" and packet.usage is not None:
                counts["model_calls"] += 1
                if packet.usage.latency_ms:
                    model_latencies.append(float(packet.usage.latency_ms))
                if packet.usage.estimated_total_cost_usd is not None:
                    cost_total += float(packet.usage.estimated_total_cost_usd)
                    cost_n += 1
            else:
                counts["no_model_call"] += 1
                if packet.llm_status == "error":
                    counts["model_errors"] += 1
                if packet.llm_status == "pii_blocked":
                    counts["pii_blocked"] += 1

            if email_id in EXPECTED_LOADS:
                expect = EXPECTED_LOADS[email_id]
                labeled["known_labeled_cases"] += 1
                match = got_load == expect
                detail = {
                    "email_id": email_id,
                    "expected_load": expect,
                    "resolved_load": got_load,
                    "resolution_status": packet.resolution.status,
                    "match": match,
                }
                if packet.resolution.status == "unresolved":
                    labeled["unresolved"] += 1
                    detail["result"] = "unresolved"
                elif packet.resolution.status == "ambiguous":
                    labeled["ambiguous"] += 1
                    detail["result"] = "ambiguous"
                elif match:
                    labeled["correct_load"] += 1
                    detail["result"] = "match"
                else:
                    labeled["incorrect_load"] += 1
                    detail["result"] = "mismatch"
                labeled["details"].append(detail)
                row["known_label"] = detail

        except MissingAPIKeyError as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            latencies.append(float(duration_ms))
            counts["failed"] += 1
            counts["pipeline_errors"] += 1
            row.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "error_type": "MissingAPIKeyError",
                    "latency_ms": duration_ms,
                }
            )
            print(f"[{email_id}] FAILED (missing API key) — aborting remaining live run.")
            rows.append(row)
            # Missing key on live mode is fatal for remaining emails.
            break
        except Exception as exc:  # noqa: BLE001 — continue remaining emails
            duration_ms = int((time.perf_counter() - t0) * 1000)
            latencies.append(float(duration_ms))
            counts["failed"] += 1
            counts["pipeline_errors"] += 1
            row.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "traceback": traceback.format_exc(limit=4),
                    "latency_ms": duration_ms,
                }
            )
            print(f"[{email_id}] FAILED: {exc}")

        rows.append(row)
        if row.get("status") == "ok":
            print(
                f"[{email_id}] {row.get('inbox')} {row.get('claim_type')} "
                f"resolve={row.get('resolution_status')} load={row.get('resolved_load') or '—'} "
                f"llm={row.get('llm_status')} {duration_ms}ms"
            )

    wall_ms = int((time.perf_counter() - wall_t0) * 1000)
    summary = {
        "title": "ALL-SAMPLE EMAIL EVALUATION",
        "mode": mode,
        "note": (
            "Behavioral run over all sample .eml files via the existing pipeline. "
            "Known labeled load matches are a fixture regression check only — "
            "not a production accuracy estimate. Unlabeled emails have no ground truth."
        ),
        "total_emails": counts["total"],
        "processed": counts["processed"],
        "escalated": counts["escalated"],
        "failed": counts["failed"],
        "claims_inbox": counts["claims_inbox"],
        "non_claims_inbox": counts["non_claims_inbox"],
        "model_calls": counts["model_calls"],
        "emails_requiring_no_model_call": counts["no_model_call"],
        "model_errors": counts["model_errors"],
        "pii_blocked": counts["pii_blocked"],
        "pipeline_errors": counts["pipeline_errors"],
        "processing_time_ms": wall_ms,
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else None,
        "p50_latency_ms": (
            round(_percentile(latencies, 50), 2) if _percentile(latencies, 50) is not None else None
        ),
        "p95_latency_ms": (
            round(_percentile(latencies, 95), 2) if _percentile(latencies, 95) is not None else None
        ),
        "avg_model_latency_ms": (
            round(statistics.mean(model_latencies), 2) if model_latencies else None
        ),
        "estimated_total_model_cost_usd": round(cost_total, 8) if cost_n else 0.0,
        "estimated_avg_model_cost_per_email_usd": (
            round(cost_total / counts["total"], 8) if counts["total"] else None
        ),
        "estimated_avg_model_cost_per_model_call_usd": (
            round(cost_total / cost_n, 8) if cost_n else None
        ),
        "by_inbox": by_inbox,
        "by_resolution_status": by_resolve,
        "by_claim_type": by_claim_type,
        "known_labeled_results": labeled,
        "cost_note": (
            "Estimated model cost from configured USD/MTok rates and Anthropic "
            "usage when present — not an invoice."
        ),
    }

    payload = {"summary": summary, "emails": rows}
    json_path = report_root / "eval_all.json"
    md_path = report_root / "eval_all.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_render_eval_all_md(summary, labeled), encoding="utf-8")

    print("-" * 72)
    print(f"ALL-SAMPLE EMAIL EVALUATION ({mode})")
    print(f"  total={summary['total_emails']} processed={summary['processed']} "
          f"failed={summary['failed']} escalated={summary['escalated']}")
    print(f"  model_calls={summary['model_calls']} "
          f"no_model={summary['emails_requiring_no_model_call']} "
          f"model_errors={summary['model_errors']}")
    print(f"  avg_latency_ms={summary['avg_latency_ms']} "
          f"p50={summary['p50_latency_ms']} p95={summary['p95_latency_ms']}")
    print(f"  estimated_total_model_cost_usd={summary['estimated_total_model_cost_usd']}")
    print(
        f"  known-labeled correct={labeled['correct_load']}/"
        f"{labeled['known_labeled_cases']} (regression only)"
    )
    print(f"Reports: {json_path}  {md_path}")
    print(f"Packets: {out}")

    # Exit non-zero only if every email failed or live key missing mid-run with zero success.
    if counts["processed"] == 0:
        return 1
    return 0


def _render_eval_all_md(summary: dict[str, Any], labeled: dict[str, Any]) -> str:
    def fmt(v: Any) -> str:
        if v is None:
            return "—"
        if isinstance(v, float):
            return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"
        return str(v)

    lines = [
        "# ALL-SAMPLE EMAIL EVALUATION",
        "",
        f"**Mode:** `{summary['mode']}`  ",
        f"**Note:** {summary['note']}",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        f"| Total emails | {summary['total_emails']} |",
        f"| Processed | {summary['processed']} |",
        f"| Escalated | {summary['escalated']} |",
        f"| Failed | {summary['failed']} |",
        f"| Claims inbox | {summary['claims_inbox']} |",
        f"| Non-claims inbox (skipped label; still processed) | {summary['non_claims_inbox']} |",
        f"| Known labeled cases | {labeled['known_labeled_cases']} |",
        f"| Correct load associations | {labeled['correct_load']} |",
        f"| Incorrect load associations | {labeled['incorrect_load']} |",
        f"| Ambiguous (labeled) | {labeled['ambiguous']} |",
        f"| Unresolved (labeled) | {labeled['unresolved']} |",
        f"| Model calls | {summary['model_calls']} |",
        f"| Emails requiring no model call | {summary['emails_requiring_no_model_call']} |",
        f"| Model errors | {summary['model_errors']} |",
        f"| PII blocked | {summary['pii_blocked']} |",
        f"| Pipeline errors | {summary['pipeline_errors']} |",
        f"| Processing time (ms) | {summary['processing_time_ms']} |",
        f"| Avg latency (ms) | {fmt(summary['avg_latency_ms'])} |",
        f"| P50 latency (ms) | {fmt(summary['p50_latency_ms'])} |",
        f"| P95 latency (ms) | {fmt(summary['p95_latency_ms'])} |",
        f"| Estimated total model cost (USD) | {fmt(summary['estimated_total_model_cost_usd'])} |",
        f"| Estimated avg model cost / email (USD) | {fmt(summary['estimated_avg_model_cost_per_email_usd'])} |",
        "",
        "## KNOWN LABELED RESULTS",
        "",
        "Fixture regression only (EXPECTED_LOADS). Not a production accuracy estimate.",
        "",
    ]
    if labeled["details"]:
        lines.extend(
            [
                "| id | expected | resolved | status | result |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for d in labeled["details"]:
            lines.append(
                f"| {d['email_id']} | {d['expected_load']} | "
                f"{d.get('resolved_load') or '—'} | {d['resolution_status']} | "
                f"{d['result']} |"
            )
    else:
        lines.append("_No labeled fixtures in this run._")

    lines.extend(
        [
            "",
            "## UNLABELED / BEHAVIORAL RESULTS",
            "",
            f"All {summary['total_emails']} sample emails were processed through the existing pipeline. Emails outside "
            "EXPECTED_LOADS have **no ground-truth load label** in this harness.",
            "",
            "### By inbox",
            "",
        ]
    )
    for k, v in sorted((summary.get("by_inbox") or {}).items()):
        lines.append(f"- `{k}`: {v}")
    lines.extend(["", "### By resolution status", ""])
    for k, v in sorted((summary.get("by_resolution_status") or {}).items()):
        lines.append(f"- `{k}`: {v}")
    lines.extend(["", "### By claim type (heuristic/LLM classification)", ""])
    for k, v in sorted((summary.get("by_claim_type") or {}).items()):
        lines.append(f"- `{k}`: {v}")
    lines.extend(
        [
            "",
            f"_{summary.get('cost_note')}_",
            "",
        ]
    )
    return "\n".join(lines)
