"""
Coordinator-facing claim analysis layout.

Separates sources of truth so the model cannot silently invent missing facts:
  1. freightpro_facts  — retrieved from FreightPro only
  2. email_facts       — extracted from the email only
  3. pod_facts         — extracted from the POD only (or unknown)
  4. comparisons       — deterministic discrepancies / corroborations
  5. unknowns          — required facts that are null + why HITL
  6. llm_interpretation — model judgment only (validated against known IDs)
  7. draft_response    — suggested reply (never auto-sent)
"""

from __future__ import annotations

import re
from typing import Any

from meridian_claims.freightpro import freightpro_source_meta
from meridian_claims.models import CarrierInfo, Identifiers, PodResult, Resolution
from meridian_claims.resolve_load import extract_identifiers

_LOAD_RE = re.compile(r"\bMF-\d{5}\b", re.IGNORECASE)
_PO_RE = re.compile(r"\bPO-\d{7}\b", re.IGNORECASE)
_BOL_RE = re.compile(r"\bBOL\d+\b", re.IGNORECASE)


def _v(value: Any) -> Any:
    """Normalize empty strings to null for coordinator clarity."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def build_freightpro_facts(resolution: Resolution) -> dict[str, Any]:
    source = freightpro_source_meta()
    load = resolution.load or {}
    if resolution.status == "unresolved" or not load:
        return {
            "available": False,
            **source,
            "resolution_status": resolution.status,
            "resolution_method": resolution.method,
            "resolution_confidence": resolution.confidence,
            "reason": resolution.reason,
            "load_number": None,
            "status": None,
            "status_recorded": None,
            "lane": None,
            "origin_city": None,
            "dest_city": None,
            "pickup_date": None,
            "delivery_date": None,
            "equipment": None,
            "commodity": None,
            "po_number": None,
            "bol_number": None,
            "pro_number": None,
            "pod_received": None,
            "shipper_name": None,
            "carrier_legal_name": None,
            "carrier_mc": None,
            "carrier_scac": None,
            "carrier_status": None,
            "evidence": list(resolution.evidence or []),
            "conflicts": list(resolution.conflicts or []),
            "shipper_corroboration": resolution.shipper_corroboration,
            "candidate_load_numbers": [
                c.get("LoadNumber") for c in (resolution.candidates or []) if c.get("LoadNumber")
            ],
        }
    status = _v(load.get("Status"))
    return {
        "available": True,
        **source,
        "resolution_status": resolution.status,
        "resolution_method": resolution.method,
        "resolution_confidence": resolution.confidence,
        "reason": resolution.reason,
        "load_number": _v(load.get("LoadNumber")),
        "status": status,
        "status_recorded": status,  # alias: recorded in snapshot, not live tracking
        "lane": _v(load.get("lane")),
        "origin_city": _v(load.get("OriginCity")),
        "dest_city": _v(load.get("DestCity")),
        "pickup_date": _v(load.get("PickupDate")),
        "delivery_date": _v(load.get("DeliveryDate")),
        "equipment": _v(load.get("EquipmentType")),
        "commodity": _v(load.get("Commodity")),
        "po_number": _v(load.get("PONumber")),
        "bol_number": _v(load.get("BOLNumber")),
        "pro_number": _v(load.get("PRONumber")),
        "pod_received": _v(load.get("PODReceived")),
        "shipper_name": _v(load.get("shipper_name")),
        "carrier_legal_name": _v(load.get("carrier_legal_name")),
        "carrier_mc": _v(load.get("carrier_mc")),
        "carrier_scac": _v(load.get("carrier_scac")),
        "carrier_status": _v(load.get("carrier_status")),
        "evidence": list(resolution.evidence or []),
        "conflicts": list(resolution.conflicts or []),
        "shipper_corroboration": resolution.shipper_corroboration,
        "candidate_load_numbers": [
            c.get("LoadNumber") for c in (resolution.candidates or []) if c.get("LoadNumber")
        ],
    }


def build_email_facts(
    *,
    identifiers: Identifiers,
    carrier: CarrierInfo,
    subject: str,
    claim_type_heuristic: str,
) -> dict[str, Any]:
    return {
        "subject": subject,
        "claimed_intent_heuristic": claim_type_heuristic,
        "identifiers": {
            "load_number": identifiers.load_number,
            "po": identifiers.po,
            "bol": identifiers.bol,
            "pro": identifiers.pro,
        },
        "carrier_mention": carrier.email_mention,
        # Free-text allegation is not a structured FreightPro fact — coordinators read draft/summary.
        "note": "Structured IDs only here; narrative allegation lives under llm_interpretation / draft.",
    }


def build_pod_facts(pod: PodResult) -> dict[str, Any]:
    """POD-sourced facts only. Unreadable/missing → explicit nulls + reason."""
    base: dict[str, Any] = {
        "available": pod.status == "readable" and bool(pod.excerpt),
        "filename": pod.filename,
        "extraction_method": pod.mode,
        "status": pod.status,
        "findings": pod.findings,
        "load_number": None,
        "po_number": None,
        "bol_number": None,
        "condition_summary": None,
        "exceptions_noted": None,
        "excerpt_redacted": None,
        "unavailable_reason": None,
    }
    if pod.status in ("no_attachment", "missing") or pod.mode == "none":
        base["unavailable_reason"] = "No POD PDF attached to the email."
        return base
    if pod.status in ("unreadable", "invalid") or pod.mode in (
        "blank_page",
        "vision_blocked",
        "unreadable",
        "invalid",
    ):
        base["unavailable_reason"] = pod.findings or f"POD not usable (mode={pod.mode})."
        return base
    if not pod.excerpt:
        base["unavailable_reason"] = "POD present but no extractable text."
        return base

    ids = extract_identifiers(pod.excerpt).identifiers
    low = pod.excerpt.lower()
    exceptions_noted: bool | None = None
    if "no exception" in low or "seals intact" in low:
        exceptions_noted = False
        condition = "clean / no exceptions noted (per POD text)"
    elif "damage" in low or "exception" in low or "shortage" in low:
        exceptions_noted = True
        condition = "exceptions or damage language present in POD text"
    else:
        condition = "condition not clearly stated in extracted text"

    base.update(
        {
            "available": True,
            "load_number": ids.load_number,
            "po_number": ids.po,
            "bol_number": ids.bol,
            "condition_summary": condition,
            "exceptions_noted": exceptions_noted,
            "excerpt_redacted": pod.excerpt[:1500],
            "unavailable_reason": None,
        }
    )
    return base


def build_comparisons(
    *,
    discrepancies: list[str],
    freightpro: dict[str, Any],
    email: dict[str, Any],
    pod: dict[str, Any],
) -> dict[str, Any]:
    corroborations: list[str] = []
    email_ids = email.get("identifiers") or {}
    if freightpro.get("available"):
        for key, fp_key in (
            ("load_number", "load_number"),
            ("po", "po_number"),
            ("bol", "bol_number"),
        ):
            ev = email_ids.get(key)
            fv = freightpro.get(fp_key)
            if ev and fv and str(ev).upper() == str(fv).upper():
                corroborations.append(f"Email {key} matches FreightPro ({ev})")
        if pod.get("available"):
            for key, label in (
                ("load_number", "load_number"),
                ("po_number", "po_number"),
                ("bol_number", "bol_number"),
            ):
                pv = pod.get(key)
                fv = freightpro.get(label if label != "load_number" else "load_number")
                if key == "load_number":
                    fv = freightpro.get("load_number")
                elif key == "po_number":
                    fv = freightpro.get("po_number")
                elif key == "bol_number":
                    fv = freightpro.get("bol_number")
                if pv and fv and str(pv).upper() == str(fv).upper():
                    corroborations.append(f"POD {key} matches FreightPro ({pv})")
    return {
        "discrepancies": list(discrepancies),
        "corroborations": corroborations,
        "source": "deterministic",
    }


def build_unknowns(
    *,
    freightpro: dict[str, Any],
    email: dict[str, Any],
    pod: dict[str, Any],
    discrepancies: list[str],
) -> list[dict[str, Any]]:
    """Required facts that are null/unavailable — each explains HITL need."""
    unknowns: list[dict[str, Any]] = []

    if not freightpro.get("available"):
        unknowns.append(
            {
                "field": "freightpro_load",
                "value": None,
                "reason": freightpro.get("reason")
                or "Could not uniquely resolve a FreightPro load.",
                "requires_human": True,
            }
        )
    else:
        if freightpro.get("resolution_status") == "ambiguous":
            unknowns.append(
                {
                    "field": "freightpro_load_identity",
                    "value": None,
                    "reason": freightpro.get("reason")
                    or "Load identity ambiguous; see conflicts/candidates.",
                    "requires_human": True,
                }
            )
        for field, label in (
            ("pro_number", "PRO number"),
            ("pod_received", "FreightPro PODReceived flag"),
        ):
            if freightpro.get(field) is None:
                unknowns.append(
                    {
                        "field": f"freightpro.{field}",
                        "value": None,
                        "reason": f"{label} not present on the FreightPro row.",
                        "requires_human": False,
                    }
                )

    if not (email.get("identifiers") or {}).get("load_number"):
        if not freightpro.get("available"):
            unknowns.append(
                {
                    "field": "email.identifiers.load_number",
                    "value": None,
                    "reason": "Email did not cite an MF load number.",
                    "requires_human": True,
                }
            )

    if not pod.get("available"):
        unknowns.append(
            {
                "field": "pod_contents",
                "value": None,
                "reason": pod.get("unavailable_reason") or "POD facts unavailable.",
                "requires_human": True,
            }
        )
    else:
        if pod.get("exceptions_noted") is None:
            unknowns.append(
                {
                    "field": "pod.exceptions_noted",
                    "value": None,
                    "reason": "POD text did not clearly state whether exceptions were noted.",
                    "requires_human": True,
                }
            )

    if any("damage" in d.lower() and "clean" in d.lower() for d in discrepancies):
        unknowns.append(
            {
                "field": "delivery_condition_ground_truth",
                "value": None,
                "reason": "Email alleges loss/damage but POD/FreightPro indicate a clean delivery — condition is disputed.",
                "requires_human": True,
            }
        )

    return unknowns


def known_identifier_set(
    freightpro: dict[str, Any],
    email: dict[str, Any],
    pod: dict[str, Any],
) -> set[str]:
    """IDs the model is allowed to cite — anything else is invention."""
    allowed: set[str] = set()
    for source in (
        email.get("identifiers") or {},
        {
            "load_number": freightpro.get("load_number"),
            "po": freightpro.get("po_number"),
            "bol": freightpro.get("bol_number"),
            "pro": freightpro.get("pro_number"),
        },
        {
            "load_number": pod.get("load_number"),
            "po": pod.get("po_number"),
            "bol": pod.get("bol_number"),
        },
    ):
        for v in source.values():
            if v:
                allowed.add(str(v).upper())
    for ln in freightpro.get("candidate_load_numbers") or []:
        if ln:
            allowed.add(str(ln).upper())
    return allowed


def validate_llm_against_known_facts(
    llm_data: dict[str, Any],
    allowed_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """
    Strip/flag invented MF/PO/BOL identifiers in model prose.
    Returns (cleaned_interpretation_fields, validation_warnings).
    """
    warnings: list[str] = []
    fields = {
        "claim_type": str(llm_data.get("claim_type") or "other"),
        "confidence": float(llm_data.get("confidence") or 0.5),
        "alleged_facts": str(llm_data.get("alleged_facts") or ""),
        "summary": str(llm_data.get("summary") or ""),
        "draft_reply": str(llm_data.get("draft_reply") or ""),
        "discrepancies": [str(x) for x in (llm_data.get("discrepancies") or [])],
        "needs_human_reasons": [str(x) for x in (llm_data.get("needs_human_reasons") or [])],
    }

    def _scrub(text: str, label: str) -> str:
        out = text
        for pattern in (_LOAD_RE, _PO_RE, _BOL_RE):
            for match in pattern.findall(text):
                if match.upper() not in allowed_ids:
                    warnings.append(
                        f"Removed invented identifier {match} from {label} "
                        "(not present in email/FreightPro/POD facts)."
                    )
                    out = re.sub(re.escape(match), "[UNKNOWN_ID]", out, flags=re.IGNORECASE)
        return out

    for key in ("alleged_facts", "summary", "draft_reply"):
        fields[key] = _scrub(fields[key], key)
    fields["discrepancies"] = [_scrub(d, "discrepancies") for d in fields["discrepancies"]]
    return fields, warnings


def build_analysis_bundle(
    *,
    freightpro: dict[str, Any],
    email: dict[str, Any],
    pod: dict[str, Any],
    comparisons: dict[str, Any],
    unknowns: list[dict[str, Any]],
    llm_interpretation: dict[str, Any],
    draft_response: dict[str, Any],
    human_review: dict[str, Any],
) -> dict[str, Any]:
    return {
        "what_we_know": {
            "from_freightpro": freightpro,
            "from_email": email,
            "from_pod": pod,
        },
        "what_evidence_supports_it": {
            "freightpro_evidence": freightpro.get("evidence") or [],
            "corroborations": comparisons.get("corroborations") or [],
        },
        "what_is_inconsistent": {
            "discrepancies": comparisons.get("discrepancies") or [],
            "freightpro_conflicts": freightpro.get("conflicts") or [],
        },
        "what_is_still_unknown": unknowns,
        "what_ai_suggests": {
            "interpretation": llm_interpretation,
            "draft_response": draft_response,
        },
        "why_human_review": human_review,
        # Flat aliases for simpler consumers
        "freightpro_facts": freightpro,
        "email_facts": email,
        "pod_facts": pod,
        "comparisons": comparisons,
        "unknowns": unknowns,
        "llm_interpretation": llm_interpretation,
        "draft_response": draft_response,
        "human_review": human_review,
    }
