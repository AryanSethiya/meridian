"""Tests for stretch: all-60 eval reporting, pricing/cost, latency fields."""

from __future__ import annotations

import json
from pathlib import Path

from meridian_claims.eval import EXPECTED_LOADS, run_eval_all, _percentile
from meridian_claims.pricing import ModelPricing, load_pricing
from meridian_claims.pipeline import process_email

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def test_percentile_basic():
    assert _percentile([], 50) is None
    assert _percentile([10], 50) == 10.0
    assert _percentile([10, 20, 30, 40], 50) == 25.0


def test_pricing_estimate_from_tokens():
    p = ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0, source="test")
    est = p.estimate(1_000_000, 1_000_000)
    assert est["estimated_input_cost_usd"] == 3.0
    assert est["estimated_output_cost_usd"] == 15.0
    assert est["estimated_total_cost_usd"] == 18.0
    assert "Estimated" in est["note"]


def test_pricing_null_when_tokens_missing():
    p = ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0)
    est = p.estimate(None, None)
    assert est["estimated_total_cost_usd"] is None
    assert est["input_tokens"] is None


def test_load_pricing_env_override(monkeypatch):
    monkeypatch.setenv("MERIDIAN_PRICE_INPUT_PER_MTOK", "1.5")
    monkeypatch.setenv("MERIDIAN_PRICE_OUTPUT_PER_MTOK", "7.5")
    p = load_pricing()
    assert p.input_per_mtok == 1.5
    assert p.output_per_mtok == 7.5
    assert p.source == "env"


def test_decision_record_has_latency_fields(tmp_path: Path):
    packet = process_email(
        DATA / "emails" / "035.eml",
        output_dir=tmp_path,
        use_llm=False,
        use_vision=False,
    )
    d = packet.decision
    assert d["processing_duration_ms"] >= 0
    assert d["model_called"] is False
    assert d["estimated_total_cost_usd"] is None
    assert d["model_latency_ms"] is None


def test_eval_all_dry_run_writes_reports(tmp_path: Path):
    """Small deterministic check: harness generates reports without API key."""
    out_packets = tmp_path / "packets"
    reports = tmp_path / "reports"
    # Use real data dir but write packets/reports under tmp
    code = run_eval_all(
        data_dir=DATA,
        emails_dir=DATA / "emails",
        output_dir=out_packets,
        report_dir=reports,
        dry_run=True,
    )
    assert code == 0
    payload = json.loads((reports / "eval_all.json").read_text(encoding="utf-8"))
    summary = payload["summary"]
    n_emails = len(list((DATA / "emails").glob("*.eml")))
    assert n_emails >= 60
    assert summary["total_emails"] == n_emails
    assert summary["processed"] + summary["failed"] == n_emails or summary["processed"] == n_emails
    assert summary["mode"] == "dry_run"
    assert summary["model_calls"] == 0
    assert "known_labeled_results" in summary
    labeled = summary["known_labeled_results"]
    assert labeled["known_labeled_cases"] == len(EXPECTED_LOADS)
    assert labeled["correct_load"] == len(EXPECTED_LOADS)
    md = (reports / "eval_all.md").read_text(encoding="utf-8")
    assert "KNOWN LABELED RESULTS" in md
    assert "UNLABELED / BEHAVIORAL RESULTS" in md
    assert "EMAIL EVALUATION" in md
    # At least some packets written
    assert any(out_packets.glob("*.json"))


def test_eval_all_handles_missing_optional_fields_in_summary_shape(tmp_path: Path):
    """Report schema always includes latency/cost keys even when dry-run."""
    reports = tmp_path / "reports"
    run_eval_all(
        data_dir=DATA,
        emails_dir=DATA / "emails",
        output_dir=tmp_path / "packets",
        report_dir=reports,
        dry_run=True,
    )
    summary = json.loads((reports / "eval_all.json").read_text())["summary"]
    for key in (
        "avg_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "estimated_total_model_cost_usd",
        "emails_requiring_no_model_call",
        "pipeline_errors",
    ):
        assert key in summary
