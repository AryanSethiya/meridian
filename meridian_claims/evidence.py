"""Deterministic discrepancy / corroboration checks (no LLM)."""

from __future__ import annotations

import re
from typing import Any, Iterable

from meridian_claims.models import Identifiers, PodResult
from meridian_claims.resolve_load import extract_identifiers


FORBIDDEN_DRAFT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bclaim (is )?approved\b",
        r"\bapproved your claim\b",
        r"\bwe will pay\b",
        r"\bpayment (will be|has been) (issued|sent)\b",
        r"\bclaim (is )?denied\b",
        r"\bwe deny\b",
        r"\baccept(?:s|ing)? liability\b",
        r"\bwe are liable\b",
        r"\bsettlement (of|for)\b",
    ]
]

# Brokerage rates are commercial facts for coordinators — not claim/settlement amounts.
# Only fields listed here may be echoed as trusted dollars in a draft.
APPROVED_FREIGHTPRO_MONEY_FIELDS: tuple[str, ...] = ()

# $1,234.56 or 1234.56 USD / dollars (require currency marker — bare integers are not money)
_MONEY_RE = re.compile(
    r"""
    (?:
        \$\s*(?P<dollar_whole>\d{1,3}(?:,\d{3})*|\d+)(?:\.(?P<dollar_frac>\d{2}))?
        |
        (?P<usd_whole>\d{1,3}(?:,\d{3})*|\d+)(?:\.(?P<usd_frac>\d{2}))?\s*(?:USD|dollars?)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

SAFE_DRAFT_INVENTED_AMOUNT = (
    "Thank you for contacting Meridian Freight claims.\n\n"
    "A coordinator is reviewing your claim. An automated draft attempted to cite a "
    "dollar amount that was not present in the inbound email or approved FreightPro "
    "claim facts, so that text was withheld. A human will follow up with next steps.\n\n"
    "— Meridian Freight Claims (draft for human review — not sent)"
)


def filter_draft(draft: str) -> tuple[str, list[str]]:
    """Strip/flag liability language the model must not assert."""
    if not draft:
        return draft, []
    warnings: list[str] = []
    out = draft
    for pat in FORBIDDEN_DRAFT_PATTERNS:
        if pat.search(out):
            warnings.append(f"Removed forbidden draft language matching {pat.pattern}")
            out = pat.sub("[REVIEW REQUIRED — liability language removed]", out)
    return out, warnings


def extract_monetary_cents(text: str | None) -> set[int]:
    """Return monetary amounts in integer cents found in text."""
    if not text:
        return set()
    found: set[int] = set()
    for m in _MONEY_RE.finditer(text):
        whole = m.group("dollar_whole") or m.group("usd_whole") or "0"
        frac = m.group("dollar_frac") or m.group("usd_frac") or "00"
        whole_n = int(whole.replace(",", ""))
        found.add(whole_n * 100 + int(frac))
    return found


def trusted_monetary_cents(
    *,
    email_text: str | None = None,
    load: dict[str, Any] | None = None,
    extra_texts: Iterable[str] | None = None,
) -> set[int]:
    """
    Amounts the draft is allowed to repeat.

    Sources: inbound email, optional structured claim strings, and explicitly
    approved FreightPro money fields only (rates are excluded by default).
    """
    trusted: set[int] = set()
    trusted |= extract_monetary_cents(email_text)
    for t in extra_texts or []:
        trusted |= extract_monetary_cents(t)
    if load:
        for key in APPROVED_FREIGHTPRO_MONEY_FIELDS:
            raw = load.get(key)
            if raw is None or raw == "":
                continue
            # Normalize bare numerics from CSV into a $-prefixed form for parsing.
            s = str(raw).strip()
            if re.fullmatch(r"\d+(?:\.\d{1,2})?", s):
                s = f"${s}"
            trusted |= extract_monetary_cents(s)
    return trusted


def validate_draft_amounts(
    draft: str,
    trusted: set[int],
) -> tuple[str, list[str]]:
    """
    Block drafts that introduce dollar amounts absent from trusted sources.

    On failure, replace the entire draft with a safe fallback (do not send/use
    the invented figures) and return escalation warnings.
    """
    if not draft:
        return draft, []
    draft_amounts = extract_monetary_cents(draft)
    if not draft_amounts:
        return draft, []
    invented = sorted(a for a in draft_amounts if a not in trusted)
    if not invented:
        return draft, []
    shown = ", ".join(f"${c / 100:.2f}" for c in invented)
    warning = (
        f"Draft introduced monetary amount(s) not in trusted sources ({shown}); "
        "replaced with safe fallback — human review required."
    )
    return SAFE_DRAFT_INVENTED_AMOUNT, [warning]


def sanitize_claim_draft(
    draft: str,
    *,
    trusted_cents: set[int] | None = None,
    email_text: str | None = None,
    load: dict[str, Any] | None = None,
    extra_texts: Iterable[str] | None = None,
) -> tuple[str, list[str]]:
    """Liability filter + trusted monetary-amount gate (deterministic)."""
    out, warnings = filter_draft(draft)
    trusted = (
        trusted_cents
        if trusted_cents is not None
        else trusted_monetary_cents(
            email_text=email_text, load=load, extra_texts=extra_texts
        )
    )
    out, money_warnings = validate_draft_amounts(out, trusted)
    warnings.extend(money_warnings)
    return out, warnings


def pod_vs_load_discrepancies(pod: PodResult, load: dict | None) -> list[str]:
    """Compare POD excerpt identifiers to FreightPro load (deterministic)."""
    if not load or not pod.excerpt:
        return []
    discs: list[str] = []
    ids = extract_identifiers(pod.excerpt).identifiers
    if ids.load_number and load.get("LoadNumber") and ids.load_number != load["LoadNumber"]:
        discs.append(
            f"POD cites {ids.load_number} but resolved load is {load['LoadNumber']}"
        )
    if ids.bol and load.get("BOLNumber") and ids.bol.upper() != load["BOLNumber"].upper():
        discs.append(
            f"POD BOL {ids.bol} does not match FreightPro BOL {load['BOLNumber']}"
        )
    if ids.po and load.get("PONumber") and ids.po.upper() != load["PONumber"].upper():
        discs.append(
            f"POD PO {ids.po} does not match FreightPro PO {load['PONumber']}"
        )
    return discs


def email_vs_load_corroboration(email_text: str, load: dict | None) -> list[str]:
    """Soft mismatches between email prose and FreightPro (equipment, pickup date, DNU)."""
    if not load:
        return []
    discs: list[str] = []
    text_l = email_text.lower()

    equip = (load.get("EquipmentType") or "").strip()
    if equip:
        mentioned = None
        if re.search(r"\breefer\b", text_l):
            mentioned = "Reefer"
        elif re.search(r"\bflatbed\b", text_l):
            mentioned = "Flatbed"
        elif re.search(r"\bvan\b", text_l):
            mentioned = "Van"
        if mentioned and mentioned.lower() != equip.lower():
            discs.append(
                f"Email mentions equipment {mentioned} but FreightPro has {equip}"
            )

    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", email_text)
    pickup = (load.get("PickupDate") or "").strip()
    if dates and pickup:
        if re.search(r"pickup[^\n]{0,40}" + re.escape(dates[0]), text_l):
            if dates[0] != pickup:
                discs.append(
                    f"Email pickup date {dates[0]} differs from FreightPro PickupDate {pickup}"
                )

    if load.get("carrier_status") == "Do Not Use":
        discs.append("Assigned carrier status is Do Not Use — escalate before any reply")

    return discs


def damage_vs_clean_pod(email_text: str, pod: PodResult, load: dict | None) -> list[str]:
    discs: list[str] = []
    body_l = email_text.lower()
    alleges_loss = any(w in body_l for w in ("damage", "shortage", "short", "refus"))
    if load and load.get("PODReceived") == "Y" and "damage" in body_l:
        discs.append(
            "Shipper alleges damage but FreightPro PODReceived=Y "
            "(clean POD on file — verify exceptions)."
        )
    if pod.mode == "text" and pod.excerpt and alleges_loss:
        low = pod.excerpt.lower()
        if "no exception" in low or "seals intact" in low:
            discs.append(
                "POD text reports clean delivery / no exceptions, "
                "but the email alleges loss or damage."
            )
    return discs


def build_observed_block(
    *,
    identifiers: Identifiers,
    resolution,
    carrier,
    pod: PodResult,
    seed_discrepancies: list[str],
) -> dict:
    """Facts from code/retrieval — not model interpretation."""
    return {
        "identifiers": {
            "load_number": identifiers.load_number,
            "po": identifiers.po,
            "bol": identifiers.bol,
            "pro": identifiers.pro,
        },
        "resolution": {
            "status": resolution.status,
            "method": resolution.method,
            "confidence": resolution.confidence,
            "reason": resolution.reason,
            "conflicts": list(resolution.conflicts or []),
            "evidence": list(resolution.evidence or []),
            "multi_load_ids": list(resolution.multi_load_ids or []),
            "load_number": (resolution.load or {}).get("LoadNumber"),
            "lane": (resolution.load or {}).get("lane"),
            "status_etl": (resolution.load or {}).get("Status"),
            "pod_received": (resolution.load or {}).get("PODReceived"),
            "commodity": (resolution.load or {}).get("Commodity"),
            "equipment": (resolution.load or {}).get("EquipmentType"),
            "shipper": (resolution.load or {}).get("shipper_name"),
            "carrier": (resolution.load or {}).get("carrier_legal_name"),
            "carrier_mc": (resolution.load or {}).get("carrier_mc"),
            "carrier_scac": (resolution.load or {}).get("carrier_scac"),
        },
        "carrier_mention": {
            "email_mention": carrier.email_mention,
            "matched_legal_name": carrier.legal_name,
            "mc": carrier.mc,
            "scac": carrier.scac,
            "status": carrier.status,
        },
        "pod": {
            "filename": pod.filename,
            "mode": pod.mode,
            "status": pod.status,
            "findings": pod.findings,
        },
        "deterministic_discrepancies": list(seed_discrepancies),
    }
