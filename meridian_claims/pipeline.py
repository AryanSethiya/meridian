"""End-to-end claims triage pipeline for one .eml file."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from meridian_claims.agent import (
    MissingAPIKeyError,
    ModelResponseError,
    classify_and_draft,
)
from meridian_claims.analysis import (
    build_analysis_bundle,
    build_comparisons,
    build_email_facts,
    build_freightpro_facts,
    build_pod_facts,
    build_unknowns,
    known_identifier_set,
    validate_llm_against_known_facts,
)
from meridian_claims.email_parser import parse_eml
from meridian_claims.evidence import (
    build_observed_block,
    damage_vs_clean_pod,
    email_vs_load_corroboration,
    sanitize_claim_draft,
    pod_vs_load_discrepancies,
)
from meridian_claims.forward_mail import (
    analyze_forwarded_content,
    model_safe_forward_body,
)
from meridian_claims.freightpro import FreightProDB
from meridian_claims.models import (
    ActionPacket,
    CarrierInfo,
    Classification,
    PodResult,
)
from meridian_claims.observability import build_decision_record
from meridian_claims.pii_gate import (
    PIIGateError,
    PIIGateReport,
    build_lexicon_from_rows,
)
from meridian_claims.pod import decide_pod_attachments, process_pod_attachment
from meridian_claims.redact import (
    model_safe_load,
    redact_load_dict,
    redact_text,
)
from meridian_claims.render import print_summary, write_packet
from meridian_claims.resolve_load import (
    apply_shipper_corroboration,
    extract_identifiers,
    resolve_load,
)

DEFAULT_ASSUMPTIONS = [
    "FreightPro facts are from the assignment snapshot (source=FreightPro snapshot, "
    "snapshot_date=2026-08-26). Reporting data can be ~14h behind live ops; "
    "Status is recorded status, not live tracking. No per-field as_of timestamps.",
    "Read-only FreightPro access — no claim file is written back by this tool.",
    "Draft replies are for human review only; nothing is auto-sent.",
    "DriverName/DriverPhone are Legal PII. Text payloads are redacted + scanned at the "
    "outbound gate before Anthropic; residual known PII fails closed.",
    "POD image vision is blocked by default (pixels cannot be safely redacted). "
    "Production Anthropic use of customer PII still requires a Legal-approved DPA — "
    "redaction is mitigation, not a DPA substitute.",
    "Carrier LegalName is free text; MCNumber/SCAC are preferred for identity.",
    "observed.* fields are deterministic; interpretation.* fields are model-generated.",
]


def process_email(
    eml_path: Path,
    *,
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    use_llm: bool = True,
    use_vision: bool = True,
) -> ActionPacket:
    """
    Parse email → resolve load → process POD → (optional) LLM classify/draft → packet.
    """
    t0 = time.perf_counter()
    eml_path = Path(eml_path)
    repo_root = Path(__file__).resolve().parent.parent
    env_out = os.environ.get("MERIDIAN_OUTPUT_DIR", "").strip()
    if output_dir is not None:
        out_root = Path(output_dir)
    elif env_out:
        out_root = Path(env_out)
    else:
        out_root = repo_root / "output"
    attach_dir = out_root / "attachments" / eml_path.stem

    parsed = parse_eml(eml_path, attachment_dir=attach_dir)
    db = FreightProDB.load(data_dir)

    blob = f"{parsed.subject}\n{parsed.body}"
    extracted = extract_identifiers(blob)
    resolution = resolve_load(db, extracted.identifiers, raw_hits=extracted.raw_hits)
    # Deterministic sender ↔ shipper ContactEmail/domain check (not carrier).
    resolution = apply_shipper_corroboration(resolution, parsed.from_addr, db)

    # Raw CSV rows for PII lexicon (before enrich redacts DriverName).
    raw_rows: list = []
    if resolution.load and resolution.load.get("LoadNumber"):
        row = db.raw_load_by_number(resolution.load["LoadNumber"])
        if row:
            raw_rows.append(row)
    for cand in resolution.candidates or []:
        ln = cand.get("LoadNumber")
        if ln:
            row = db.raw_load_by_number(ln)
            if row:
                raw_rows.append(row)
    for ln in resolution.multi_load_ids or []:
        row = db.raw_load_by_number(ln)
        if row:
            raw_rows.append(row)
    lexicon = build_lexicon_from_rows(*raw_rows)

    # Carrier mention from email free text via FreightPro (SCAC → MC → LegalName).
    mention_hit = db.find_carrier_mention(blob)
    email_mention = None
    if mention_hit:
        email_mention = (
            (mention_hit.get("SCAC") or "").strip()
            or (mention_hit.get("MCNumber") or "").strip()
            or (mention_hit.get("LegalName") or "").strip()
            or None
        )

    carrier = CarrierInfo(
        legal_name=(resolution.load or {}).get("carrier_legal_name")
        or (mention_hit or {}).get("LegalName"),
        mc=(resolution.load or {}).get("carrier_mc") or (mention_hit or {}).get("MCNumber"),
        scac=(resolution.load or {}).get("carrier_scac") or (mention_hit or {}).get("SCAC"),
        status=(resolution.load or {}).get("carrier_status")
        or (mention_hit or {}).get("Status"),
        email_mention=email_mention,
    )

    # POD / attachment inventory (deterministic; no LLM classification)
    attachment_decision = decide_pod_attachments(parsed.attachments)
    attachments_audit = {
        "inventory": list(attachment_decision.get("inventory") or []),
        "pod_candidates": list(attachment_decision.get("pod_candidates") or []),
        "selection_status": attachment_decision.get("selection_status"),
        "selected_filename": (attachment_decision.get("selected") or {}).get("filename"),
        "unprocessed": list(attachment_decision.get("unprocessed") or []),
        "reason": attachment_decision.get("reason"),
    }

    pod = PodResult(status="no_attachment", mode="none", findings="No PDF attachment.")
    selected: tuple[str, bytes] | None = None
    sel_status = attachment_decision.get("selection_status")
    if sel_status == "selected" and attachment_decision.get("selected"):
        sel = attachment_decision["selected"]
        selected = (sel["filename"], sel["data"])
        pod = process_pod_attachment(
            sel["filename"], sel["data"], use_vision=use_vision and use_llm
        )
    elif sel_status == "ambiguous_multiple_pods":
        pod = PodResult(
            filename=None,
            mode="none",
            excerpt="",
            findings=attachment_decision.get("reason")
            or "Multiple POD candidates; none processed.",
            status="unreadable",
        )
    elif attachment_decision.get("inventory") and sel_status == "no_pod":
        non_pdf = [
            row["filename"]
            for row in attachment_decision["inventory"]
            if not row.get("is_pdf")
        ]
        if non_pdf:
            pod = PodResult(
                filename=non_pdf[0],
                mode="invalid",
                findings=(
                    f"Non-PDF attachment(s) present ({', '.join(non_pdf)}); "
                    "open manually. No POD PDF selected."
                ),
                status="invalid",
            )

    load = resolution.load
    discrepancies: list[str] = []
    needs_human_reasons = [
        "Marcus policy: no AI-written reply to a shipper without a human read.",
    ]

    # Identifier conflicts / multi-load
    for c in resolution.conflicts or []:
        discrepancies.append(c)
        needs_human_reasons.append(c)
    if resolution.multi_load_ids:
        needs_human_reasons.append(
            "Multiple load numbers cited — split or confirm which claim to open."
        )
    # Ambiguous/unresolved loads may still carry a best-guess for HITL context,
    # but only status=="resolved" is authoritative for any future automation.
    if not resolution.is_authoritative_for_action():
        needs_human_reasons.append(
            f"Load identity {resolution.status}: {resolution.reason}"
        )

    # Deterministic evidence checks
    discrepancies.extend(damage_vs_clean_pod(blob, pod, load))
    for d in pod_vs_load_discrepancies(pod, load):
        if d not in discrepancies:
            # avoid duplicate "clean delivery" lines
            if "clean delivery" in d and any("clean delivery" in x for x in discrepancies):
                continue
            discrepancies.append(d)
    for d in email_vs_load_corroboration(blob, load):
        if d not in discrepancies:
            discrepancies.append(d)

    if sel_status == "ambiguous_multiple_pods":
        needs_human_reasons.append(
            attachment_decision.get("reason")
            or "Multiple POD candidates; human must choose which attachment to use."
        )
        if attachment_decision.get("reason") not in discrepancies:
            discrepancies.append(str(attachment_decision.get("reason")))

    if pod.status in ("unreadable", "invalid") and selected:
        needs_human_reasons.append(
            "POD attachment present but unreadable/invalid — open manually."
        )
        if "Attached POD could not be read automatically." not in discrepancies:
            discrepancies.append("Attached POD could not be read automatically.")

    if not selected and sel_status == "no_pod" and any(
        w in blob.lower()
        for w in ("claim", "damage", "shortage", "refused", "os&d", "osd")
    ):
        needs_human_reasons.append("No POD/evidence attachment on a claims email.")

    if load and load.get("carrier_status") == "Do Not Use":
        needs_human_reasons.append("Carrier marked Do Not Use.")

    # Multi-intent: keep claims path; do not execute tracking/invoice/quote side requests.
    secondary_intents = _secondary_ops_intents(blob)
    if secondary_intents:
        reason = (
            "multiple intents: claims plus "
            + ", ".join(secondary_intents)
            + " — continue claims investigation only; do not execute the secondary request."
        )
        needs_human_reasons.append(reason)
        if reason not in discrepancies:
            discrepancies.append(reason)

    # Always require human for anything that looks like a claim path.
    needs_human = True

    # Forward / third-party content (markers only; do not parse full threads).
    forward_info = analyze_forwarded_content(parsed.subject, parsed.body)
    if forward_info.detected and forward_info.escalation_reason:
        needs_human_reasons.append(forward_info.escalation_reason)
        if forward_info.escalation_reason not in discrepancies:
            discrepancies.append(forward_info.escalation_reason)

    # Redact before model — lexicon from FreightPro; gate re-scans at the boundary.
    redacted_body = redact_text(parsed.body, extra_names=lexicon.names)
    redacted_subject = redact_text(parsed.subject, extra_names=lexicon.names)
    redacted_pod_excerpt = redact_text(pod.excerpt, extra_names=lexicon.names)
    redacted_preamble = redact_text(forward_info.preamble or "", extra_names=lexicon.names)
    model_body = model_safe_forward_body(
        forward_info,
        redacted_preamble if forward_info.detected else redacted_body,
    )
    redacted_load = redact_load_dict(load)
    safe_load = model_safe_load(load)
    safe_candidates = [model_safe_load(c) for c in (resolution.candidates or [])]

    # Persist redacted excerpt on POD object for pod_facts / packet
    pod_for_facts = PodResult(
        filename=pod.filename,
        mode=pod.mode,
        excerpt=redacted_pod_excerpt,
        findings=pod.findings,
        status=pod.status,
    )

    heuristic_intent = _heuristic_claim_type(parsed.subject + " " + parsed.body)
    freightpro_facts = build_freightpro_facts(resolution)
    email_facts = build_email_facts(
        identifiers=extracted.identifiers,
        carrier=carrier,
        subject=redacted_subject,
        claim_type_heuristic=heuristic_intent,
    )
    email_facts["secondary_intents"] = list(secondary_intents)
    email_facts["forward"] = {
        "detected": forward_info.detected,
        "markers": list(forward_info.markers),
        "sender_trust": forward_info.sender_trust,
        "third_party_block_present": forward_info.third_party_block_present,
        "safe_redaction_guaranteed": forward_info.safe_redaction_guaranteed,
        "model_policy": forward_info.model_policy,
        "escalation_reason": forward_info.escalation_reason,
    }
    pod_facts = build_pod_facts(pod_for_facts)
    comparisons = build_comparisons(
        discrepancies=discrepancies,
        freightpro=freightpro_facts,
        email=email_facts,
        pod=pod_facts,
    )
    unknowns = build_unknowns(
        freightpro=freightpro_facts,
        email=email_facts,
        pod=pod_facts,
        discrepancies=discrepancies,
    )
    for u in unknowns:
        if u.get("requires_human") and u.get("reason"):
            reason = f"Unknown: {u['field']} — {u['reason']}"
            if reason not in needs_human_reasons:
                needs_human_reasons.append(reason)

    allowed_ids = known_identifier_set(freightpro_facts, email_facts, pod_facts)

    observed = build_observed_block(
        identifiers=extracted.identifiers,
        resolution=resolution,
        carrier=carrier,
        pod=pod_for_facts,
        seed_discrepancies=discrepancies,
    )
    # Model sees separated facts only — not a license to invent nulls.
    observed_for_model = {
        "freightpro_facts": freightpro_facts,
        "email_facts": {
            **email_facts,
            "body_redacted": model_body,
        },
        "pod_facts": {
            **{k: v for k, v in pod_facts.items() if k != "excerpt_redacted"},
            "excerpt_redacted": pod_facts.get("excerpt_redacted"),
        },
        "comparisons": comparisons,
        "unknowns": unknowns,
        "resolution_load_for_model": safe_load,
        "resolution_candidates_for_model": safe_candidates,
        "email_meta": {
            "id": parsed.email_id,
            "date": parsed.date,
            # Deterministic result only — raw From/To addresses are not sent to Anthropic.
            "sender_corroboration": resolution.shipper_corroboration,
            "sender_trust": forward_info.sender_trust,
            "forward_detected": forward_info.detected,
        },
        "instruction": (
            "Do not invent values for unknowns. Only cite identifiers present in "
            "freightpro_facts / email_facts / pod_facts. "
            "Use email_meta.sender_corroboration (matched|conflict|unavailable) for "
            "sender/shipper identity — do not assume raw mailbox addresses. "
            "Do not treat forwarded Original-From headers as shipper identity."
        ),
    }

    llm_data: dict = {}
    usage = None
    llm_status = "dry_run" if not use_llm else "ok"
    llm_error: str | None = None
    interpretation: dict = {}
    llm_validation_warnings: list[str] = []
    pii_report = PIIGateReport(
        status="skipped_dry_run" if not use_llm else "passed",
        lexicon_entries=lexicon.size,
        notes=["Dry-run: no Anthropic call."] if not use_llm else [],
    )
    if pod.mode == "vision_blocked":
        pii_report.vision_policy = "blocked_pending_dpa"
        pii_report.notes.append("POD vision blocked pending DPA (pixels not redacted).")
        needs_human_reasons.append(
            "POD image not sent to Anthropic — open attachment manually (DPA/vision gate)."
        )

    # Fail closed on forwards: third-party PII cannot be guaranteed via FreightPro lexicon.
    if use_llm and forward_info.detected and not forward_info.safe_redaction_guaranteed:
        llm_status = "pii_blocked"
        llm_error = forward_info.escalation_reason
        pii_report = PIIGateReport(
            status="blocked",
            lexicon_entries=lexicon.size,
            notes=[
                forward_info.escalation_reason
                or "Forwarded content — Anthropic call fail-closed.",
                "Coordinator packet retains redacted full body for human review; "
                "model body withheld third-party block.",
            ],
        )
        llm_data = {}
    elif use_llm:
        try:
            llm_data, usage, pii_report, _sent = classify_and_draft(
                observed_for_model, lexicon=lexicon
            )
            llm_status = "ok"
            if pod.mode == "vision_blocked":
                pii_report.vision_policy = "blocked_pending_dpa"
        except MissingAPIKeyError:
            raise
        except PIIGateError as exc:
            llm_status = "pii_blocked"
            llm_error = str(exc)
            llm_data = {}
            pii_report = PIIGateReport(
                status="blocked",
                lexicon_entries=lexicon.size,
                residual_name_hits=1,
                notes=[
                    "Outbound PII gate blocked the Anthropic call. "
                    "Deterministic packet only; human must review."
                ],
            )
            needs_human_reasons.append(
                "PII gate blocked model call — fail closed; human review required."
            )
        except ModelResponseError as exc:
            llm_status = "error"
            llm_error = str(exc)
            needs_human_reasons.append(f"Model/API failure — using deterministic fallback: {exc}")
            llm_data = {}
        except Exception as exc:  # noqa: BLE001
            llm_status = "error"
            llm_error = str(exc)
            needs_human_reasons.append(f"Unexpected model error — deterministic fallback: {exc}")
            llm_data = {}

    if llm_data:
        cleaned, llm_validation_warnings = validate_llm_against_known_facts(
            llm_data, allowed_ids
        )
        for w in llm_validation_warnings:
            needs_human_reasons.append(w)
            discrepancies.append(w)
        classification = Classification(
            claim_type=cleaned["claim_type"],
            confidence=cleaned["confidence"],
            alleged_facts=cleaned["alleged_facts"],
            source="llm",
        )
        summary = cleaned["summary"]
        draft, draft_warnings = sanitize_claim_draft(
            cleaned["draft_reply"],
            email_text=blob,
            load=load,
        )
        for w in draft_warnings:
            needs_human_reasons.append(w)
            discrepancies.append(w)
        for d in cleaned["discrepancies"]:
            if d not in discrepancies:
                discrepancies.append(d)
        for r in cleaned["needs_human_reasons"]:
            if r not in needs_human_reasons:
                needs_human_reasons.append(r)
        needs_human = True
        if classification.claim_type == "not_a_claim":
            needs_human_reasons.append(
                "Model suggested not_a_claim — human must confirm before closing."
            )
        interpretation = {
            "source": "llm",
            "claim_type": classification.claim_type,
            "confidence": classification.confidence,
            "alleged_facts": classification.alleged_facts,
            "summary": summary,
            "draft_reply": draft,
            "model_discrepancies": cleaned["discrepancies"],
            "invented_id_warnings": llm_validation_warnings,
        }
        draft_source = "llm"
    else:
        claim_type = heuristic_intent
        classification = Classification(
            claim_type=claim_type,
            confidence=0.55,
            alleged_facts=redacted_body[:240],
            source="deterministic" if not use_llm else "llm_fallback",
        )
        summary = (
            f"{claim_type} email from {parsed.from_addr}; "
            f"load resolution={resolution.status}."
        )
        draft = _fallback_draft(parsed, resolution, extracted.identifiers)
        draft, draft_warnings = sanitize_claim_draft(
            draft,
            email_text=blob,
            load=load,
        )
        for w in draft_warnings:
            needs_human_reasons.append(w)
            discrepancies.append(w)
        if llm_status in ("error", "pii_blocked"):
            summary = f"[MODEL FALLBACK] {summary}"
        interpretation = {
            "source": classification.source,
            "claim_type": claim_type,
            "confidence": classification.confidence,
            "alleged_facts": classification.alleged_facts,
            "summary": summary,
            "draft_reply": draft,
            "model_discrepancies": [],
            "invented_id_warnings": [],
        }
        draft_source = classification.source

    # Rebuild comparisons after any LLM discrepancy merges
    comparisons = build_comparisons(
        discrepancies=discrepancies,
        freightpro=freightpro_facts,
        email=email_facts,
        pod=pod_facts,
    )

    llm_interpretation = {
        "status": llm_status if use_llm else "dry_run",
        "source": interpretation.get("source"),
        "claim_type": interpretation.get("claim_type"),
        "confidence": interpretation.get("confidence"),
        "summary": interpretation.get("summary"),
        "alleged_facts": interpretation.get("alleged_facts"),
        "suggested_discrepancies": interpretation.get("model_discrepancies") or [],
        "invented_id_warnings": interpretation.get("invented_id_warnings") or [],
        "error": llm_error,
        "note": (
            "Interpretation only — not a source of FreightPro/POD facts. "
            "Null/unknown fields above were not filled by the model."
        ),
    }
    draft_response = {
        "generated": bool(draft and draft.strip()),
        "source": draft_source,
        "text": draft,
        "auto_send": False,
        "note": "Draft for human review only — never sent automatically.",
    }
    human_review = {
        "required": needs_human,
        "reasons": _unique(needs_human_reasons),
        "unknowns_requiring_human": [
            u for u in unknowns if u.get("requires_human")
        ],
    }
    analysis = build_analysis_bundle(
        freightpro=freightpro_facts,
        email=email_facts,
        pod=pod_facts,
        comparisons=comparisons,
        unknowns=unknowns,
        llm_interpretation=llm_interpretation,
        draft_response=draft_response,
        human_review=human_review,
    )

    # Coordinator-facing observed block (legacy / detailed)
    observed_out = {
        **observed,
        "pod": {
            **observed["pod"],
            "excerpt_redacted": redacted_pod_excerpt,
        },
        "load_coordinator_view": redacted_load,
        "attachments": attachments_audit,
        "forward": email_facts.get("forward") or forward_info.to_dict(),
    }

    duration_ms = int((time.perf_counter() - t0) * 1000)
    attachment_names = [a.filename for a in parsed.attachments if a.filename]
    decision = build_decision_record(
        email_id=parsed.email_id,
        intent=classification.claim_type,
        identifiers=extracted.identifiers,
        resolution=resolution,
        pod=pod_for_facts,
        attachment_filenames=attachment_names,
        attachment_decision=attachments_audit,
        evidence=list(resolution.evidence or []),
        discrepancy_count=len(discrepancies),
        pii_gate=pii_report.to_dict(),
        llm_status=llm_status if use_llm else "dry_run",
        classification_source=classification.source,
        needs_human=needs_human,
        needs_human_reasons=needs_human_reasons,
        draft_reply=draft,
        processing_duration_ms=duration_ms,
        llm_error=llm_error,
        usage=usage,
    )

    packet = ActionPacket(
        email_id=parsed.email_id,
        summary=summary,
        from_addr=parsed.from_addr,
        to_addr=parsed.to_addr,
        subject=parsed.subject,
        date=parsed.date,
        classification=classification,
        identifiers=extracted.identifiers,
        resolution=resolution,
        carrier=carrier,
        pod=pod_for_facts,
        discrepancies=discrepancies,
        draft_reply=draft,
        needs_human=needs_human,
        needs_human_reasons=_unique(needs_human_reasons),
        assumptions=list(DEFAULT_ASSUMPTIONS),
        usage=usage,
        observed=observed_out,
        interpretation=interpretation,
        llm_status=llm_status if use_llm else "dry_run",
        llm_error=llm_error,
        pii_gate=pii_report.to_dict(),
        decision=decision.to_dict(),
        analysis=analysis,
        attachments=attachments_audit,
    )

    write_packet(packet, out_root)
    return packet


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def _heuristic_claim_type(text: str) -> str:
    t = text.lower()
    if "short" in t:
        return "shortage"
    if "refus" in t:
        return "refusal"
    if "os&d" in t or "osd" in t:
        return "osd"
    if "damage" in t or "damaged" in t:
        return "damage"
    if "temp abuse" in t or "temperature" in t:
        return "damage"
    if "claim" in t or "late delivery" in t:
        return "other"
    return "other"


# Secondary ops asks that must not be auto-executed on a claims email.
_SECONDARY_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "tracking",
        re.compile(
            r"\b(?:track(?:ing)?(?:\s+status)?|where\s+is\s+(?:my|the)\s+"
            r"(?:load|shipment|truck)|(?:current\s+)?eta)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "invoice",
        re.compile(r"\b(?:invoice|invoicing|billing\s+copy|send\s+(?:the\s+)?bill)\b", re.IGNORECASE),
    ),
    (
        "quote",
        re.compile(
            r"\b(?:quote|quoting|rate\s+request|need\s+a\s+rate|price\s+this\s+lane)\b",
            re.IGNORECASE,
        ),
    ),
]

_CLAIM_SIGNAL_RE = re.compile(
    r"\b(?:claim|damage|damaged|shortage|short(?:age)?|refus(?:e|al|ed)?|"
    r"os&d|osd|temp(?:erature)?\s+abuse)\b",
    re.IGNORECASE,
)


def _secondary_ops_intents(text: str) -> list[str]:
    """
    If a claims email also asks for tracking / invoice / quote, return those labels.

    Does not change claim classification or execute the secondary work — callers
    escalate with a multiple-intents human-review reason.
    """
    if not text or not _CLAIM_SIGNAL_RE.search(text):
        return []
    found: list[str] = []
    for label, pat in _SECONDARY_INTENT_PATTERNS:
        if pat.search(text):
            found.append(label)
    return found


def _fallback_draft(parsed, resolution, identifiers) -> str:
    load_no = (resolution.load or {}).get("LoadNumber") or identifiers.load_number or "your shipment"
    return (
        f"Thank you for contacting Meridian Freight claims.\n\n"
        f"We received your note regarding {load_no} "
        f"(PO {identifiers.po or 'n/a'}, BOL {identifiers.bol or 'n/a'}).\n\n"
        f"A coordinator is reviewing this against our load record and any POD on file. "
        f"We will follow up shortly with next steps. "
        f"If you have photos, a delivery receipt with exceptions, or an itemized loss list, "
        f"please reply with those attached.\n\n"
        f"— Meridian Freight Claims (draft for human review — not sent)"
    )


def run_process_cli(
    eml_path: Path,
    *,
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    try:
        packet = process_email(
            eml_path,
            data_dir=data_dir,
            output_dir=output_dir,
            use_llm=not dry_run,
            use_vision=not dry_run,
        )
    except MissingAPIKeyError as exc:
        print(str(exc), flush=True)
        return 2
    print_summary(packet)
    out = Path(output_dir) if output_dir else Path(__file__).resolve().parent.parent / "output"
    print(f"Wrote {out / (packet.email_id + '.json')} and {out / (packet.email_id + '.md')}")
    if packet.llm_status == "error":
        print(f"Note: llm_status=error ({packet.llm_error})")
    return 0
