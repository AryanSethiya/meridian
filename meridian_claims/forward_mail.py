"""Detect forwarded / third-party email content (no full chain understanding)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


# Standard markers only — do not attempt to parse nested thread semantics.
_BODY_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    (
        "forwarded_message",
        re.compile(r"(?im)^-+\s*Forwarded message\s*-+\s*$"),
    ),
    (
        "begin_forwarded_message",
        re.compile(r"(?im)^Begin forwarded message:\s*$"),
    ),
    (
        "original_message",
        re.compile(r"(?im)^-+\s*Original Message\s*-+\s*$"),
    ),
    (
        "outlook_forward_header",
        re.compile(r"(?im)^From:\s+.+\r?\n(?:Sent|Date):\s+.+\r?\nTo:\s+", re.DOTALL),
    ),
]

_SUBJECT_FWD_RE = re.compile(r"(?i)^\s*(?:fw|fwd)\s*:")


@dataclass
class ForwardAnalysis:
    """Auditable forward/third-party handling decision (no raw PII values)."""

    detected: bool = False
    markers: list[str] = field(default_factory=list)
    # Outer coordinator note above the first forward marker (may be empty).
    preamble: str = ""
    # True when a forwarded block was split off the body.
    third_party_block_present: bool = False
    # Never use embedded Original-From for shipper corroboration.
    sender_trust: str = "envelope_from_only"
    # Safe redaction of unknown third-party PII cannot be guaranteed.
    safe_redaction_guaranteed: bool = True
    model_policy: str = "normal"  # normal | fail_closed_withheld
    escalation_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_forwarded_content(subject: str, body: str) -> ForwardAnalysis:
    """
    Detect likely forwarded content using standard email signals.

    Preserves the full body for identifier extraction / coordinator review.
    Does not treat embedded From/Sent headers as trusted sender identity.
    """
    markers: list[str] = []
    subj = subject or ""
    text = body or ""

    if _SUBJECT_FWD_RE.search(subj):
        markers.append("subject:Fwd/FW")

    split_at: int | None = None
    for label, pat in _BODY_MARKERS:
        m = pat.search(text)
        if m:
            markers.append(f"body:{label}")
            idx = m.start()
            if split_at is None or idx < split_at:
                split_at = idx

    if not markers:
        return ForwardAnalysis(
            detected=False,
            preamble=text,
            sender_trust="envelope_from_only",
            safe_redaction_guaranteed=True,
            model_policy="normal",
        )

    preamble = text[:split_at].strip() if split_at is not None else text.strip()
    third_party = split_at is not None and split_at < len(text)
    reason = (
        "Forwarded/third-party email content detected "
        f"({', '.join(markers)}). Embedded sender headers are not trusted for "
        "shipper corroboration. Unknown third-party PII cannot be safely "
        "guaranteed after redaction — Anthropic call fail-closed; human review."
    )
    return ForwardAnalysis(
        detected=True,
        markers=markers,
        preamble=preamble,
        third_party_block_present=third_party,
        sender_trust="envelope_from_only",
        safe_redaction_guaranteed=False,
        model_policy="fail_closed_withheld",
        escalation_reason=reason,
    )


def model_safe_forward_body(analysis: ForwardAnalysis, redacted_body_or_preamble: str) -> str:
    """
    Content allowed into the model context for the email body.

    When a forward is detected, withhold the third-party block entirely rather
    than pretending unknown names/phones were fully redacted. Caller must pass
    an already-redacted preamble (or full body when not a forward).
    """
    if not analysis.detected:
        return redacted_body_or_preamble
    preamble = (redacted_body_or_preamble or "").strip()
    if preamble:
        return (
            preamble
            + "\n\n[FORWARDED/THIRD-PARTY CONTENT WITHHELD — PII fail-closed]"
        )
    return "[FORWARDED/THIRD-PARTY CONTENT WITHHELD — PII fail-closed]"
