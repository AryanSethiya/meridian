# NOTES (for reviewers)

## Time

Core claims slice (parse → resolve → POD → HITL packet → DESIGN/README) stayed near the **~8h** brief. Safety hardening and thin stretch demos added some overtime; I stopped expanding scope rather than padding features.

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

**Stretch note:** the brief says pick **at most one** optional stretch. After the core slice was solid I added thin wrappers for all four (all-sample behavioral eval, model-boundary PII tests, estimated cost/latency, local review UI with optional live re-process, latency/tokens, and no send). They share the existing pipeline/ActionPacket — no second architecture. Walkthrough: [YouTube video](https://www.youtube.com/watch?v=Dpa6lfQ7p_g) (I demo **one** stretch path there).

## What I would build next (still not done)

- Graph poller feeding the same pipeline  
- Wrong-load logging / coordinator review metrics in production  
- Per-field `as_of` **if** FreightPro export ever provides it (not inventable today)  

I would **not** start unsupervised send or quotes until wrong-load ≈ 0 and draft-accept metrics are real on claims.
