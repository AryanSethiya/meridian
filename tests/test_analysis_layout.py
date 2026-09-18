"""Tests for separated claim-analysis packet layout."""

from __future__ import annotations

from pathlib import Path

from meridian_claims.analysis import (
    known_identifier_set,
    validate_llm_against_known_facts,
)
from meridian_claims.pipeline import process_email

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def test_analysis_separates_fact_sources_035():
    packet = process_email(DATA / "emails" / "035.eml", use_llm=False, use_vision=False)
    a = packet.analysis
    assert a
    assert a["freightpro_facts"]["available"] is True
    assert a["freightpro_facts"]["load_number"] == "MF-10487"
    assert a["freightpro_facts"]["pod_received"] == "Y"
    assert a["email_facts"]["identifiers"]["load_number"] == "MF-10487"
    assert a["pod_facts"]["available"] is True
    assert a["pod_facts"]["exceptions_noted"] is False
    assert a["comparisons"]["discrepancies"]
    assert a["what_is_inconsistent"]["discrepancies"]
    assert a["what_ai_suggests"]["draft_response"]["generated"] is True
    assert a["what_ai_suggests"]["draft_response"]["auto_send"] is False
    assert a["why_human_review"]["required"] is True
    # Disputed condition should appear as unknown requiring human
    fields = {u["field"] for u in a["unknowns"]}
    assert "delivery_condition_ground_truth" in fields


def test_freightpro_freshness_source_metadata_present():
    packet = process_email(DATA / "emails" / "035.eml", use_llm=False, use_vision=False)
    fp = packet.analysis["freightpro_facts"]
    assert fp["source"] == "FreightPro snapshot"
    assert fp["snapshot_date"] == "2026-08-26"
    assert "14" in fp["freshness_note"]
    assert fp["status_semantics"] == "recorded_status_not_live_tracking"
    assert fp["status_recorded"] == fp["status"]
    # No invented per-field as_of map
    assert "field_as_of" not in fp
    assert any("2026-08-26" in a and "recorded status" in a for a in packet.assumptions)
    # Model OBSERVED_FACTS path uses the same freightpro_facts object shape
    from meridian_claims.agent import SYSTEM_PROMPT

    assert "recorded status" in SYSTEM_PROMPT.lower() or "recorded/as-of" in SYSTEM_PROMPT
    assert "14 hours" in SYSTEM_PROMPT or "14 hour" in SYSTEM_PROMPT

def test_analysis_pod_unknown_on_blank_036():
    packet = process_email(DATA / "emails" / "036.eml", use_llm=False, use_vision=False)
    pod = packet.analysis["pod_facts"]
    assert pod["available"] is False
    assert pod["load_number"] is None
    assert pod["unavailable_reason"]
    unknowns = packet.analysis["unknowns"]
    assert any(u["field"] == "pod_contents" and u["requires_human"] for u in unknowns)


def test_analysis_041_po_only_freightpro_known():
    packet = process_email(DATA / "emails" / "041.eml", use_llm=False, use_vision=False)
    a = packet.analysis
    assert a["email_facts"]["identifiers"]["load_number"] is None
    assert a["email_facts"]["identifiers"]["po"] == "PO-4500032"
    assert a["freightpro_facts"]["load_number"] == "MF-10032"
    assert a["pod_facts"]["available"] is False


def test_validate_llm_strips_invented_load_ids():
    allowed = {"MF-10487", "PO-8300487", "BOL243910"}
    llm = {
        "claim_type": "damage",
        "confidence": 0.9,
        "alleged_facts": "damage on MF-10487",
        "summary": "Also mentions MF-99999 which is fake",
        "draft_reply": "Regarding MF-99999 and PO-8300487",
        "discrepancies": ["MF-88888 mismatch"],
        "needs_human_reasons": [],
    }
    cleaned, warnings = validate_llm_against_known_facts(llm, allowed)
    assert "MF-99999" not in cleaned["summary"]
    assert "[UNKNOWN_ID]" in cleaned["summary"]
    assert "MF-99999" not in cleaned["draft_reply"]
    assert "PO-8300487" in cleaned["draft_reply"]
    assert warnings
    assert "MF-10487" in cleaned["alleged_facts"]


def test_known_identifier_set_from_analysis_sources():
    packet = process_email(DATA / "emails" / "035.eml", use_llm=False)
    a = packet.analysis
    allowed = known_identifier_set(
        a["freightpro_facts"], a["email_facts"], a["pod_facts"]
    )
    assert "MF-10487" in allowed
    assert "PO-8300487" in allowed
    assert "BOL243910" in allowed
