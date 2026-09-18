# Technical handoff — Meridian Freight claims triage

Inspection based on the current repo code under `meridian_claims/`, `tests/`, `data/`, and docs. Status labels: **EXISTS** / **PARTIAL** / **FRAGILE** / **MISSING**.

---

## 1. Repository structure

```text
meridian-freight-fde-takehome-assignment/
├── README.md                 # cold-clone demo
├── DESIGN.md                 # client 6-week plan
├── NOTES.md                  # reviewer notes
├── INSTRUCTIONS.md           # original assignment (untouched)
├── data.md                   # data dictionary
├── requirements.txt          # anthropic, pypdf, pymupdf, pytest
├── .env.example / .gitignore
├── data/
│   ├── emails/*.eml          # 60 sample emails
│   ├── attachments/          # POD PDFs + fake .xls
│   ├── freightpro/           # loads, carriers, shippers, rates CSVs
│   └── carrier_availability.csv
├── meridian_claims/          # application package
├── tests/                    # unit tests
└── output/                   # gitignored runtime artifacts
```

| File | Role |
|---|---|
| `cli.py` / `__main__.py` | `process` / `eval` entrypoints |
| `pipeline.py` | `process_email()` — orchestration |
| `email_parser.py` | `.eml` → `ParsedEmail` + attachments |
| `resolve_load.py` | ID extract + FreightPro resolve |
| `freightpro.py` | CSV load + indexes + carrier mention |
| `pod.py` | PDF text / blank detect / optional vision |
| `redact.py` | Pattern + lexicon string redaction |
| `pii_gate.py` | Outbound gate before Anthropic |
| `agent.py` | Anthropic classify+draft |
| `evidence.py` | Deterministic discrepancies + draft filter |
| `analysis.py` | Separated facts / unknowns / AI / draft |
| `observability.py` | `decision` audit record |
| `render.py` | JSON/MD/decision sidecar writers |
| `eval.py` | Claims@ harness + expected loads |
| `models.py` | Dataclasses (`ActionPacket`, etc.) |

**UNUSED by code:** `data/freightpro/rates.csv`, `data/carrier_availability.csv` (intentionally out of claims scope).

---

## 2. Architecture

```text
                    ┌─────────────────────────────────────────┐
                    │  CLI: python -m meridian_claims process │
                    └──────────────────┬──────────────────────┘
                                       │
                                       v
┌──────────────┐   ┌─────────────────┐   ┌──────────────────┐
│ email_parser │──▶│ extract_ids +   │──▶│ FreightProDB     │
│ parse_eml()  │   │ resolve_load()  │   │ loads/carriers/  │
└──────┬───────┘   └────────┬────────┘   │ shippers CSVs    │
       │                    │            └────────┬─────────┘
       │ attachments        │ resolution          │
       v                    v                     │
┌──────────────┐   ┌─────────────────┐            │
│ select_pod + │   │ evidence checks │◀───────────┘
│ process_pod  │   │ discrepancies   │
└──────┬───────┘   └────────┬────────┘            │
       │                    │                     │
       v                    v                     │
┌──────────────┐   ┌─────────────────┐   ┌────────v─────────┐
│ redact +     │──▶│ analysis.py     │──▶│ pii_gate         │
│ lexicon      │   │ facts/unknowns  │   │ prepare_text_…   │
└──────────────┘   └────────┬────────┘   └────────┬─────────┘
                            │                     │
                            │              ┌──────v───────┐
                            │              │ agent.py     │
                            │              │ Anthropic    │  (or dry-run /
                            │              │ messagesAPI  │   error fallback)
                            │              └──────┬───────┘
                            │                     │
                            v                     v
                   ┌─────────────────────────────────────┐
                   │ ActionPacket + analysis + decision  │
                   │ → output/<id>.json/.md/.decision.json│
                   └─────────────────────────────────────┘
                              │
                              v
                   Human review (no send path)
```

**Component connections:** all orchestration is synchronous inside `pipeline.process_email()`. No Graph API, no queue, no DB writes. **MISSING:** production mail ingress/egress.

---

## 3. End-to-end: `035.eml` through code

1. **CLI** `cli.main` → `run_process_cli` → `process_email(path, use_llm=…)`
2. **`email_parser.parse_eml`**
   - Headers: From `shipping@prairiegraincooper.example`, Subject `Damage claim MF-10487`
   - Body: damage on MF-10487 / BOL243910 / PO-8300487, JBHT, clean-POD dispute
   - Attachment saved: `output/attachments/035/POD_MF-10487.pdf`
3. **`FreightProDB.load`** indexes CSVs under `data/freightpro/`
4. **`extract_identifiers(subject+body)`** → MF-10487, PO-8300487, BOL243910
5. **`resolve_load`** unique `LoadNumber` → enriched load (Prairie Grain, JB Hunt, PODReceived=Y, Detroit→Cleveland)
6. **`build_lexicon_from_rows(raw load)`** — MF-10487 row has empty driver fields in sample → lexicon size often 0
7. **Carrier:** hardcoded JBHT token + `find_carrier_mention` → SCAC JBHT / LegalName JB Hunt
8. **`select_pod_attachment`** → `POD_MF-10487.pdf`
   **`process_pod_attachment`** → pypdf text (“seals intact, no exceptions…”) → `mode=text`
9. **Discrepancies** via `damage_vs_clean_pod` (email damage vs PODReceived=Y + clean POD text)
10. Redact body/subject/POD excerpt (`Receiver:` → `[REDACTED_RECEIVER]`)
11. **`build_*_facts` / `build_unknowns`** → includes `delivery_condition_ground_truth` unknown
12. If live: **`classify_and_draft(observed_for_model, lexicon)`** → Anthropic JSON → `validate_llm_against_known_facts` → `filter_draft`
13. **`build_analysis_bundle`**, **`build_decision_record`**, **`ActionPacket`**
14. **`write_packet`** → `035.json`, `035.md`, `035.decision.json`
15. **`needs_human=True`**, `draft_response.auto_send=False`

---

## 4. LLM

### Anthropic call sites

| # | Location | When |
|---|---|---|
| 1 | `agent.classify_and_draft` → `client.messages.create` | Default live `process` (not `--dry-run`) |
| 2 | `pod._vision_pod` → `client.messages.create` | Only if no text layer **and** not blank **and** `MERIDIAN_ALLOW_POD_VISION=1` |

Default path for sample scans: vision **never** called (blank-page or vision_blocked).

### Call 1 — classify/draft (**EXISTS**)

- **Model:** `ANTHROPIC_MODEL` or `claude-sonnet-4-5-20250929`
- **System:** `SYSTEM_PROMPT` in `agent.py` (no inventing facts; HITL draft)
- **User:** OBSERVED_FACTS JSON after `prepare_text_payload` (freightpro/email/pod facts, comparisons, unknowns, redacted body, rate-stripped load)
- **Returns:** JSON with `claim_type`, `confidence`, `alleged_facts`, `discrepancies`, `draft_reply`, `needs_human_reasons`, `summary`
- **Validation:** JSON parse; require `claim_type` or `draft_reply`; scrub invented MF/PO/BOL via `validate_llm_against_known_facts`; `filter_draft` strips approve/pay/deny language; API/JSON errors → deterministic fallback (`llm_status=error`)

### Call 2 — vision (**EXISTS**, gated)

- Image PNG + prompt asking for usable/summary/exceptions_noted without names/phones
- **PARTIAL:** image pixels themselves can contain PII; blocked by default for that reason

---

## 5. Data

**Access:** `FreightProDB.load()` reads `loads.csv`, `carriers.csv`, `shippers.csv` into lists/dicts; indexes `by_load_number`, `by_po`, `by_bol`, `by_pro`, `by_mc`, `by_scac`. Reloaded per email (**FRAGILE** for scale, fine for demo).

**Resolution order** (`resolve_load.py`): LoadNumber (0.95) → PO (0.85) → BOL (0.85) → PRO (0.80).
Unique → `resolved`; multi-row same key → `ambiguous`; MF+conflicting PO/BOL → `ambiguous` + `conflicts`; multiple MFs in email → `ambiguous` + `multi_load_ids`.

**Carrier normalization:** Prefer SCAC/MC over `LegalName`. Pipeline also hardcodes JB Hunt token variants. Substring SCAC matching is **FRAGILE**.

**Enrich:** joins shipper/carrier; driver fields → `[REDACTED]` unless `include_pii=True` (only used for raw lexicon collection via `raw_load_by_number`).

---

## 6. POD

1. **Find:** `select_pod_attachment` — PDFs with `"pod"` in name, else first PDF. Other attachments ignored for processing (**PARTIAL**).
2. **Text:** `pypdf` via `extract_pdf_text`; ≥20 chars → `mode=text`.
3. **Blank:** `is_blank_scan` (few drawings, ≤4 unique colors) → `blank_page`, no vision.
4. **Non-blank, no text:** if vision env not set → `vision_blocked`; else rasterize + `_vision_pod`.
5. **035:** text POD, clean condition vs damage email → discrepancies.
6. **036:** `POD_scan_a.pdf` blank gray → `blank_page`, `pod_facts.available=false`.

---

## 7. PII

**Detected/redacted:**

- Lexicon from FreightPro `DriverName`/`DriverPhone` on resolved/candidate loads
- `redact_text`: phones, `Driver:` labels, `Receiver:` lines, lexicon names
- `pii_gate.prepare_text_payload`: deep redact + residual full-name/phone scan → **fail closed**
- Rates stripped from model load view via `model_safe_load`

**Can reach Anthropic (text path):** redacted email body/subject, structured facts, redacted POD excerpt, From/To addresses, company names, load IDs, commercial fields only if not stripped from that context. **Should not** reach: known driver name/phone (tested).

**Can reach Anthropic (vision path):** raw page image if opt-in — **PARTIAL / unsafe by design**, default off.

**Logs/output:**

- `decision.json`: designed PII-free
- `035.json` / `.md`: From address, shipper/carrier names, **redacted** POD excerpt; coordinator `resolution.load` may still include **CustomerRate/CarrierRate** (commercial, not Legal driver PII)
- Dry-run `alleged_facts` uses redacted body slice — better than raw, but unlabeled personal names not in lexicon remain **PARTIAL** risk
- Forwarded third-party PII: **MISSING** handling

---

## 8. Human review

**Always** `needs_human = True` on claims path (model cannot waive).

**Extra reasons include:** Marcus policy; unresolved/ambiguous identity; multi-MF; unreadable/missing POD; Do Not Use carrier; unknowns with `requires_human`; PII gate block; model failure; invented-ID warnings; liability language removed.

**Auto-send:** **MISSING** entirely. `draft_response.auto_send: false`. No Graph/SMTP/send API. Confirmed: **nothing can be automatically sent**.

---

## 9. Tests + evaluation

**~43 tests** across:

- `test_resolve_and_redact.py` — IDs, resolve, conflict, multi-MF, redact, POD blank/invalid, showcase 035/036/041/042, LLM fallback, HITL force, SCAC
- `test_pii_outbound.py` — lexicon, gate, fail-closed, mocked Anthropic payload clean, vision blocked, DPA note
- `test_observability.py` — decision fields, no driver PII in decision
- `test_analysis_layout.py` — fact separation, POD unknown, invented ID scrub

**Eval 8/8 proves:** for the eight sample `claims@` emails, dry-run load resolution hits the hardcoded `EXPECTED_LOADS` map.

**Does NOT prove:** claim_type accuracy, draft quality, live Anthropic behavior, non-claims mail, multi-PO ambiguity on real data, OCR quality, wrong-but-unique MF, PII on every live call, 60-email corpus.

---

## 10. Failure cases

| Case | Behavior |
|---|---|
| No load number | Fall through to PO/BOL/PRO (041/042 work) |
| PO/BOL → multiple loads | `ambiguous` + candidates (**EXISTS**, rarely in sample) |
| Wrong carrier extracted | Does not overturn MF resolve; mention is informational (**FRAGILE**) |
| Scanned/blank POD | `blank_page` / `vision_blocked` → unknown POD facts |
| Garbage OCR with ≥20 chars | May mark `readable` incorrectly (**FRAGILE**) |
| POD contradicts email | Deterministic discrepancies + unknown ground truth (035) |
| Stale FreightPro | Assumption string only; no per-field `as_of` (**PARTIAL**) |
| Anthropic down | Fallback packet, `llm_status=error` |
| Malformed JSON | `ModelResponseError` → fallback |
| Hallucinated claim $ | Liability phrases filtered; **$ amounts NOT specially scrubbed** (**PARTIAL**) |
| Driver PII in prompt | Gate + tests for known FreightPro drivers |
| Multiple MF requests | `ambiguous` + multi_load_ids |
| Dual intent, one MF | **MISSING** |
| No attachment | Escalate reason |
| Multiple attachments | First POD-named PDF only (**PARTIAL**) |
| Forwarded third-party info | **MISSING** special handling |
| Low confidence / disputed | Escalate; still may attach best-guess load when ambiguous-with-conflict |

---

## 11. Documentation vs code

| Doc | Says | Match? |
|---|---|---|
| **README** | 3-command demo, dry-run, eval, analysis layout, PII≠DPA | Matches |
| **DESIGN** | Claims-first, HITL, conflicts, metrics, weeks 3–6 Graph/review/quotes | Weeks 3–6 are **plan only** — **MISSING** in code (correctly framed as roadmap) |
| **DESIGN** | Vision off by default | Matches |
| **NOTES** | AI overrides, next steps | Matches; time split is approximate |

**Mismatch to watch in interview:** DESIGN architecture says vision may run after blank detect — code only runs vision if `MERIDIAN_ALLOW_POD_VISION` is set. README is clearer. Minor wording drift only.

---

## 12. Top 10 interviewer criticisms

1. Unique MF match ≠ correct shipper — no From-domain corroboration.
2. Partial OCR can look “readable.”
3. Carrier mention matching is brittle.
4. No dollar/settlement-amount guard beyond a few phrases.
5. Multi-attachment inventory incomplete.
6. Forwards / unlabeled PII residual risk.
7. Dual-intent emails under-handled.
8. Eval 8/8 is a narrow spot-check, easy to oversell.
9. No review UI / send control plane beyond a boolean.
10. Reloading full CSVs per email — demo-grade, not production-shaped.

---

## 13. Fifteen hard interview questions

1. Prove wrong-load can’t be unique-and-confident.
2. Show the exact Anthropic payload for a driver-named email.
3. Why is vision off? When would you turn it on?
4. What happens if PO matches three loads? Demo it.
5. How do you stop a $12k “we’ll pay” draft?
6. ETL is 14h stale — where is that in the packet?
7. Why claims not quotes? Defend against Dana.
8. What breaks if we feed a `quotes@` email?
9. How does multi-attachment selection work?
10. What’s the difference between `analysis` and `decision`?
11. Can the model set `needs_human=false`? Show the code.
12. What does 8/8 eval *not* measure?
13. How would you change this if Legal forbids Anthropic until DPA?
14. Where would Graph API plug in without rewriting the core?
15. Live: change constraint “must auto-send high-confidence tracking” — how does your architecture bend?

---

## FINAL HANDOFF

* **Architecture:** Synchronous CLI pipeline: parse → resolve → POD → redact/gate → optional Anthropic → `ActionPacket` (analysis + decision). No send, no FreightPro write, no Graph.
* **Data flow:** `.eml` + CSV snapshot → identifiers → load/shipper/carrier join → discrepancies/unknowns → redacted model context → packet files under `output/`.
* **LLM flow:** One primary `messages.create` for classify/draft; optional gated vision call. JSON validated; invented IDs scrubbed; failures fall back.
* **PII flow:** FreightPro lexicon + regex redact → outbound gate fail-closed → vision blocked by default. DPA **not** solved.
* **Strongest parts:** Claims scope judgment; 035 contradiction demo; identity conflict/multi-MF; blank POD honesty; HITL forced; PII gate tests; analysis separation.
* **Weakest parts:** False confidence on unique IDs; OCR threshold; $ hallucination; multi-attach/forwards; eval oversell risk; no review/send product.
* **Top improvements:** Shipper corroboration; attachment inventory; amount scrub; POD readability heuristic; fixture for multi-PO ambiguity; `as_of` on FreightPro facts.
* **Important files:** `pipeline.py`, `resolve_load.py`, `pod.py`, `pii_gate.py`, `agent.py`, `analysis.py`, `eval.py`, `DESIGN.md`, `README.md`.
* **Demo commands:**

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python -m meridian_claims process data/emails/035.eml
# also: --dry-run ; process 036 ; eval --dry-run ; pytest -q
```
