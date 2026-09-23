# Interview code map — where to open files

When they say “show me in code,” jump here. Know the **one-sentence purpose** of each file.

---

## Mental model (say this)

> `cli.py` is the door. `pipeline.process_email()` is the conductor. Everything else is a stage. The review UI is the same conductor behind HTTP. Nothing sends email.

---

## File → one-liner → open when asked about…

| File | One-liner | Open when they ask… |
|---|---|---|
| `cli.py` | `process` / `eval` / `review`; loads `.env` | “How do I run this?” |
| `pipeline.py` | End-to-end orchestration; always `needs_human=True` | “Walk me through one email” |
| `email_parser.py` | `.eml` → headers, body, attachments | “How do you ingest mail?” |
| `resolve_load.py` | MF→PO→BOL→PRO + shipper corroboration | “How do you find the load?” |
| `freightpro.py` | Read-only CSV indexes | “Where is FreightPro?” |
| `pod.py` | Inventory, text extract, blank page, vision gate | “How do PODs work?” |
| `evidence.py` | Discrepancies + draft $/liability sanitize | “Where do comparisons live?” |
| `forward_mail.py` | Forward markers → model fail-closed | “What about forwards?” |
| `redact.py` / `pii_gate.py` | Mask + outbound residual scan | “PII / Legal?” |
| `agent.py` | Anthropic classify + draft | “Where is the LLM?” |
| `analysis.py` | Fact buckets for the packet | “How is the packet structured?” |
| `models.py` | `ActionPacket`, `Resolution.is_authoritative…` | “Automation gate?” |
| `render.py` | Write `output/{id}.{json,md}` | “What does the coordinator see?” |
| `eval.py` | 8-fixture regression + `--all` behavioral | “How do you evaluate?” |
| `review_server.py` + `review_ui/` | Local HITL UI; `POST /api/process` → same pipeline | “Review UI?” |
| `pricing.py` | Estimated $ from Anthropic tokens | “Cost?” |

**Unused by design:** `rates.csv`, `carrier_availability.csv` (quote/dispatch — out of slice).

---

## Deep dive A — `process_email` order (memorize)

Open `pipeline.py` → `process_email()`. Rough stage order:

1. Parse email (`email_parser`)
2. Load FreightPro DB (`freightpro.FreightProDB`)
3. Extract identifiers + `resolve_load` + shipper corroboration
4. Decide / process POD (`pod`)
5. Build discrepancies (`evidence`)
6. Forward + multi-intent checks
7. Build `observed_for_model` (facts only; `sender_corroboration`, not raw From/To)
8. If `use_llm` and not fail-closed → `classify_and_draft`; else heuristic fallback
9. Assemble `analysis` + decision record
10. `write_packet` → return `ActionPacket`

**Always:** `needs_human = True`, draft `auto_send: false`.

---

## Deep dive B — Load resolution

Open `resolve_load.py`:

- `extract_identifiers` — regex for `MF-#####`, `PO-#######`, BOL, PRO  
- `resolve_load` preference: **LoadNumber → PO → BOL → PRO**  
- Multiple distinct MFs → `ambiguous` (no silent pick)  
- After unique match, **cross-check** other IDs; mismatches → conflicts / lower confidence  
- Shipper corroboration: envelope From domain vs shipper ContactEmail → `matched` / `conflict` / `unavailable`

**Say aloud:**  
“Carrier free-text mention is informational. It does not overturn a unique LoadNumber.”

**Automation gate** (`models.py`):

```python
Resolution.is_authoritative_for_action()  # True only if status == "resolved"
```

Ambiguous “best guess” is for humans, not for future auto-action.

---

## Deep dive C — POD

Open `pod.py`:

1. Inventory **all** attachments (selected vs present-not-processed)  
2. Select POD-named PDF or sole PDF; **multi-POD → escalate** (no silent pick)  
3. Extract text; quality gate  
4. Blank/placeholder scan → `blank_page`  
5. Vision only if: no usable text, not blank, **and** `MERIDIAN_ALLOW_POD_VISION=1`  
6. Default: vision blocked pending DPA (pixels can’t be redacted safely)

**Demo contrast:** `035` = readable text POD (“no exceptions”) vs `036` = blank page.

---

## Deep dive D — LLM boundary

Open `agent.py` → `classify_and_draft`:

1. `require_api_key()` — UI override via `temporary_api_key` / else env  
2. `prepare_text_payload` (PII gate) — fail-closed on residual known PII  
3. `anthropic.Anthropic(...).messages.create(...)`  
4. Parse JSON; scrub invented IDs; return usage/cost estimates  

**Prompt contract (say this):**  
“Model must not invent null facts. Deterministic comparisons win. Draft never approves/denies/settles.”

**Skips Anthropic when:** dry-run, forward fail-closed, PII gate block, missing key (clear error).

Review UI path: `review_server._process_email_id` → `temporary_api_key` → **same** `process_email`.

---

## Deep dive E — Safety gates (name 5 fast)

1. HITL always on claims path  
2. PII redact + outbound scan; vision off by default  
3. Forward / third-party → model fail-closed  
4. Multi-intent (claims + tracking/invoice/quote) → escalate; secondary not executed  
5. Draft liability language + invented dollar-amount block (`evidence.py`)

---

## Deep dive F — Packet shape

Coordinator-facing sections (also in MD render):

1. FreightPro facts (+ snapshot freshness)  
2. Email facts  
3. POD facts / unknown  
4. What is inconsistent  
5. What is still unknown  
6. What AI suggests  
7. Draft response (`auto_send: false`)  
8. Why human review  

Sidecar: `output/{id}.decision.json` (audit / latency / tokens).

---

## Deep dive G — Eval honesty

Open `eval.py`:

- `EXPECTED_LOADS` — **8** labeled claims fixtures only  
- `eval --dry-run` — regression, no Anthropic  
- `eval --all` — behavioral run over all sample `.eml`; **not** an accuracy benchmark  

Never claim “8/8 means production-ready.”

---

## Live navigation tips

1. Keep `pipeline.py`, `resolve_load.py`, `agent.py`, `pod.py` as your four tabs.  
2. When stuck: start at `process_email` and scroll — it’s the story.  
3. Prefer pointing at **functions**, not dumping whole files.  
4. If they ask “change X,” first say **where** and **what breaks**, then edit.

See [`INTERVIEW_LIVE_DRILLS.md`](INTERVIEW_LIVE_DRILLS.md) for practice edits.
