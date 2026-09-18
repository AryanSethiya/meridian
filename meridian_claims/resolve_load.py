"""Extract freight identifiers and resolve them against FreightPro."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from meridian_claims.freightpro import FreightProDB
from meridian_claims.models import Identifiers, Resolution

LOAD_RE = re.compile(r"\bMF-(\d{5})\b", re.IGNORECASE)
PO_RE = re.compile(r"\bPO[-\s]?(\d{7})\b", re.IGNORECASE)
BOL_RE = re.compile(r"\bBOL\s*([A-Z]?\d{6,})\b", re.IGNORECASE)
PRO_RE = re.compile(r"\bPRO\s*(\d{7,})\b", re.IGNORECASE)
# Display-name <addr@domain> or bare addr@domain
_FROM_ANGLE_RE = re.compile(r"<([^>]+)>")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ExtractedIds:
    identifiers: Identifiers
    raw_hits: dict[str, list[str]] = field(default_factory=dict)


def extract_identifiers(text: str) -> ExtractedIds:
    """Pull MF / PO / BOL / PRO numbers from free text (subject + body)."""
    loads = _unique([f"MF-{m.group(1)}" for m in LOAD_RE.finditer(text)])
    pos = _unique([f"PO-{m.group(1)}" for m in PO_RE.finditer(text)])
    bols: list[str] = []
    for m in BOL_RE.finditer(text):
        raw = m.group(1).upper()
        bols.append(raw if raw.startswith("BOL") else f"BOL{raw}")
    bols = _unique(bols)
    pros = _unique([f"PRO{m.group(1)}" for m in PRO_RE.finditer(text)])

    def first(xs: list[str]) -> str | None:
        return xs[0] if xs else None

    return ExtractedIds(
        identifiers=Identifiers(
            load_number=first(loads),
            po=first(pos),
            bol=first(bols),
            pro=first(pros),
        ),
        raw_hits={
            "load_number": loads,
            "po": pos,
            "bol": bols,
            "pro": pros,
        },
    )


def resolve_load(db: FreightProDB, identifiers: Identifiers, raw_hits: dict[str, list[str]] | None = None) -> Resolution:
    """
    Resolve a load with preference order: LoadNumber → PO → BOL → PRO.

    After a unique match, cross-check any other provided identifiers against the
    load row. Mismatches lower confidence and are recorded in ``conflicts``.
    Multiple distinct load numbers in the email escalate to ambiguous.
    """
    raw_hits = raw_hits or {}
    multi_loads = raw_hits.get("load_number") or []
    if len(multi_loads) > 1:
        candidates = []
        for ln in multi_loads[:5]:
            rows = db.by_load_number.get(ln, [])
            if len(rows) == 1:
                candidates.append(db.enrich_load(rows[0], include_pii=False))
        return Resolution(
            status="ambiguous",
            method="LoadNumber",
            confidence=0.35,
            load=None,
            candidates=candidates,
            reason=f"Email cites multiple load numbers: {', '.join(multi_loads)}",
            conflicts=[f"Multiple load numbers in one email: {multi_loads}"],
            evidence=[f"raw_hits.load_number={multi_loads}"],
            multi_load_ids=list(multi_loads),
        )

    attempts: list[tuple[str, str | None, float]] = [
        ("LoadNumber", identifiers.load_number, 0.95),
        ("PONumber", identifiers.po, 0.85),
        ("BOLNumber", identifiers.bol, 0.85),
        ("PRONumber", identifiers.pro, 0.80),
    ]

    last_empty_reason = "No MF / PO / BOL / PRO identifiers found in the email."

    for method, value, conf in attempts:
        if not value:
            continue
        index = {
            "LoadNumber": db.by_load_number,
            "PONumber": db.by_po,
            "BOLNumber": db.by_bol,
            "PRONumber": db.by_pro,
        }[method]
        candidates = index.get(value, [])
        if len(candidates) == 1:
            enriched = db.enrich_load(candidates[0], include_pii=False)
            conflicts, evidence = _cross_check(identifiers, enriched, method, value)
            final_conf = conf
            status = "resolved"
            reason = f"Unique match on {method}={value}"
            if conflicts:
                final_conf = min(conf, 0.45)
                status = "ambiguous"
                reason = (
                    f"Matched on {method}={value} but other identifiers conflict "
                    f"with FreightPro ({len(conflicts)} conflict(s))"
                )
                return Resolution(
                    status=status,
                    method=method,
                    confidence=final_conf,
                    load=enriched,  # still attach best guess for coordinator
                    candidates=[enriched],
                    reason=reason,
                    conflicts=conflicts,
                    evidence=evidence,
                )
            evidence.insert(0, f"{method}={value} → unique row")
            return Resolution(
                status=status,
                method=method,
                confidence=final_conf,
                load=enriched,
                candidates=[],
                reason=reason,
                conflicts=[],
                evidence=evidence,
            )
        if len(candidates) > 1:
            return Resolution(
                status="ambiguous",
                method=method,
                confidence=0.4,
                load=None,
                candidates=[db.enrich_load(c, include_pii=False) for c in candidates[:5]],
                reason=f"{len(candidates)} loads share {method}={value}",
                conflicts=[],
                evidence=[f"{method}={value} → {len(candidates)} rows"],
            )
        last_empty_reason = f"No load found for {method}={value}"

    return Resolution(
        status="unresolved",
        method=None,
        confidence=0.0,
        load=None,
        candidates=[],
        reason=last_empty_reason,
        conflicts=[],
        evidence=[],
        shipper_corroboration="unavailable",
    )


def apply_shipper_corroboration(
    resolution: Resolution,
    from_addr: str | None,
    db: FreightProDB,
) -> Resolution:
    """
    Deterministic sender ↔ FreightPro shipper ContactEmail/domain check.

    - Never uses carrier name/SCAC (carrier mismatch alone must not overturn LoadNumber).
    - Conflict → escalate (ambiguous) while keeping the best-guess load attached.
    - Missing usable sender or shipper contact → mark unavailable; keep prior status.
    """
    if not resolution.load:
        evidence = list(resolution.evidence or [])
        note = "shipper_sender_corroboration=unavailable (no resolved load to check)"
        if note not in evidence:
            evidence.append(note)
        return replace(
            resolution,
            shipper_corroboration="unavailable",
            evidence=evidence,
        )

    shipper_id = (resolution.load.get("ShipperID") or "").strip()
    shipper = db.shippers.get(shipper_id, {}) if shipper_id else {}
    contact_email, contact_domain = _contact_email_parts(shipper.get("ContactEmail"))
    sender_email, sender_domain = _sender_parts(from_addr)

    evidence = list(resolution.evidence or [])
    conflicts = list(resolution.conflicts or [])

    if not sender_domain:
        evidence.append(
            "shipper_sender_corroboration=unavailable (no usable From address/domain)"
        )
        return replace(
            resolution,
            shipper_corroboration="unavailable",
            evidence=evidence,
        )
    if not contact_domain:
        evidence.append(
            "shipper_sender_corroboration=unavailable "
            "(FreightPro shipper ContactEmail missing/unusable)"
        )
        return replace(
            resolution,
            shipper_corroboration="unavailable",
            evidence=evidence,
        )

    if sender_email and contact_email and sender_email == contact_email:
        evidence.append(
            f"shipper ContactEmail corroborates sender ({_mask_email(sender_email)})"
        )
        return replace(
            resolution,
            shipper_corroboration="matched",
            evidence=evidence,
        )
    if sender_domain == contact_domain:
        evidence.append(
            f"shipper ContactEmail domain corroborates sender (@{sender_domain})"
        )
        return replace(
            resolution,
            shipper_corroboration="matched",
            evidence=evidence,
        )

    # Both sides present and disagree → escalate; do not drop the load guess.
    conflict_msg = (
        f"Email sender domain @{sender_domain} does not match FreightPro shipper "
        f"ContactEmail domain @{contact_domain} on load "
        f"{resolution.load.get('LoadNumber')}"
    )
    conflicts.append(conflict_msg)
    evidence.append("shipper_sender_corroboration=conflict")
    return replace(
        resolution,
        status="ambiguous",
        confidence=min(float(resolution.confidence or 0.0), 0.45),
        reason=(
            f"Matched on {resolution.method} but sender/shipper domain conflict "
            f"(see conflicts)"
            if resolution.method
            else "Sender/shipper domain conflict (see conflicts)"
        ),
        conflicts=conflicts,
        evidence=evidence,
        shipper_corroboration="conflict",
        load=resolution.load,
        candidates=resolution.candidates or ([resolution.load] if resolution.load else []),
    )


def _sender_parts(from_addr: str | None) -> tuple[str | None, str | None]:
    """Return (email, domain) from an RFC822 From value, or (None, None)."""
    if not from_addr or not str(from_addr).strip():
        return None, None
    raw = str(from_addr).strip()
    angle = _FROM_ANGLE_RE.search(raw)
    addr = (angle.group(1) if angle else raw).strip().strip('"').strip().lower()
    # Drop trailing junk occasionally seen in display forms
    addr = addr.split()[0] if addr else ""
    if not _EMAIL_RE.match(addr):
        return None, None
    _, _, domain = addr.partition("@")
    domain = domain.strip().lower()
    return addr, domain or None


def _contact_email_parts(contact: str | None) -> tuple[str | None, str | None]:
    if not contact or not str(contact).strip():
        return None, None
    addr = str(contact).strip().lower()
    if not _EMAIL_RE.match(addr):
        return None, None
    _, _, domain = addr.partition("@")
    return addr, domain.strip().lower() or None


def _mask_email(addr: str) -> str:
    """Avoid echoing full local-parts into evidence (no email-shaped tokens)."""
    local, _, domain = addr.partition("@")
    if not domain:
        return "[redacted-sender]"
    # Do not emit local@domain — JSON escaping of ellipsis can create residual
    # email-shaped substrings that trip the outbound PII gate.
    if len(local) <= 2:
        return f"[redacted-sender domain={domain}]"
    return f"[redacted-sender {local[0]}… domain={domain}]"


def _cross_check(
    identifiers: Identifiers,
    load: dict,
    matched_method: str,
    matched_value: str,
) -> tuple[list[str], list[str]]:
    """Compare non-matching identifier fields to the resolved load row."""
    conflicts: list[str] = []
    evidence: list[str] = [f"primary_match={matched_method}:{matched_value}"]

    checks = [
        ("LoadNumber", identifiers.load_number, "load_number"),
        ("PONumber", identifiers.po, "po"),
        ("BOLNumber", identifiers.bol, "bol"),
        ("PRONumber", identifiers.pro, "pro"),
    ]
    for field_name, extracted, _label in checks:
        if not extracted:
            continue
        if field_name == matched_method:
            continue
        actual = (load.get(field_name) or "").strip()
        if not actual:
            evidence.append(f"{field_name}: email has {extracted}, FreightPro blank")
            continue
        if actual.upper() != extracted.upper():
            conflicts.append(
                f"Email {field_name}={extracted} does not match FreightPro "
                f"{field_name}={actual} on load {load.get('LoadNumber')}"
            )
        else:
            evidence.append(f"{field_name} corroborates ({extracted})")
    return conflicts, evidence


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
