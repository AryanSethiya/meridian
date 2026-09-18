"""
Model-boundary PII tests: raw input → pipeline → Anthropic payload must not
contain person names (lexicon), phones, emails, or FreightPro driver fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meridian_claims.email_parser import ParsedEmail, parse_eml
from meridian_claims.freightpro import FreightProDB
from meridian_claims.pii_gate import PIIGateError, build_lexicon_from_rows, prepare_text_payload
from meridian_claims.pipeline import process_email
from meridian_claims.redact import assert_no_pii, redact_text

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


@pytest.fixture(scope="module")
def db() -> FreightProDB:
    return FreightProDB.load(DATA)


@pytest.fixture
def driver_row(db: FreightProDB) -> dict:
    for row in db.loads:
        if (row.get("DriverName") or "").strip() and (row.get("DriverPhone") or "").strip():
            return row
    pytest.skip("No driver PII in snapshot")


def _fake_anthropic(captured: dict):
    class FakeMessages:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            content = MagicMock()
            content.type = "text"
            content.text = json.dumps(
                {
                    "claim_type": "damage",
                    "confidence": 0.8,
                    "alleged_facts": "damage alleged",
                    "discrepancies": [],
                    "draft_reply": "Under review by a coordinator.",
                    "needs_human_reasons": ["HITL"],
                    "summary": "damage claim under review",
                }
            )
            resp = MagicMock()
            resp.content = [content]
            resp.usage = MagicMock(input_tokens=20, output_tokens=10)
            return resp

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    return FakeClient


def test_redact_masks_email_and_phone():
    text = "Call Jane at 555-123-4567 or jane.doe@shipper.example"
    out = redact_text(text)
    assert "555-123-4567" not in out
    assert "jane.doe@shipper.example" not in out
    assert "[REDACTED_PHONE]" in out
    assert "[REDACTED_EMAIL]" in out


def test_prepare_payload_strips_email_phone_name_from_body(driver_row: dict):
    lex = build_lexicon_from_rows(driver_row)
    dirty = {
        "email_facts": {
            "body_redacted": (
                f"Please call {driver_row['DriverName']} at {driver_row['DriverPhone']} "
                f"or email ops-contact@dangerous.example about the load."
            ),
        },
        "resolution_load_for_model": {
            "LoadNumber": driver_row["LoadNumber"],
            "DriverName": driver_row["DriverName"],
            "DriverPhone": driver_row["DriverPhone"],
        },
    }
    _safe, serialized, report = prepare_text_payload(dirty, lex, fail_closed=True)
    assert report.status == "passed"
    assert_no_pii(serialized, [driver_row["DriverName"]], [driver_row["DriverPhone"]])
    assert "ops-contact@dangerous.example" not in serialized
    assert "@dangerous.example" not in serialized or "[REDACTED_EMAIL]" in serialized


def test_pipeline_model_boundary_blocks_name_phone_email(
    driver_row: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """
    End-to-end: inject PII into email body → capture Anthropic user content →
    assert raw PII absent. Does not call the real Anthropic API.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    captured: dict = {}
    FakeClient = _fake_anthropic(captured)
    real_parse = parse_eml
    injected_email = "witness@external.example"

    def parse_with_pii(path, attachment_dir=None):
        parsed = real_parse(path, attachment_dir=attachment_dir)
        body = (
            f"{parsed.body}\n"
            f"On-site contact {driver_row['DriverName']} "
            f"phone {driver_row['DriverPhone']} "
            f"email {injected_email}."
        )
        return ParsedEmail(
            path=parsed.path,
            email_id=parsed.email_id,
            from_addr=parsed.from_addr,
            to_addr=parsed.to_addr,
            subject=parsed.subject,
            date=parsed.date,
            body=body,
            attachments=parsed.attachments,
        )

    with patch("meridian_claims.pipeline.parse_eml", side_effect=parse_with_pii):
        with patch(
            "meridian_claims.pipeline.build_lexicon_from_rows",
            side_effect=lambda *rows: build_lexicon_from_rows(driver_row, *rows),
        ):
            with patch("anthropic.Anthropic", FakeClient):
                packet = process_email(
                    DATA / "emails" / "035.eml",
                    output_dir=tmp_path,
                    use_llm=True,
                    use_vision=False,
                )

    assert packet.llm_status == "ok"
    assert packet.usage is not None
    assert packet.usage.estimated_total_cost_usd is not None
    user_content = captured["kwargs"]["messages"][0]["content"]
    assert_no_pii(
        user_content,
        [driver_row["DriverName"]],
        [driver_row["DriverPhone"]],
    )
    assert injected_email not in user_content
    assert driver_row["DriverName"] not in user_content
    # Envelope addresses still must not appear
    assert "shipping@" not in user_content
    assert "claims@" not in user_content


def test_fail_closed_when_redaction_bypassed(driver_row: dict):
    lex = build_lexicon_from_rows(driver_row)
    with patch(
        "meridian_claims.pii_gate.redact_structure",
        side_effect=lambda obj, _lex: (obj, {"phones": 0, "names": 0, "receivers": 0, "emails": 0}),
    ):
        with pytest.raises(PIIGateError):
            prepare_text_payload(
                {
                    "body": (
                        f"Contact {driver_row['DriverName']} at "
                        f"{driver_row['DriverPhone']}"
                    )
                },
                lex,
                fail_closed=True,
            )


def test_fail_closed_on_residual_email_when_bypass():
    lex = build_lexicon_from_rows()
    with patch(
        "meridian_claims.pii_gate.redact_structure",
        side_effect=lambda obj, _lex: (obj, {"phones": 0, "names": 0, "receivers": 0, "emails": 0}),
    ):
        with pytest.raises(PIIGateError):
            prepare_text_payload(
                {"body": "Reply to secret.person@shipper.example please"},
                lex,
                fail_closed=True,
            )
