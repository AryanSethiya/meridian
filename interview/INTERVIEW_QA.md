# Interview rapid Q&A

Short answers. Expand only if they dig.

---

### What problem are you solving?
Claims triage: cut ~20 min POD↔FreightPro compare time with a HITL action packet — not Dana’s full 4-inbox auto-agent.

### Why not automate 90%?
Marcus won’t allow unread AI send; data is dirty/stale; Legal lacks DPA. Wrong send &gt; week of coordinator time.

### Why claims first?
Highest time ROI, HITL-natural, sample data has the hard cases. Depth over breadth.

### What does the system output?
ActionPacket: JSON + markdown + decision sidecar. Facts separated from AI suggestion. Draft for human review only.

### Where does “truth” come from?
Deterministic path: parse, resolve, POD, discrepancies. LLM only classifies/drafts and must not invent null facts.

### Where is FreightPro?
Read-only CSVs in `data/freightpro/` via `freightpro.py`. Stand-in for the read replica. Not live API.

### How do you find the load?
MF → PO → BOL → PRO. Multi-MF or ID conflicts → ambiguous. Shipper domain corroboration on top.

### What if there’s no load number?
Try PO, then BOL, then PRO (`041`, `042`). If none unique → unresolved / escalate.

### How do PODs work?
Inventory attachments; extract text; blank → `blank_page`; vision off by default (DPA / pixels).

### When do you call Anthropic?
After deterministic facts + PII gate, if not dry-run and not fail-closed. `agent.classify_and_draft`.

### What if the API key is missing?
Clear `MissingAPIKeyError` / exit. No silent skip pretending success on live path.

### Does the review UI use different code?
No. Same `process_email`. UI is HTTP + localStorage Accept/Reject. Never sends.

### Is 8/8 accuracy?
No — known-sample regression on 8 labeled fixtures only.

### What about PII?
Redact DriverName/Phone; outbound scan fail-closed; no raw From/To to model; vision blocked. Mitigation ≠ DPA.

### What would you build next?
Graph poller on `claims@` → coordinator queue metrics → tracking HITL drafts. Not unsupervised send.

### Where did AI tools mislead you?
Pushed 4-inbox/auto-send; wanted to “OCR” blank PODs; trusted free-text carriers; treated redaction as Legal-complete; skipped ID cross-checks. You overrode each (see `NOTES.md`).

### Weakest part of the slice?
CSV-scale data access; fragile carrier free-text; stretch breadth vs “pick one”; no production ingress yet.

### If wrong-load rate isn’t zero?
Tighten gates — do **not** expand automation.
