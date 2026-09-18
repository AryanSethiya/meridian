"""Redact driver PII and other personal data before any third-party model call."""

from __future__ import annotations

import re
from typing import Any

# US-ish phone patterns common in dispatch email.
PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\w)"
)

# Common "Driver: Name" / "driver name is X" patterns.
DRIVER_LABEL_RE = re.compile(
    r"(?i)\b(driver(?:\s+name)?|operator)\s*[:=#-]\s*[A-Z][a-zA-Z.'\-]+(?:\s+[A-Z][a-zA-Z.'\-]+)?"
)

# POD / delivery receiver lines often contain personal names.
RECEIVER_RE = re.compile(
    r"(?i)\b(receiver|received by|signed by|signature|consignee contact)\s*[:=#-]\s*.+$",
    re.MULTILINE,
)


def redact_text(text: str, extra_names: list[str] | None = None) -> str:
    """Mask phones, labeled drivers, receiver lines, and known driver names."""
    if not text:
        return text
    out = PHONE_RE.sub("[REDACTED_PHONE]", text)
    out = DRIVER_LABEL_RE.sub(r"\1: [REDACTED_DRIVER]", out)
    out = RECEIVER_RE.sub(r"\1: [REDACTED_RECEIVER]", out)
    for name in extra_names or []:
        name = (name or "").strip()
        if len(name) < 3:
            continue
        out = re.sub(re.escape(name), "[REDACTED_DRIVER]", out, flags=re.IGNORECASE)
    return out


def redact_load_dict(load: dict[str, Any] | None) -> dict[str, Any] | None:
    if load is None:
        return None
    out = dict(load)
    for key in ("DriverName", "DriverPhone", "driver_name", "driver_phone"):
        if key in out and out[key]:
            out[key] = "[REDACTED]"
    return out


def model_safe_load(load: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Load view for Anthropic: PII redacted and commercial rate fields removed.
    Coordinators still see rates in the full action packet.
    """
    if load is None:
        return None
    out = redact_load_dict(load) or {}
    for key in ("CustomerRate", "CarrierRate", "FuelSurcharge", "CarrierID", "ShipperID"):
        out.pop(key, None)
    return out


def collect_driver_names(*sources: dict[str, Any] | None) -> list[str]:
    """Collect raw driver names from unredacted load rows for body redaction."""
    names: list[str] = []
    for src in sources:
        if not src:
            continue
        for key in ("DriverName", "driver_name"):
            val = src.get(key)
            if val and str(val) not in ("", "[REDACTED]"):
                names.append(str(val).strip())
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        k = n.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


# Back-compat alias used by older call sites / tests
def collect_driver_names_from_load(load: dict[str, Any] | None) -> list[str]:
    return collect_driver_names(load)


def assert_no_pii(text: str, forbidden_names: list[str], forbidden_phones: list[str]) -> None:
    """Raise AssertionError if known PII substrings remain (used in tests)."""
    lower = text.lower()
    for name in forbidden_names:
        if name and name.lower() in lower:
            raise AssertionError(f"PII name still present: {name!r}")
    for phone in forbidden_phones:
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) >= 10 and digits in re.sub(r"\D", "", text):
            raise AssertionError(f"PII phone still present: {phone!r}")
