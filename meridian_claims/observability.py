"""Lightweight structured decision/audit records (no external observability stack)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# Keys that must never appear in a decision record (defense in depth).
_FORBIDDEN_SUBSTRINGS = (
    "drivername",
    "driverphone",
    "driver_name",
    "driver_phone",
    "[redacted_driver]",  # markers OK in notes elsewhere; we avoid body/excerpts entirely
)


@dataclass
class DecisionRecord:
    """
    Coordinator-auditable decision log for one email.

    Contains identifiers and process metadata only — never email bodies,
    POD excerpts, driver names, or phone numbers.
    """

    email_id: str
    intent: str
    extracted_identifiers: dict[str, str | None]
    resolution_method: str | None
    resolved_load: str | None
    resolution_status: str
    resolution_confidence: float
    attachment: dict[str, Any]
    pod_extraction_method: str
    pod_status: str
    evidence_used: list[str] = field(default_factory=list)
    discrepancy_count: int = 0
    pii_redaction_status: str = "unknown"
    model_call_status: str = "unknown"
    model_output_validation_status: str = "unknown"
    escalation_decision: str = "escalate"  # escalate | (reserved)
    escalation_reasons: list[str] = field(default_factory=list)
    draft_generated: bool = False
    processing_duration_ms: int = 0
    classification_source: str = "unknown"
    # Cost / latency observability (estimated model cost; duration via perf_counter).
    model_called: bool = False
    model_name: str | None = None
    model_input_tokens: int | None = None
    model_output_tokens: int | None = None
    estimated_input_cost_usd: float | None = None
    estimated_output_cost_usd: float | None = None
    estimated_total_cost_usd: float | None = None
    model_latency_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_decision_record(
    *,
    email_id: str,
    intent: str,
    identifiers: Any,
    resolution: Any,
    pod: Any,
    attachment_filenames: list[str],
    evidence: list[str],
    discrepancy_count: int,
    pii_gate: dict[str, Any],
    llm_status: str,
    classification_source: str,
    needs_human: bool,
    needs_human_reasons: list[str],
    draft_reply: str,
    processing_duration_ms: int,
    llm_error: str | None = None,
    attachment_decision: dict[str, Any] | None = None,
    usage: Any | None = None,
) -> DecisionRecord:
    """Assemble a PII-safe decision record from pipeline outputs."""
    resolved_load = None
    if resolution.load:
        resolved_load = resolution.load.get("LoadNumber")

    model_validation = _model_validation_status(llm_status, classification_source, llm_error)

    att_meta: dict[str, Any] = {
        "count": len(attachment_filenames),
        "filenames": list(attachment_filenames),
        "has_pdf": any(f.lower().endswith(".pdf") for f in attachment_filenames),
    }
    if attachment_decision:
        att_meta.update(
            {
                "selection_status": attachment_decision.get("selection_status"),
                "selected_filename": attachment_decision.get("selected_filename"),
                "pod_candidates": list(attachment_decision.get("pod_candidates") or []),
                "unprocessed_count": len(attachment_decision.get("unprocessed") or []),
                "reason": attachment_decision.get("reason"),
            }
        )

    record = DecisionRecord(
        email_id=email_id,
        intent=intent,
        extracted_identifiers={
            "load_number": identifiers.load_number,
            "po": identifiers.po,
            "bol": identifiers.bol,
            "pro": identifiers.pro,
        },
        resolution_method=resolution.method,
        resolved_load=resolved_load,
        resolution_status=resolution.status,
        resolution_confidence=float(resolution.confidence or 0.0),
        attachment=att_meta,
        pod_extraction_method=pod.mode,
        pod_status=pod.status,
        evidence_used=_safe_evidence_list(evidence or list(resolution.evidence or [])),
        discrepancy_count=discrepancy_count,
        pii_redaction_status=str(pii_gate.get("status") or "unknown"),
        model_call_status=llm_status,
        model_output_validation_status=model_validation,
        escalation_decision="escalate" if needs_human else "no_escalate",
        escalation_reasons=_safe_reason_list(needs_human_reasons),
        draft_generated=bool(draft_reply and draft_reply.strip()),
        processing_duration_ms=processing_duration_ms,
        classification_source=classification_source,
        model_called=bool(usage is not None and llm_status == "ok"),
        model_name=(getattr(usage, "model", None) or None) if usage else None,
        model_input_tokens=(
            int(getattr(usage, "input_tokens", 0) or 0) if usage and llm_status == "ok" else None
        ),
        model_output_tokens=(
            int(getattr(usage, "output_tokens", 0) or 0) if usage and llm_status == "ok" else None
        ),
        estimated_input_cost_usd=(
            getattr(usage, "estimated_input_cost_usd", None) if usage else None
        ),
        estimated_output_cost_usd=(
            getattr(usage, "estimated_output_cost_usd", None) if usage else None
        ),
        estimated_total_cost_usd=(
            getattr(usage, "estimated_total_cost_usd", None) if usage else None
        ),
        model_latency_ms=(
            int(getattr(usage, "latency_ms", 0) or 0) if usage and llm_status == "ok" else None
        ),
    )
    _assert_no_forbidden_payload(record.to_dict())
    return record


def _model_validation_status(
    llm_status: str, classification_source: str, llm_error: str | None
) -> str:
    if llm_status == "dry_run":
        return "skipped_dry_run"
    if llm_status == "pii_blocked":
        return "blocked_pii_gate"
    if llm_status == "error":
        return "invalid_or_api_error_fallback"
    if llm_status == "ok" and classification_source == "llm":
        return "validated_ok"
    if classification_source == "llm_fallback":
        return "fallback_after_failure"
    if classification_source == "deterministic":
        return "skipped_deterministic"
    return "unknown"


def _safe_evidence_list(items: list[str]) -> list[str]:
    """Keep short evidence tags; drop anything that looks like body/POD content."""
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        # Evidence from resolve_load is structured (primary_match=...); keep those.
        # Truncate long free text that might embed PII.
        if len(s) > 200:
            s = s[:200] + "…"
        if _looks_like_pii_leak(s):
            out.append("[evidence omitted — possible PII]")
            continue
        out.append(s)
    return out


def _safe_reason_list(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        if len(s) > 240:
            s = s[:240] + "…"
        if _looks_like_pii_leak(s):
            continue
        out.append(s)
    return out


def _looks_like_pii_leak(text: str) -> bool:
    lower = text.lower()
    if "driver:" in lower or "receiver:" in lower:
        return True
    # phone-shaped
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) >= 10 and any(c in text for c in "-()."):
        return True
    return False


def _assert_no_forbidden_payload(obj: Any, path: str = "") -> None:
    """Raise if decision JSON accidentally includes forbidden keys/values patterns."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower().replace("-", "")
            if kl in ("drivername", "driverphone", "driver_name", "driver_phone"):
                raise ValueError(f"Decision record must not include key {path}.{k}")
            if kl in ("body", "excerpt", "alleged_facts", "draft_reply", "email_body"):
                raise ValueError(f"Decision record must not include content field {path}.{k}")
            _assert_no_forbidden_payload(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_forbidden_payload(v, f"{path}[{i}]")
