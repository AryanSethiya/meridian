# NOTES (for reviewers)

## Time (~8h core + focused hardening)

- Brief, conflicts, sample data, slice choice (~1.5h)  
- Deterministic pipeline: parse → resolve → POD text → packet (~2h)  
- Anthropic draft path, blank-POD behavior, PII gate, tests (~2h)  
- Analysis layout, decision audit, eval harness (~1.5h)  
- Safety hardening: shipper corroboration, attachment inventory, draft $-gate, POD quality, multi-intent, forwards, freshness metadata (~2h+)  
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

## Implemented in this repo (not “next”)

- Sender ↔ shipper ContactEmail/domain corroboration (`matched` / `conflict` / `unavailable`)  
- Attachment inventory; multi-POD → escalate (no silent pick)  
- Draft liability filter + invented monetary-amount block  
- Multi-intent escalate (claims + tracking/invoice/quote); secondary not executed  
- Forwarded/third-party markers → model fail-closed; envelope From only for shipper trust  
- FreightPro source/snapshot freshness on facts (no invented per-field `as_of`)  
- POD text quality gate; model payload uses `sender_corroboration` (no raw From/To)  

## What I would build next (still not done)

- Graph poller + thin accept/edit/reject queue  
- Wrong-load logging / coordinator review metrics in production  
- Per-field `as_of` **if** FreightPro export ever provides it (not inventable today)  

I would **not** start unsupervised send or quotes until wrong-load ≈ 0 and draft-accept metrics are real on claims.
