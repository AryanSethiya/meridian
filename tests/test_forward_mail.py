"""Forwarded / third-party email handling tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from meridian_claims.forward_mail import analyze_forwarded_content
from meridian_claims.pipeline import process_email

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def test_analyze_forward_markers():
    analysis = analyze_forwarded_content(
        "Fwd: Damage claim MF-10487",
        "See below.\n\n---------- Forwarded message ---------\n"
        "From: Jane Thirdparty <jane@other.example>\n"
        "Damage on MF-10487. Call John Smith at 555-111-2222.\n",
    )
    assert analysis.detected is True
    assert "subject:Fwd/FW" in analysis.markers
    assert any("forwarded_message" in m for m in analysis.markers)
    assert analysis.sender_trust == "envelope_from_only"
    assert analysis.safe_redaction_guaranteed is False
    assert analysis.model_policy == "fail_closed_withheld"
    assert analysis.preamble.startswith("See below")
    assert analysis.third_party_block_present is True


def test_forwarded_email_fail_closed_and_escalates(tmp_path: Path):
    eml = tmp_path / "fwd_claim.eml"
    eml.write_text(
        "From: shipping@prairiegraincooper.example\n"
        "To: claims@meridianfreight.example\n"
        "Subject: Fwd: Damage claim MF-10487\n"
        "Date: Mon, 24 Aug 2026 22:00:00 -0500\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Passing along.\n"
        "\n"
        "---------- Forwarded message ---------\n"
        "From: Jane Thirdparty <jane@other.example>\n"
        "Date: Mon, 24 Aug 2026\n"
        "Subject: Damage\n"
        "To: shipping@prairiegraincooper.example\n"
        "\n"
        "Damage on MF-10487 / BOL243910. Call John Smith at 555-111-2222.\n",
        encoding="utf-8",
    )
    with patch("meridian_claims.pipeline.classify_and_draft") as mock_llm:
        packet = process_email(
            eml,
            data_dir=DATA,
            output_dir=tmp_path / "out",
            use_llm=True,
            use_vision=False,
        )
    mock_llm.assert_not_called()
    assert packet.llm_status == "pii_blocked"
    fwd = packet.analysis["email_facts"]["forward"]
    assert fwd["detected"] is True
    assert fwd["sender_trust"] == "envelope_from_only"
    assert fwd["safe_redaction_guaranteed"] is False
    assert any("Forwarded/third-party" in r for r in packet.needs_human_reasons)
    # Envelope From still used for shipper corroboration — not embedded Jane From.
    assert packet.resolution.shipper_corroboration == "matched"
    assert packet.resolution.load["LoadNumber"] == "MF-10487"
    # Identifiers preserved from original/forwarded content for the coordinator.
    assert packet.identifiers.load_number == "MF-10487"


def test_non_forward_035_unaffected():
    packet = process_email(DATA / "emails" / "035.eml", use_llm=False, use_vision=False)
    fwd = packet.analysis["email_facts"]["forward"]
    assert fwd["detected"] is False
    assert fwd["model_policy"] == "normal"
    assert packet.llm_status == "dry_run"
    assert not any("Forwarded/third-party" in r for r in packet.needs_human_reasons)
