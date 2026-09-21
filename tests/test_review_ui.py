"""Smoke tests for the local review UI server (no browser framework)."""

from __future__ import annotations

import json
from pathlib import Path

from meridian_claims.review_server import (
    _list_emails,
    _list_packets,
    _load_packet,
    _process_email_id,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def test_list_packets_discovers_action_json(tmp_path: Path):
    (tmp_path / "035.json").write_text(
        json.dumps(
            {
                "email_id": "035",
                "subject": "Damage",
                "needs_human": True,
                "llm_status": "ok",
                "classification": {"claim_type": "damage", "confidence": 0.9},
                "identifiers": {"load_number": "MF-10487"},
                "resolution": {
                    "status": "resolved",
                    "load": {"LoadNumber": "MF-10487"},
                },
                "usage": {
                    "input_tokens": 2600,
                    "output_tokens": 771,
                    "latency_ms": 18042,
                    "estimated_total_cost_usd": 0.019365,
                    "model": "claude-sonnet-4-5",
                },
                "decision": {
                    "processing_duration_ms": 18685,
                    "model_called": True,
                    "model_input_tokens": 2600,
                    "model_output_tokens": 771,
                    "model_latency_ms": 18042,
                    "estimated_total_cost_usd": 0.019365,
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "035.decision.json").write_text("{}", encoding="utf-8")
    (tmp_path / "eval_all.json").write_text("{}", encoding="utf-8")
    (tmp_path / "bad.json").write_text("{not-json", encoding="utf-8")

    listing = _list_packets(tmp_path)
    ids = {p["email_id"] for p in listing["packets"]}
    assert "035" in ids
    assert "eval_all" not in ids
    bad = next(p for p in listing["packets"] if p["email_id"] == "bad")
    assert bad.get("error") == "malformed_json"

    row = next(p for p in listing["packets"] if p["email_id"] == "035")
    assert row["processing_duration_ms"] == 18685
    assert row["model_latency_ms"] == 18042
    assert row["input_tokens"] == 2600
    assert row["output_tokens"] == 771
    assert row["estimated_total_cost_usd"] == 0.019365
    assert row["model_called"] is True


def test_load_packet_rejects_traversal(tmp_path: Path):
    assert _load_packet(tmp_path, "../etc/passwd") is None
    assert _load_packet(tmp_path, "missing") is None


def test_list_emails_marks_existing_packets(tmp_path: Path):
    emails = tmp_path / "emails"
    emails.mkdir()
    (emails / "035.eml").write_text("From: a@b.example\nSubject: x\n\nHi\n", encoding="utf-8")
    (emails / "036.eml").write_text("From: a@b.example\nSubject: y\n\nHi\n", encoding="utf-8")
    out = tmp_path / "output"
    out.mkdir()
    (out / "035.json").write_text(
        json.dumps({"email_id": "035"}), encoding="utf-8"
    )

    listing = _list_emails(emails, out)
    by_id = {e["email_id"]: e for e in listing["emails"]}
    assert by_id["035"]["has_packet"] is True
    assert by_id["036"]["has_packet"] is False


def test_process_email_id_dry_run_writes_packet(tmp_path: Path):
    out = tmp_path / "output"
    result = _process_email_id(
        "035",
        emails_dir=DATA / "emails",
        data_dir=DATA,
        output_dir=out,
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["email_id"] == "035"
    assert result["mode"] == "dry_run"
    assert (out / "035.json").is_file()
    assert result["processing_duration_ms"] is not None


def test_process_email_id_rejects_bad_id(tmp_path: Path):
    result = _process_email_id(
        "../etc/passwd",
        emails_dir=DATA / "emails",
        data_dir=DATA,
        output_dir=tmp_path,
        dry_run=True,
    )
    assert result["ok"] is False
    assert result["http_status"] == 400


def test_api_config_never_returns_secret(monkeypatch):
    from meridian_claims.review_server import _api_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-should-not-leak")
    cfg = _api_config()
    assert cfg["has_env_api_key"] is True
    assert cfg["default_mode"] == "live"
    blob = json.dumps(cfg)
    assert "sk-ant-secret" not in blob


def test_temporary_api_key_overrides_env(monkeypatch):
    from meridian_claims.agent import resolve_api_key, temporary_api_key

    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert resolve_api_key() == "env-key"
    with temporary_api_key("ui-key"):
        assert resolve_api_key() == "ui-key"
    assert resolve_api_key() == "env-key"
