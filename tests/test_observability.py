"""Tests for structured decision/audit observability (no PII in decision records)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian_claims.freightpro import FreightProDB
from meridian_claims.observability import build_decision_record
from meridian_claims.pipeline import process_email
from meridian_claims.models import Identifiers, PodResult, Resolution

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def test_decision_record_on_035_has_required_fields(tmp_path: Path):
    packet = process_email(
        DATA / "emails" / "035.eml",
        output_dir=tmp_path,
        use_llm=False,
        use_vision=False,
    )
    d = packet.decision
    assert d, "decision record missing"
    assert d["email_id"] == "035"
    assert d["intent"] == "damage"
    assert d["extracted_identifiers"]["load_number"] == "MF-10487"
    assert d["extracted_identifiers"]["po"] == "PO-8300487"
    assert d["extracted_identifiers"]["bol"] == "BOL243910"
    assert d["resolution_method"] == "LoadNumber"
    assert d["resolved_load"] == "MF-10487"
    assert d["resolution_status"] == "resolved"
    assert d["resolution_confidence"] >= 0.9
    assert d["attachment"]["count"] >= 1
    assert d["attachment"]["has_pdf"] is True
    assert d["pod_extraction_method"] == "text"
    assert d["pod_status"] == "readable"
    assert isinstance(d["evidence_used"], list)
    assert d["pii_redaction_status"] == "skipped_dry_run"
    assert d["model_call_status"] == "dry_run"
    assert d["model_output_validation_status"] == "skipped_dry_run"
    assert d["escalation_decision"] == "escalate"
    assert d["draft_generated"] is True
    assert d["processing_duration_ms"] >= 0
    # sidecar file
    assert (tmp_path / "035.decision.json").exists()


def test_decision_record_036_blank_pod(tmp_path: Path):
    packet = process_email(
        DATA / "emails" / "036.eml",
        output_dir=tmp_path,
        use_llm=False,
        use_vision=False,
    )
    d = packet.decision
    assert d["intent"] == "shortage"
    assert d["resolved_load"] == "MF-10005"
    assert d["pod_extraction_method"] == "blank_page"
    assert d["pod_status"] == "unreadable"
    assert d["escalation_decision"] == "escalate"


def test_decision_record_041_po_method(tmp_path: Path):
    packet = process_email(
        DATA / "emails" / "041.eml",
        output_dir=tmp_path,
        use_llm=False,
        use_vision=False,
    )
    d = packet.decision
    assert d["resolution_method"] == "PONumber"
    assert d["resolved_load"] == "MF-10032"
    assert d["extracted_identifiers"]["po"] == "PO-4500032"
    assert d["attachment"]["count"] == 0


def test_decision_record_contains_no_driver_pii(tmp_path: Path):
    db = FreightProDB.load(DATA)
    driver_row = next(
        r for r in db.loads if (r.get("DriverName") or "").strip() and (r.get("DriverPhone") or "").strip()
    )
    packet = process_email(
        DATA / "emails" / "035.eml",
        output_dir=tmp_path,
        use_llm=False,
        use_vision=False,
    )
    blob = json.dumps(packet.decision)
    assert driver_row["DriverName"] not in blob
    assert driver_row["DriverPhone"] not in blob
    # No email body / POD excerpt fields
    assert "excerpt" not in packet.decision
    assert "body" not in packet.decision
    assert "alleged_facts" not in packet.decision
    assert "draft_reply" not in packet.decision


def test_build_decision_rejects_forbidden_content_keys():
    """Defense: builder must not accept body/excerpt-like pollution via evidence alone."""
    resolution = Resolution(
        status="resolved",
        method="LoadNumber",
        confidence=0.95,
        load={"LoadNumber": "MF-10487"},
        evidence=["primary_match=LoadNumber:MF-10487"],
    )
    rec = build_decision_record(
        email_id="035",
        intent="damage",
        identifiers=Identifiers(load_number="MF-10487"),
        resolution=resolution,
        pod=PodResult(mode="text", status="readable", filename="POD_MF-10487.pdf"),
        attachment_filenames=["POD_MF-10487.pdf"],
        evidence=["primary_match=LoadNumber:MF-10487"],
        discrepancy_count=2,
        pii_gate={"status": "passed"},
        llm_status="ok",
        classification_source="llm",
        needs_human=True,
        needs_human_reasons=["Marcus policy"],
        draft_reply="Thanks",
        processing_duration_ms=12,
    )
    assert rec.model_output_validation_status == "validated_ok"
    assert rec.to_dict()["resolved_load"] == "MF-10487"
