"""
Outbound PII gate — last line of defense before any Anthropic transmission.

Legal (assignment): DriverName / DriverPhone are PII. No personal data may go to a
third party without a DPA. This module does NOT satisfy a DPA; it only reduces
known PII leakage on text payloads and blocks unsafe image uploads by default.

Production use of customer PII with Anthropic remains gated on Legal approving
the required DPA.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from meridian_claims.redact import (
    PHONE_RE,
    RECEIVER_RE,
    redact_text,
)

# Env opt-in for image vision. Default OFF — pixels cannot be safely redacted.
VISION_ALLOW_ENV = "MERIDIAN_ALLOW_POD_VISION"


class PIIGateError(RuntimeError):
    """Raised when residual PII is detected in an outbound model payload."""


@dataclass
class PIIGateReport:
    """Observable redaction outcome — never contains the original PII values."""

    status: str  # passed | blocked | skipped_dry_run | vision_blocked
    lexicon_entries: int = 0
    phones_masked: int = 0
    names_masked: int = 0
    receiver_lines_masked: int = 0
    residual_name_hits: int = 0
    residual_phone_hits: int = 0
    vision_policy: str = "not_applicable"
    # Human-readable reasons safe to show coordinators (no raw PII).
    notes: list[str] = field(default_factory=list)
    dpa_note: str = (
        "Production use of customer PII with Anthropic requires a Legal-approved "
        "DPA. This gate redacts known DriverName/DriverPhone and patterned phones "
        "before text model calls; POD image vision is blocked by default because "
        "pixels cannot be safely redacted."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PIILexicon:
    """Known PII tokens gathered from FreightPro (and similar systems of record)."""

    names: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    phone_digits: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.names) + len(self.phones)


def build_lexicon_from_rows(*rows: dict[str, Any] | None) -> PIILexicon:
    """Collect DriverName / DriverPhone from unredacted FreightPro rows."""
    names: list[str] = []
    phones: list[str] = []
    for row in rows:
        if not row:
            continue
        for key in ("DriverName", "driver_name"):
            val = (row.get(key) or "").strip()
            if val and val != "[REDACTED]":
                names.append(val)
                # Also mask first/last alone when both present (common in prose).
                parts = [p for p in re.split(r"\s+", val) if len(p) >= 3]
                names.extend(parts)
        for key in ("DriverPhone", "driver_phone"):
            val = (row.get(key) or "").strip()
            if val and val != "[REDACTED]":
                phones.append(val)
    # de-dupe
    names = _unique(names)
    phones = _unique(phones)
    digits = _unique([re.sub(r"\D", "", p) for p in phones if len(re.sub(r"\D", "", p)) >= 10])
    return PIILexicon(names=names, phones=phones, phone_digits=digits)


def redact_string(text: str, lexicon: PIILexicon) -> tuple[str, dict[str, int]]:
    """Apply patterned + lexicon redaction to a single string."""
    if not text:
        return text, {"phones": 0, "names": 0, "receivers": 0}
    phones = len(PHONE_RE.findall(text))
    receivers = len(RECEIVER_RE.findall(text))
    out = redact_text(text, extra_names=lexicon.names)
    # Count name hits by seeing how many lexicon names disappeared.
    names = 0
    lower_before = text.lower()
    lower_after = out.lower()
    for name in lexicon.names:
        if name.lower() in lower_before and name.lower() not in lower_after:
            names += 1
    return out, {"phones": phones, "names": names, "receivers": receivers}


def redact_structure(obj: Any, lexicon: PIILexicon) -> tuple[Any, dict[str, int]]:
    """Deep-redact all strings in a JSON-serializable structure."""
    totals = {"phones": 0, "names": 0, "receivers": 0}
    if isinstance(obj, str):
        text, counts = redact_string(obj, lexicon)
        for k, v in counts.items():
            totals[k] += v
        return text, totals
    if isinstance(obj, list):
        out_list = []
        for item in obj:
            cleaned, counts = redact_structure(item, lexicon)
            out_list.append(cleaned)
            for k, v in counts.items():
                totals[k] += v
        return out_list, totals
    if isinstance(obj, dict):
        # Never send raw driver fields even if somehow present.
        out_dict: dict[str, Any] = {}
        for key, val in obj.items():
            if key in ("DriverName", "DriverPhone", "driver_name", "driver_phone"):
                if val and str(val) not in ("", "[REDACTED]"):
                    out_dict[key] = "[REDACTED]"
                    totals["names"] += 1 if "name" in key.lower() else 0
                    totals["phones"] += 1 if "phone" in key.lower() else 0
                else:
                    out_dict[key] = "[REDACTED]" if val else val
                continue
            cleaned, counts = redact_structure(val, lexicon)
            out_dict[key] = cleaned
            for k, v in counts.items():
                totals[k] += v
        return out_dict, totals
    return obj, totals


def scan_residual(serialized: str, lexicon: PIILexicon) -> tuple[int, int]:
    """Residual check: full driver names + phones (lexicon digits or pattern)."""
    lower = serialized.lower()
    name_hits = sum(1 for n in lexicon.names if " " in n and n.lower() in lower)
    digit_blob = re.sub(r"\D", "", serialized)
    phone_hits = sum(1 for d in lexicon.phone_digits if d and d in digit_blob)
    phone_hits += len(PHONE_RE.findall(serialized))
    return name_hits, phone_hits


def prepare_text_payload(
    context: dict[str, Any],
    lexicon: PIILexicon,
    *,
    fail_closed: bool = True,
) -> tuple[dict[str, Any], str, PIIGateReport]:
    """
    Boundary: deep-redact context, serialize, scan. Fail closed on residual PII.

    Returns (safe_context, serialized_user_content_fragment, report).
    """
    cleaned, counts = redact_structure(context, lexicon)
    serialized = json.dumps(cleaned, indent=2, default=str)
    name_hits, phone_hits = scan_residual(serialized, lexicon)

    report = PIIGateReport(
        status="passed",
        lexicon_entries=lexicon.size,
        phones_masked=counts["phones"],
        names_masked=counts["names"],
        receiver_lines_masked=counts["receivers"],
        residual_name_hits=name_hits,
        residual_phone_hits=phone_hits,
        vision_policy="not_applicable",
        notes=[
            "Text payload passed through outbound PII gate before Anthropic.",
        ],
    )

    if name_hits or phone_hits:
        report.status = "blocked"
        report.notes.append(
            "Residual known DriverName/DriverPhone (or phone pattern) detected "
            "after redaction — model call blocked; escalate to human."
        )
        if fail_closed:
            raise PIIGateError(
                f"Outbound PII gate blocked Anthropic call "
                f"(residual_names={name_hits}, residual_phones={phone_hits})."
            )
    return cleaned, serialized, report


def vision_allowed() -> bool:
    return os.environ.get(VISION_ALLOW_ENV, "").strip() in ("1", "true", "yes", "on")


def vision_block_report() -> PIIGateReport:
    return PIIGateReport(
        status="vision_blocked",
        vision_policy="blocked_pending_dpa",
        notes=[
            f"POD image vision blocked by default ({VISION_ALLOW_ENV} not set). "
            "Scanned POD pixels may contain driver/receiver names, phones, or "
            "signatures that cannot be redacted in-image. Coordinator must open "
            "the attachment manually until Legal approves a DPA and vision is "
            "explicitly enabled."
        ],
    )


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
