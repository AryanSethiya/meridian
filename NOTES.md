# NOTES (for reviewers)

## Time (~8h core + focused hardening)

- Brief, conflicts, sample data, slice choice (~1.5h)  
- Deterministic pipeline: parse → resolve → POD text → packet (~2h)  
- Anthropic draft path, blank-POD behavior, PII gate, tests (~2h)  
- Analysis layout, decision audit record, eval harness (~1.5h)  
- DESIGN / README / NOTES pass (~1h)  

Stopped short of quotes, review UI, and Graph wiring on purpose.

## How AI coding tools were used

Cursor scaffolded modules and first-draft prose. I overrode it when it:

- Pushed a **4-inbox / auto-send** agent (Dana’s framing) instead of claims + HITL  
- Wanted to **“successfully OCR”** blank `POD_scan_*.pdf` pages — we mark unreadable / skip vision  
- Trusted **free-text carrier names** — we prefer MF/PO/BOL/PRO and MC/SCAC  
- Treated **redaction as “Legal done”** — gate is leakage mitigation; **DPA is still required**  
- Skipped **identifier cross-checks** — conflicting MF/PO now goes `ambiguous`, not silent resolve  

I can explain and change every module live without depending on chat history.

## What I would build next (not done)

- Soft shipper/From-domain corroboration (reduce wrong-but-unique MF)  
- Attachment inventory when multiple files are present  
- Dollar/settlement-amount scrub on drafts  
- Graph poller + thin accept/edit/reject queue  
- Per-field `as_of` on FreightPro facts in the packet UI  

I would **not** start unsupervised send or quotes until wrong-load ≈ 0 and draft-accept metrics are real on claims.
