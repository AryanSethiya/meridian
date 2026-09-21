"""Anthropic-backed claim classification and draft reply generation."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from meridian_claims.models import Classification, ModelUsage
from meridian_claims.pii_gate import PIIGateError, PIIGateReport, PIILexicon, prepare_text_payload
from meridian_claims.pricing import load_pricing

SYSTEM_PROMPT = """You are an ops assistant for Meridian Freight, a freight broker.
You help coordinators triage inbound claims emails. You never send email yourself.

You receive OBSERVED_FACTS with clearly separated sections:
  - freightpro_facts (from TMS only)
  - email_facts (from the email only)
  - pod_facts (from the POD only; may be unavailable/null)
  - comparisons (deterministic discrepancies already computed)
  - unknowns (facts that are null — do not invent values for these)

Rules — critical:
- Do NOT invent load numbers, PO/BOL/PRO numbers, rates, carrier IDs, POD contents,
  delivery conditions, or liability outcomes that are not in OBSERVED_FACTS.
- If a fact is null/unknown in OBSERVED_FACTS, say it is unknown — never fill it in.
- Do not contradict deterministic comparisons or resolution status.
- PII (driver names/phones, receivers) has been redacted; do not ask for it back.
- FreightPro data is a reporting snapshot (see freightpro_facts.source / snapshot_date /
  freshness_note). Treat Status and similar fields as recorded status as of that
  snapshot — not live GPS/tracking. Do not invent per-field as_of timestamps.
  Real FreightPro reporting can lag live operations by roughly 14 hours.

Return ONLY a JSON object with these keys:
{
  "claim_type": "damage|shortage|refusal|osd|other|not_a_claim",
  "confidence": 0.0-1.0,
  "alleged_facts": "short summary of what the shipper claims (from the email only)",
  "discrepancies": ["optional extra mismatches; prefer seed comparisons given"],
  "draft_reply": "professional reply for HUMAN REVIEW ONLY — do not approve/deny/settle",
  "needs_human_reasons": ["why a coordinator must review"],
  "summary": "one or two sentence coordinator summary grounded only in known facts"
}

Draft rules:
- Acknowledge receipt; cite only identifiers present in OBSERVED_FACTS.
- If POD is unavailable/unknown, say so and ask for usable evidence.
- When mentioning FreightPro load status, call it recorded/as-of-snapshot status,
  not live tracking.
- Never say the claim is approved, denied, paid, or that Meridian accepts liability.
"""


class MissingAPIKeyError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is not set."""


class ModelResponseError(RuntimeError):
    """Raised when the model returns unusable output or the API fails."""


def require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. Set it either:\n"
            "  1) export ANTHROPIC_API_KEY=sk-ant-...   (shell — wins if both set)\n"
            "  2) put ANTHROPIC_API_KEY=... in a repo-root .env file\n"
            "Then retry."
        )
    return key


def classify_and_draft(
    context: dict[str, Any],
    lexicon: PIILexicon | None = None,
) -> tuple[dict[str, Any], ModelUsage, PIIGateReport, str]:
    """
    Call Anthropic with a gate-sanitized payload.

    Returns (parsed_json, usage, pii_report, exact_user_message_sent).
    The exact_user_message_sent string is what would be transmitted — tests assert
    no DriverName/DriverPhone appear in it.
    """
    import anthropic

    api_key = require_api_key()
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    client = anthropic.Anthropic(api_key=api_key)

    lex = lexicon or PIILexicon()
    try:
        _safe_ctx, serialized, report = prepare_text_payload(context, lex, fail_closed=True)
    except PIIGateError:
        raise

    user_message = (
        "Produce the JSON interpretation fields for this claims triage case.\n"
        "OBSERVED_FACTS follow. Do not contradict deterministic resolution.\n\n"
        f"{serialized}"
    )

    t0 = time.perf_counter()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": user_message,
                }
            ],
        )
    except MissingAPIKeyError:
        raise
    except PIIGateError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ModelResponseError(f"Anthropic API call failed: {exc}") from exc

    latency_ms = int((time.perf_counter() - t0) * 1000)
    text = "".join(block.text for block in resp.content if block.type == "text")
    try:
        data = _parse_json_object(text)
    except ValueError as exc:
        raise ModelResponseError(str(exc)) from exc

    if not isinstance(data, dict):
        raise ModelResponseError("Model JSON root was not an object")
    if "claim_type" not in data and "draft_reply" not in data:
        raise ModelResponseError("Model JSON missing claim_type and draft_reply")

    # Use Anthropic-reported usage only — never invent token counts.
    raw_usage = getattr(resp, "usage", None)
    in_tok = getattr(raw_usage, "input_tokens", None) if raw_usage is not None else None
    out_tok = getattr(raw_usage, "output_tokens", None) if raw_usage is not None else None
    if in_tok is None and out_tok is None:
        cost = load_pricing().estimate(None, None)
        usage = ModelUsage(
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            model=model,
            estimated_input_cost_usd=None,
            estimated_output_cost_usd=None,
            estimated_total_cost_usd=None,
            pricing_note=str(cost.get("note") or ""),
        )
    else:
        in_i = int(in_tok or 0)
        out_i = int(out_tok or 0)
        cost = load_pricing().estimate(in_i, out_i)
        usage = ModelUsage(
            input_tokens=in_i,
            output_tokens=out_i,
            latency_ms=latency_ms,
            model=model,
            estimated_input_cost_usd=cost["estimated_input_cost_usd"],
            estimated_output_cost_usd=cost["estimated_output_cost_usd"],
            estimated_total_cost_usd=cost["estimated_total_cost_usd"],
            pricing_note=str(cost.get("note") or ""),
        )
    return data, usage, report, user_message


def classification_from_llm(data: dict[str, Any]) -> Classification:
    return Classification(
        claim_type=str(data.get("claim_type", "other")),
        confidence=float(data.get("confidence", 0.5)),
        alleged_facts=str(data.get("alleged_facts", "")),
        source="llm",
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise ValueError(f"Model did not return JSON: {text[:300]}")
