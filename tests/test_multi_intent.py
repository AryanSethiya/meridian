"""Multi-intent claims email safety (no secondary ops execution)."""

from __future__ import annotations

from pathlib import Path

from meridian_claims.pipeline import _secondary_ops_intents, process_email

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def test_secondary_intents_helper_detects_tracking_and_quote():
    text = (
        "Filing a damage claim on MF-10487. Also please send tracking on this load "
        "and quote us another lane Chicago to Dallas."
    )
    assert _secondary_ops_intents(text) == ["tracking", "quote"]


def test_secondary_intents_helper_ignores_claim_only():
    text = "Damage claim on MF-10487 / BOL243910. Crushed pallets — start a claim."
    assert _secondary_ops_intents(text) == []


def test_multi_intent_email_escalates_keeps_claims(tmp_path: Path):
    eml = tmp_path / "multi_intent.eml"
    eml.write_text(
        "From: shipping@prairiegraincooper.example\n"
        "To: claims@meridianfreight.example\n"
        "Subject: Damage claim MF-10487 + tracking\n"
        "Date: Mon, 24 Aug 2026 22:00:00 -0500\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "We are filing a damage claim on MF-10487 / BOL243910.\n"
        "Also need tracking status / ETA for this shipment, and please send the invoice.\n",
        encoding="utf-8",
    )
    packet = process_email(
        eml,
        data_dir=DATA,
        output_dir=tmp_path / "out",
        use_llm=False,
        use_vision=False,
    )
    assert packet.needs_human is True
    assert any("multiple intents" in r.lower() for r in packet.needs_human_reasons)
    assert "tracking" in packet.analysis["email_facts"]["secondary_intents"]
    assert "invoice" in packet.analysis["email_facts"]["secondary_intents"]
    # Claims investigation continues (load still resolved).
    assert packet.resolution.load["LoadNumber"] == "MF-10487"
    assert packet.classification.claim_type == "damage"


def test_single_intent_035_unchanged():
    packet = process_email(DATA / "emails" / "035.eml", use_llm=False, use_vision=False)
    assert packet.analysis["email_facts"]["secondary_intents"] == []
    assert not any("multiple intents" in r.lower() for r in packet.needs_human_reasons)
    assert packet.needs_human is True
    assert packet.resolution.load["LoadNumber"] == "MF-10487"
