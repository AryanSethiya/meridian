"""Render action packets as JSON and markdown for coordinators."""

from __future__ import annotations

import json
from pathlib import Path

from meridian_claims.models import ActionPacket


def write_packet(packet: ActionPacket, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{packet.email_id}.json"
    md_path = output_dir / f"{packet.email_id}.md"
    json_path.write_text(json.dumps(packet.to_dict(), indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(packet), encoding="utf-8")
    if packet.decision:
        decision_path = output_dir / f"{packet.email_id}.decision.json"
        decision_path.write_text(
            json.dumps(packet.decision, indent=2), encoding="utf-8"
        )
    return json_path, md_path


def to_markdown(packet: ActionPacket) -> str:
    load = packet.resolution.load or {}
    lines = [
        f"# Claims action packet — {packet.email_id}",
        "",
        f"**From:** {packet.from_addr}  ",
        f"**To:** {packet.to_addr}  ",
        f"**Subject:** {packet.subject}  ",
        f"**Date:** {packet.date}",
        "",
        "## Decision record (audit)",
    ]
    if packet.decision:
        d = packet.decision
        lines.extend(
            [
                f"- Intent: `{d.get('intent')}`",
                f"- Resolved load: `{d.get('resolved_load')}` "
                f"via `{d.get('resolution_method')}` "
                f"({d.get('resolution_status')}, conf={d.get('resolution_confidence')})",
                f"- POD: `{d.get('pod_extraction_method')}` / `{d.get('pod_status')}`",
                f"- PII gate: `{d.get('pii_redaction_status')}`",
                f"- Model: `{d.get('model_call_status')}` / "
                f"validation `{d.get('model_output_validation_status')}`",
                f"- Escalation: `{d.get('escalation_decision')}` "
                f"(draft_generated={d.get('draft_generated')})",
                f"- Duration: {d.get('processing_duration_ms')} ms",
            ]
        )
    else:
        lines.append("- _(none)_")

    # Coordinator-first analysis layout
    if packet.analysis:
        a = packet.analysis
        lines.extend(["", "## What do we know?"])
        fp = a.get("freightpro_facts") or {}
        lines.append("### From FreightPro")
        if fp.get("available"):
            lines.extend(
                [
                    f"- Source: {fp.get('source')} (snapshot `{fp.get('snapshot_date')}`)",
                    f"- Freshness: {fp.get('freshness_note')}",
                    f"- Load: `{fp.get('load_number')}` "
                    f"(recorded status `{fp.get('status_recorded') or fp.get('status')}` "
                    f"— not live tracking)",
                    f"- Lane: {fp.get('lane')}",
                    f"- PODReceived: `{fp.get('pod_received')}`",
                    f"- Carrier: {fp.get('carrier_legal_name')} "
                    f"(MC {fp.get('carrier_mc')}, SCAC {fp.get('carrier_scac')})",
                ]
            )
        else:
            lines.append(
                f"- _(unavailable)_ — {fp.get('reason')} "
                f"[source={fp.get('source')}, snapshot={fp.get('snapshot_date')}]"
            )

        ef = a.get("email_facts") or {}
        ids = ef.get("identifiers") or {}
        lines.extend(
            [
                "### From email",
                f"- Intent (heuristic): `{ef.get('claimed_intent_heuristic')}`",
                f"- IDs: MF=`{ids.get('load_number')}` PO=`{ids.get('po')}` "
                f"BOL=`{ids.get('bol')}` PRO=`{ids.get('pro')}`",
                f"- Carrier mention: {ef.get('carrier_mention') or '—'}",
            ]
        )
        pf = a.get("pod_facts") or {}
        lines.append("### From POD")
        if pf.get("available"):
            lines.extend(
                [
                    f"- Method: `{pf.get('extraction_method')}`",
                    f"- IDs: MF=`{pf.get('load_number')}` PO=`{pf.get('po_number')}` "
                    f"BOL=`{pf.get('bol_number')}`",
                    f"- Condition: {pf.get('condition_summary')}",
                    f"- Exceptions noted: {pf.get('exceptions_noted')}",
                ]
            )
        else:
            lines.append(
                f"- _(unknown/null)_ — {pf.get('unavailable_reason') or pf.get('status')}"
            )

        lines.extend(["", "## What evidence supports it?"])
        for e in (a.get("what_evidence_supports_it") or {}).get("freightpro_evidence") or []:
            lines.append(f"- {e}")
        for e in (a.get("what_evidence_supports_it") or {}).get("corroborations") or []:
            lines.append(f"- {e}")

        lines.extend(["", "## What is inconsistent?"])
        inconsist = a.get("what_is_inconsistent") or {}
        discs = inconsist.get("discrepancies") or []
        if discs:
            lines.extend(f"- {d}" for d in discs)
        else:
            lines.append("- None flagged")
        for c in inconsist.get("freightpro_conflicts") or []:
            lines.append(f"- Conflict: {c}")

        lines.extend(["", "## What is still unknown?"])
        unknowns = a.get("what_is_still_unknown") or []
        if unknowns:
            for u in unknowns:
                flag = " (requires human)" if u.get("requires_human") else ""
                lines.append(f"- `{u.get('field')}` = null — {u.get('reason')}{flag}")
        else:
            lines.append("- None listed")

        lines.extend(["", "## What does the AI suggest?"])
        ai = (a.get("what_ai_suggests") or {}).get("interpretation") or {}
        draft_block = (a.get("what_ai_suggests") or {}).get("draft_response") or {}
        lines.extend(
            [
                f"- Status: `{ai.get('status')}` / source `{ai.get('source')}`",
                f"- Claim type: `{ai.get('claim_type')}` (conf {ai.get('confidence')})",
                f"- Summary: {ai.get('summary') or '—'}",
                f"- Note: {ai.get('note')}",
            ]
        )
        for w in ai.get("invented_id_warnings") or []:
            lines.append(f"- Validation: {w}")

        hr = a.get("why_human_review") or {}
        lines.extend(
            [
                "",
                "## Why human review?",
                f"- Required: **{hr.get('required')}**",
            ]
        )
        for r in hr.get("reasons") or []:
            lines.append(f"- {r}")

        lines.extend(
            [
                "",
                "## Draft response (AI suggestion — do not auto-send)",
                f"- generated: {draft_block.get('generated')} / source `{draft_block.get('source')}`",
                "",
                draft_block.get("text") or packet.draft_reply or "_(no draft)_",
            ]
        )

    lines.extend(
        [
            "",
            "## Summary",
            packet.summary or "_(none)_",
            "",
            "## Classification",
            f"- Type: `{packet.classification.claim_type}` "
            f"(confidence {packet.classification.confidence:.2f})",
            f"- Alleged facts: {packet.classification.alleged_facts or '—'}",
            "",
            "## Identifiers",
            f"- Load: `{packet.identifiers.load_number or '—'}`",
            f"- PO: `{packet.identifiers.po or '—'}`",
            f"- BOL: `{packet.identifiers.bol or '—'}`",
            f"- PRO: `{packet.identifiers.pro or '—'}`",
            "",
            "## Load resolution",
            f"- Status: **{packet.resolution.status}**"
            + (f" via `{packet.resolution.method}`" if packet.resolution.method else ""),
            f"- Confidence: {packet.resolution.confidence:.2f}",
            f"- Reason: {packet.resolution.reason or '—'}",
        ]
    )
    if load:
        lines.extend(
            [
                f"- LoadNumber: `{load.get('LoadNumber', '')}`",
                f"- Status (ETL): `{load.get('Status', '')}`",
                f"- Lane: {load.get('lane', '')}",
                f"- Commodity: {load.get('Commodity', '')}",
                f"- Equipment: {load.get('EquipmentType', '')}",
                f"- PODReceived: `{load.get('PODReceived', '')}`",
                f"- Shipper: {load.get('shipper_name', '')}",
                f"- Carrier: {load.get('carrier_legal_name', '')} "
                f"(MC {load.get('carrier_mc', '')}, SCAC {load.get('carrier_scac', '')})",
            ]
        )
    if packet.resolution.candidates:
        lines.append("- Candidates:")
        for c in packet.resolution.candidates:
            lines.append(
                f"  - `{c.get('LoadNumber')}` — {c.get('lane')} — {c.get('Status')}"
            )

    lines.extend(
        [
            "",
            "## Carrier mention",
            f"- Email mention: {packet.carrier.email_mention or '—'}",
            f"- Matched: {packet.carrier.legal_name or '—'} "
            f"(MC {packet.carrier.mc or '—'}, SCAC {packet.carrier.scac or '—'})",
            "",
            "## POD",
            f"- File: `{packet.pod.filename or 'none'}`",
            f"- Mode: `{packet.pod.mode}` / status `{packet.pod.status}`",
            f"- Findings: {packet.pod.findings or '—'}",
        ]
    )
    att = packet.attachments or {}
    if att:
        lines.extend(
            [
                "",
                "## Attachments (inventory)",
                f"- Selection: **{att.get('selection_status') or '—'}**"
                + (
                    f" → `{att.get('selected_filename')}`"
                    if att.get("selected_filename")
                    else ""
                ),
                f"- Reason: {att.get('reason') or '—'}",
            ]
        )
        for row in att.get("inventory") or []:
            lines.append(
                f"- `{row.get('filename')}` — {row.get('kind')} "
                f"({row.get('size_bytes', 0)} bytes)"
            )
        for row in att.get("unprocessed") or []:
            lines.append(
                f"- Not processed: `{row.get('filename')}` ({row.get('status')})"
            )
    if packet.pod.excerpt:
        lines.extend(["", "```", packet.pod.excerpt[:1200], "```"])

    lines.extend(["", "## Discrepancies (deterministic + validated)"])
    if packet.discrepancies:
        lines.extend(f"- {d}" for d in packet.discrepancies)
    else:
        lines.append("- None flagged")

    # Draft already shown under analysis; keep a short pointer if analysis present
    if not packet.analysis:
        lines.extend(
            [
                "",
                "## Draft reply (HUMAN REVIEW — do not auto-send)",
                "",
                packet.draft_reply or "_(no draft)_",
            ]
        )

    lines.extend(
        [
            "",
            "## Needs human",
            f"- Required: **{packet.needs_human}**",
        ]
    )
    for reason in packet.needs_human_reasons:
        lines.append(f"- {reason}")

    lines.extend(["", "## Assumptions"])
    for a in packet.assumptions:
        lines.append(f"- {a}")

    lines.extend(
        [
            "",
            "## Observed (deterministic)",
            f"- Resolution: `{packet.resolution.status}` "
            f"conf={packet.resolution.confidence:.2f} "
            f"method={packet.resolution.method or '—'}",
        ]
    )
    if packet.resolution.evidence:
        lines.append("- Evidence:")
        for e in packet.resolution.evidence:
            lines.append(f"  - {e}")
    if packet.resolution.conflicts:
        lines.append("- Conflicts:")
        for c in packet.resolution.conflicts:
            lines.append(f"  - {c}")

    lines.extend(
        [
            "",
            "## Interpretation (model / fallback)",
            f"- llm_status: `{packet.llm_status}`",
            f"- source: `{packet.interpretation.get('source', '—')}`",
        ]
    )
    if packet.llm_error:
        lines.append(f"- llm_error: {packet.llm_error}")

    if packet.pii_gate:
        lines.extend(
            [
                "",
                "## PII gate (outbound)",
                f"- status: `{packet.pii_gate.get('status')}`",
                f"- lexicon_entries: {packet.pii_gate.get('lexicon_entries')}",
                f"- phones_masked: {packet.pii_gate.get('phones_masked')} / "
                f"names_masked: {packet.pii_gate.get('names_masked')} / "
                f"receiver_lines_masked: {packet.pii_gate.get('receiver_lines_masked')}",
                f"- residual_name_hits: {packet.pii_gate.get('residual_name_hits')} / "
                f"residual_phone_hits: {packet.pii_gate.get('residual_phone_hits')}",
                f"- vision_policy: `{packet.pii_gate.get('vision_policy')}`",
            ]
        )
        for note in packet.pii_gate.get("notes") or []:
            lines.append(f"- {note}")
        if packet.pii_gate.get("dpa_note"):
            lines.append(f"- DPA: {packet.pii_gate['dpa_note']}")

    if packet.usage:
        lines.extend(
            [
                "",
                "## Model usage",
                f"- Model: `{packet.usage.model}`",
                f"- Latency: {packet.usage.latency_ms} ms",
                f"- Tokens: in={packet.usage.input_tokens} out={packet.usage.output_tokens}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def print_summary(packet: ActionPacket) -> None:
    load_no = (packet.resolution.load or {}).get("LoadNumber", "—")
    print(f"[{packet.email_id}] {packet.classification.claim_type} | "
          f"resolve={packet.resolution.status}/{packet.resolution.method or '—'} | "
          f"load={load_no} | pod={packet.pod.mode} | "
          f"needs_human={packet.needs_human}")
    if packet.discrepancies:
        print(f"  discrepancies: {len(packet.discrepancies)}")
    print(f"  draft preview: {(packet.draft_reply or '')[:160].replace(chr(10), ' ')}...")
