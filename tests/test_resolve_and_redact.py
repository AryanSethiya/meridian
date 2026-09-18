"""Unit tests: resolve, redact, evidence, POD blank-page, model payload safety."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from meridian_claims.agent import ModelResponseError
from meridian_claims.evidence import (
    SAFE_DRAFT_INVENTED_AMOUNT,
    filter_draft,
    sanitize_claim_draft,
)
from meridian_claims.freightpro import FreightProDB
from meridian_claims.pipeline import process_email
from meridian_claims.email_parser import Attachment
from meridian_claims.pod import (
    assess_pod_text_quality,
    decide_pod_attachments,
    process_pod_attachment,
)
from meridian_claims.redact import (
    assert_no_pii,
    collect_driver_names,
    model_safe_load,
    redact_text,
)
from meridian_claims.resolve_load import (
    apply_shipper_corroboration,
    extract_identifiers,
    resolve_load,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


@pytest.fixture(scope="module")
def db() -> FreightProDB:
    return FreightProDB.load(DATA)


def test_extract_mf_po_bol():
    text = "Damage claim on MF-10487 / BOL243910. PO PO-8300487. JBHT hauled."
    ids = extract_identifiers(text).identifiers
    assert ids.load_number == "MF-10487"
    assert ids.po == "PO-8300487"
    assert ids.bol == "BOL243910"


def test_extract_po_only():
    text = "Filing on PO PO-4500032 Houston to Los Angeles."
    ids = extract_identifiers(text).identifiers
    assert ids.po == "PO-4500032"
    assert ids.load_number is None


def test_resolve_by_load_number(db: FreightProDB):
    """Normal unique LoadNumber resolution (before/without sender corroboration)."""
    extracted = extract_identifiers("MF-10487 damage")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    assert res.status == "resolved"
    assert res.method == "LoadNumber"
    assert res.load["LoadNumber"] == "MF-10487"
    assert res.load["DriverName"] in ("", "[REDACTED]")
    assert res.evidence
    assert res.shipper_corroboration == "unavailable"


def test_shipper_corroboration_matching_sender(db: FreightProDB):
    extracted = extract_identifiers("MF-10487 damage")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    out = apply_shipper_corroboration(
        res, "shipping@prairiegraincooper.example", db
    )
    assert out.status == "resolved"
    assert out.load["LoadNumber"] == "MF-10487"
    assert out.shipper_corroboration == "matched"
    assert out.confidence >= 0.9
    assert any("corroborat" in e.lower() for e in out.evidence)
    assert not any("sender domain" in c.lower() for c in out.conflicts)


def test_shipper_corroboration_conflicting_sender(db: FreightProDB):
    extracted = extract_identifiers("MF-10487 damage")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    out = apply_shipper_corroboration(
        res, "claims@totally-different-shipper.example", db
    )
    assert out.status == "ambiguous"
    assert out.shipper_corroboration == "conflict"
    assert out.confidence <= 0.45
    assert out.load["LoadNumber"] == "MF-10487"  # best guess retained
    assert any("sender domain" in c.lower() for c in out.conflicts)


def test_shipper_corroboration_unavailable(db: FreightProDB):
    extracted = extract_identifiers("MF-10487 damage")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    out = apply_shipper_corroboration(res, "", db)
    assert out.status == "resolved"
    assert out.load["LoadNumber"] == "MF-10487"
    assert out.shipper_corroboration == "unavailable"
    assert any("unavailable" in e.lower() for e in out.evidence)


def test_shipper_corroboration_ignores_carrier_name_mismatch(db: FreightProDB):
    """Carrier prose differing from assigned carrier must not overturn LoadNumber."""
    extracted = extract_identifiers("MF-10487 hauled by SOME OTHER CARRIER LLC")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    out = apply_shipper_corroboration(
        res, "shipping@prairiegraincooper.example", db
    )
    assert out.status == "resolved"
    assert out.shipper_corroboration == "matched"
    assert out.load["LoadNumber"] == "MF-10487"


def test_resolve_by_po(db: FreightProDB):
    extracted = extract_identifiers("Claim against PO-4500032")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    assert res.status == "resolved"
    assert res.method == "PONumber"
    assert res.load["LoadNumber"] == "MF-10032"


def test_resolve_by_bol(db: FreightProDB):
    extracted = extract_identifiers("BOL BOL986209 damaged")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    assert res.status == "resolved"
    assert res.method == "BOLNumber"
    assert res.load["LoadNumber"] == "MF-10034"


def test_identifier_conflict_lowers_confidence(db: FreightProDB):
    """MF resolves but wrong PO must not stay high-confidence resolved."""
    extracted = extract_identifiers("Claim MF-10487 PO-4500032")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    assert res.status == "ambiguous"
    assert res.conflicts
    assert res.confidence <= 0.45
    assert res.load["LoadNumber"] == "MF-10487"


def test_multiple_load_numbers_ambiguous(db: FreightProDB):
    extracted = extract_identifiers("MF-10487 and also MF-10005 shortage")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    assert res.status == "ambiguous"
    assert set(res.multi_load_ids) == {"MF-10487", "MF-10005"}


def test_unresolved(db: FreightProDB):
    extracted = extract_identifiers("no identifiers here")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    assert res.status == "unresolved"


def test_redact_phone_and_driver_label():
    text = "Driver: Jane Doe called from 555-123-4567 about the delivery."
    out = redact_text(text)
    assert "555-123-4567" not in out
    assert "[REDACTED_PHONE]" in out
    assert "[REDACTED_DRIVER]" in out


def test_redact_receiver_line():
    text = "Condition: seals intact\nReceiver: D. Alvarez\n"
    out = redact_text(text)
    assert "Alvarez" not in out
    assert "[REDACTED_RECEIVER]" in out


def test_driver_name_from_raw_row_redacted_in_body(db: FreightProDB):
    """P0: collect DriverName from raw CSV before enrich redacts it."""
    raw = None
    for row in db.loads:
        if row.get("DriverName") and row.get("DriverPhone"):
            raw = row
            break
    assert raw is not None
    names = collect_driver_names(raw)
    assert raw["DriverName"] in names
    body = f"Please call {raw['DriverName']} at {raw['DriverPhone']} about the claim."
    redacted = redact_text(body, extra_names=names)
    assert_no_pii(redacted, [raw["DriverName"]], [raw["DriverPhone"]])


def test_model_safe_load_strips_rates(db: FreightProDB):
    extracted = extract_identifiers("MF-10487")
    res = resolve_load(db, extracted.identifiers, extracted.raw_hits)
    safe = model_safe_load(res.load)
    assert "CustomerRate" not in safe
    assert "CarrierRate" not in safe
    assert safe["LoadNumber"] == "MF-10487"


def test_filter_draft_removes_liability_language():
    draft = "Thanks. Your claim is approved and we will pay next week."
    out, warnings = filter_draft(draft)
    assert warnings
    assert "approved" not in out.lower() or "REVIEW REQUIRED" in out
    assert "we will pay" not in out.lower()


def test_draft_repeats_trusted_claim_amount():
    email = "Filing damage claim for $4,500 on MF-10487."
    draft = (
        "Thank you. We received your claim citing $4,500 in damage on MF-10487. "
        "A coordinator will review."
    )
    out, warnings = sanitize_claim_draft(draft, email_text=email, load=None)
    assert out == draft
    assert not any("monetary amount" in w.lower() for w in warnings)
    assert "$4,500" in out


def test_draft_invented_dollar_amount_blocked():
    email = "Filing damage claim on MF-10487. About 12 cases unusable."
    draft = (
        "Thank you. We note your claimed loss of $12,000 on MF-10487. "
        "A coordinator will review."
    )
    out, warnings = sanitize_claim_draft(draft, email_text=email, load={"CustomerRate": "2121"})
    assert out == SAFE_DRAFT_INVENTED_AMOUNT
    assert any("monetary amount" in w.lower() for w in warnings)
    assert "$12,000" not in out
    # Brokerage CustomerRate must not authorize inventing claim dollars.
    assert "$2121" not in out and "$2,121" not in out


def test_draft_no_dollar_amount_allowed_when_safe():
    email = "Damage claim on MF-10487 / BOL243910."
    draft = (
        "Thank you for contacting Meridian Freight claims.\n\n"
        "We received your note regarding MF-10487. A coordinator is reviewing.\n\n"
        "— Meridian Freight Claims (draft for human review — not sent)"
    )
    out, warnings = sanitize_claim_draft(draft, email_text=email, load=None)
    assert out == draft
    assert warnings == []


def test_draft_payment_settlement_language_still_filtered():
    email = "Claim for $4,500 damage."
    draft = (
        "Your claim is approved for $4,500 and we will pay next week. "
        "This is a settlement of $4,500."
    )
    out, warnings = sanitize_claim_draft(draft, email_text=email, load=None)
    assert warnings
    assert any("forbidden draft language" in w.lower() for w in warnings)
    assert "we will pay" not in out.lower()
    assert "approved" not in out.lower() or "REVIEW REQUIRED" in out
    # Trusted $4,500 may remain where liability phrases were scrubbed around it.
    assert "$12,000" not in out


def test_blank_page_pod_skips_vision():
    data = (DATA / "attachments" / "POD_scan_a.pdf").read_bytes()
    result = process_pod_attachment("POD_scan_a.pdf", data, use_vision=True)
    assert result.status == "unreadable"
    assert result.mode in ("blank_page", "unreadable")
    assert "Vision skipped" in result.findings or result.mode == "blank_page"


def test_valid_text_pod_passes_quality():
    data = (DATA / "attachments" / "POD_MF-10487.pdf").read_bytes()
    result = process_pod_attachment("POD_MF-10487.pdf", data, use_vision=False)
    assert result.status == "readable"
    assert result.mode == "text"
    assert "MF-10487" in result.excerpt
    assert "quality check passed" in result.findings.lower()
    ok, reasons = assess_pod_text_quality(result.excerpt)
    assert ok is True
    assert reasons == []


def test_empty_attachment_invalid():
    result = process_pod_attachment("POD_empty.pdf", b"", use_vision=False)
    assert result.status == "invalid"


def test_garbage_extracted_pod_text_unreadable(monkeypatch):
    garbage = (
        "@@##$$%%^^&&**(( )) !! ~~ `` || ~~~ ###@@@ "
        "x9x9x9x9 #### $$$$ %%%% ~~~~ ;;;; ::::"
    )
    assert len(garbage) >= 20
    ok, reasons = assess_pod_text_quality(garbage)
    assert ok is False
    assert reasons

    monkeypatch.setattr(
        "meridian_claims.pod.extract_pdf_text",
        lambda _data: garbage,
    )
    # Non-empty PDF bytes so we skip the empty-attachment path.
    result = process_pod_attachment(
        "POD_garbage.pdf",
        b"%PDF-1.4 junk",
        use_vision=False,
    )
    assert result.status == "unreadable"
    assert result.excerpt == ""
    assert "quality check" in result.findings.lower()


def test_short_pod_text_fails_quality():
    short = "POD seals OK!!!"  # under TEXT_MIN_CHARS / sparse
    ok, reasons = assess_pod_text_quality(short)
    assert ok is False
    assert any("too_short" in r or "insufficient_words" in r for r in reasons)


def test_corrupt_pdf_invalid():
    result = process_pod_attachment("POD_bad.pdf", b"not-a-pdf", use_vision=False)
    assert result.status == "invalid"


def test_attachment_inventory_one_pod():
    atts = [
        Attachment(
            filename="POD_MF-10487.pdf",
            content_type="application/pdf",
            data=b"%PDF-one",
        )
    ]
    d = decide_pod_attachments(atts)
    assert d["selection_status"] == "selected"
    assert d["selected"]["filename"] == "POD_MF-10487.pdf"
    assert d["pod_candidates"] == ["POD_MF-10487.pdf"]
    assert d["unprocessed"] == []
    assert len(d["inventory"]) == 1


def test_attachment_inventory_pod_plus_unrelated():
    atts = [
        Attachment("POD_MF-10487.pdf", "application/pdf", b"%PDF-pod"),
        Attachment("rates_export.xls", "application/vnd.ms-excel", b"XLS"),
    ]
    d = decide_pod_attachments(atts)
    assert d["selection_status"] == "selected"
    assert d["selected"]["filename"] == "POD_MF-10487.pdf"
    assert len(d["unprocessed"]) == 1
    assert d["unprocessed"][0]["filename"] == "rates_export.xls"
    assert d["unprocessed"][0]["status"] == "present_not_processed"


def test_attachment_inventory_multiple_pod_candidates():
    atts = [
        Attachment("POD_scan_a.pdf", "application/pdf", b"%PDF-a"),
        Attachment("POD_scan_b.pdf", "application/pdf", b"%PDF-b"),
    ]
    d = decide_pod_attachments(atts)
    assert d["selection_status"] == "ambiguous_multiple_pods"
    assert d["selected"] is None
    assert set(d["pod_candidates"]) == {"POD_scan_a.pdf", "POD_scan_b.pdf"}
    assert len(d["unprocessed"]) == 2


def test_attachment_inventory_no_pod():
    atts = [
        Attachment("notes.txt", "text/plain", b"hello"),
    ]
    d = decide_pod_attachments(atts)
    assert d["selection_status"] == "no_pod"
    assert d["selected"] is None
    assert d["pod_candidates"] == []
    assert d["unprocessed"][0]["status"] == "present_not_processed"


def test_showcase_035_dry_run():
    packet = process_email(DATA / "emails" / "035.eml", use_llm=False, use_vision=False)
    assert packet.resolution.status == "resolved"
    assert packet.resolution.load["LoadNumber"] == "MF-10487"
    assert packet.resolution.shipper_corroboration == "matched"
    assert packet.needs_human is True
    assert packet.pod.mode == "text"
    assert packet.attachments.get("selection_status") == "selected"
    assert packet.attachments.get("selected_filename") == "POD_MF-10487.pdf"
    assert any("damage" in d.lower() or "exception" in d.lower() for d in packet.discrepancies)
    assert "Alvarez" not in packet.pod.excerpt
    assert packet.observed
    assert packet.interpretation.get("source") == "deterministic"
    assert packet.llm_status == "dry_run"


def test_showcase_036_blank_pod():
    packet = process_email(DATA / "emails" / "036.eml", use_llm=False, use_vision=False)
    assert packet.resolution.status == "resolved"
    assert packet.resolution.load["LoadNumber"] == "MF-10005"
    assert packet.resolution.shipper_corroboration == "matched"
    assert packet.pod.status == "unreadable"
    assert packet.pod.mode == "blank_page"
    assert packet.attachments.get("selection_status") == "selected"
    assert packet.attachments.get("selected_filename") == "POD_scan_a.pdf"
    assert packet.needs_human is True


def test_showcase_041_po_only():
    packet = process_email(DATA / "emails" / "041.eml", use_llm=False, use_vision=False)
    assert packet.resolution.status == "resolved"
    assert packet.resolution.method == "PONumber"
    assert packet.resolution.load["LoadNumber"] == "MF-10032"
    assert packet.resolution.shipper_corroboration == "matched"


def test_showcase_042_bol_only():
    packet = process_email(DATA / "emails" / "042.eml", use_llm=False, use_vision=False)
    assert packet.resolution.status == "resolved"
    assert packet.resolution.method == "BOLNumber"
    assert packet.resolution.load["LoadNumber"] == "MF-10034"
    assert packet.resolution.shipper_corroboration == "matched"


def test_model_payload_has_no_driver_pii(db: FreightProDB):
    """Prove the Anthropic context would not contain known driver name/phone."""
    raw = None
    for row in db.loads:
        if row.get("DriverName") and row.get("LoadNumber"):
            raw = row
            break
    assert raw is not None

    # Simulate pipeline redaction for a synthetic body mentioning the driver.
    names = collect_driver_names(raw)
    body = f"Driver {raw['DriverName']} phone {raw['DriverPhone']} on {raw['LoadNumber']}"
    subject = f"Claim {raw['LoadNumber']}"
    redacted_body = redact_text(body, extra_names=names)
    redacted_subject = redact_text(subject, extra_names=names)
    enriched = db.enrich_load(raw, include_pii=False)
    safe = model_safe_load(enriched)
    payload = json.dumps(
        {"subject": redacted_subject, "body": redacted_body, "load": safe},
        default=str,
    )
    assert_no_pii(payload, [raw["DriverName"]], [raw["DriverPhone"]])
    assert "CustomerRate" not in payload


def test_llm_failure_falls_back_without_crash():
    with patch(
        "meridian_claims.pipeline.classify_and_draft",
        side_effect=ModelResponseError("boom"),
    ):
        packet = process_email(
            DATA / "emails" / "035.eml",
            use_llm=True,
            use_vision=False,
        )
    assert packet.llm_status == "error"
    assert packet.needs_human is True
    assert packet.classification.source == "llm_fallback"
    assert packet.resolution.load["LoadNumber"] == "MF-10487"
    assert any("fallback" in r.lower() for r in packet.needs_human_reasons)


def test_needs_human_forced_even_if_model_says_false():
    from meridian_claims.pii_gate import PIIGateReport

    fake = {
        "claim_type": "damage",
        "confidence": 0.99,
        "alleged_facts": "damage",
        "discrepancies": [],
        "draft_reply": "We received your claim.",
        "needs_human": False,
        "needs_human_reasons": [],
        "summary": "ok",
    }
    report = PIIGateReport(status="passed", lexicon_entries=0)
    with patch(
        "meridian_claims.pipeline.classify_and_draft",
        return_value=(fake, None, report, "safe user message"),
    ):
        packet = process_email(
            DATA / "emails" / "035.eml",
            use_llm=True,
            use_vision=False,
        )
    assert packet.needs_human is True


def test_carrier_scac_mention(db: FreightProDB):
    """JBHT resolves via FreightPro SCAC → JB Hunt row (no hardcoded pipeline tokens)."""
    hit = db.find_carrier_mention("JBHT hauled this load")
    assert hit is not None
    assert hit.get("SCAC") == "JBHT"
    assert "Hunt" in (hit.get("LegalName") or "") or hit.get("LegalName") == "JBHT"


def test_carrier_normalized_legal_name_mention(db: FreightProDB):
    hit = db.find_carrier_mention("J.B. Hunt hauled the freight")
    assert hit is not None
    assert hit.get("SCAC") == "JBHT" or "Hunt" in (hit.get("LegalName") or "")


def test_showcase_035_jbht_resolves_via_freightpro():
    packet = process_email(DATA / "emails" / "035.eml", use_llm=False, use_vision=False)
    assert packet.resolution.load["LoadNumber"] == "MF-10487"
    # Assigned load carrier and/or email mention map to JB Hunt / JBHT
    assert packet.carrier.scac == "JBHT" or (
        packet.carrier.legal_name and "Hunt" in packet.carrier.legal_name
    )
    assert packet.carrier.email_mention in ("JBHT", "JB Hunt", "JBHT") or (
        packet.carrier.email_mention and "Hunt" in packet.carrier.email_mention
    ) or packet.carrier.scac == "JBHT"
