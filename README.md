# Meridian Freight — claims triage (working slice)

**What this is:** a narrow end-to-end demo for the FDE takehome.  
Inbound `claims@` email → FreightPro lookup + POD compare → **coordinator action packet** (JSON + markdown).

**What this is not:** production inbox automation, auto-send, FreightPro writes, or 90% automation.

Client plan: [DESIGN.md](DESIGN.md) · Reviewer notes: [NOTES.md](NOTES.md) · Data dictionary: [data.md](data.md)

---

## Cold clone → working demo (≤3 commands)

From the repo root (Python 3.10+):

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
# API key — either works (shell wins if both are set):
export ANTHROPIC_API_KEY=sk-ant-...          # Option A: shell
# or: put ANTHROPIC_API_KEY=... in .env      # Option B: gitignored .env (see .env.example)
python -m meridian_claims process data/emails/035.eml
```

**Expected result:** prints a one-line summary and writes:

- `output/035.json` — full action packet  
- `output/035.md` — coordinator-readable view  
- `output/035.decision.json` — PII-safe audit/decision record  

Open `output/035.md` and read the sections **What do we know? / What is inconsistent? / Why human review? / Draft response**.

### Without an API key (deterministic path)

```bash
python -m meridian_claims process data/emails/035.eml --dry-run
```

Live `process` without `ANTHROPIC_API_KEY` exits **2** with a clear message.  
Put the key in a gitignored `.env` (see `.env.example`) or `export ANTHROPIC_API_KEY=...`. The CLI loads `.env` if present; an already-exported shell variable wins.

### Optional checks

```bash
python -m meridian_claims eval --dry-run          # known-sample claims@ regression (8 fixtures)
python -m meridian_claims eval --all --dry-run    # all 60 emails; writes output/eval_all.{json,md}
pytest -q                                         # unit tests; no API key required
python -m meridian_claims review                  # local coordinator UI at http://127.0.0.1:8765/
```

**Eval modes:** `--dry-run` skips Anthropic (no API key). Without `--dry-run`, live eval invokes model calls when the pipeline reaches the model boundary (`ANTHROPIC_API_KEY` required). `--all` is a **behavioral** run over every sample `.eml` (does it process without crashing / does HITL stay on?); it is **not** an accuracy benchmark. Only the eight `EXPECTED_LOADS` fixtures have ground-truth load labels.

**About the 8/8 result:** The 8/8 result is a known-sample regression check against the eight labeled claims fixtures supplied with the assignment. It is not a statistically meaningful production accuracy estimate. It does not measure draft quality, live Anthropic behavior, or unseen mail.

**Cost / latency:** each packet’s `decision` and `usage` record processing duration (`perf_counter`), whether a model call occurred, Anthropic token usage when returned, and **estimated** model cost from configurable rates (`MERIDIAN_PRICE_INPUT_PER_MTOK` / `MERIDIAN_PRICE_OUTPUT_PER_MTOK`). Costs are estimates, not invoices.

**Review UI:** presentation-only over `output/*.json`. Accept/Reject updates browser `localStorage` only — **does not send email**, call models, or modify ActionPackets.

---

## What to demo

| Email | Shows |
|---|---|
| `data/emails/035.eml` | Damage claim; FreightPro/POD look clean; shipper alleges damage; `JBHT` → JB Hunt |
| `data/emails/036.eml` | Shortage; blank/placeholder POD → `blank_page`, escalate |
| `data/emails/041.eml` | No load number; resolve via PO → `MF-10032` |
| `data/emails/042.eml` | Resolve via BOL → `MF-10034` |

Example CLI line (dry-run):

```text
[035] damage | resolve=resolved/LoadNumber | load=MF-10487 | pod=text | needs_human=True
```

Example eval table (dry-run): **8/8** matches against a **hardcoded expected-load map** for the eight sample `claims@` emails. **The 8/8 result is a known-sample regression check against the eight labeled claims fixtures supplied with the assignment. It is not a statistically meaningful production accuracy estimate.** It does not measure draft quality, live Anthropic behavior, or unseen mail.

---

## How the packet is organized

The `analysis` object separates sources on purpose:

1. **FreightPro facts** (read-only snapshot + source/freshness metadata)  
2. **Email facts** (extracted IDs / mentions; secondary intents / forward flags when present)  
3. **POD facts** (or explicit unknown)  
4. **Comparisons / discrepancies** (deterministic)  
5. **Unknowns** (null + why a human is needed)  
6. **LLM interpretation** (suggestion only)  
7. **Draft response** (`auto_send: false`)

Also on the packet: **attachment inventory** (selected POD vs present-not-processed) and a **decision** audit sidecar.

`needs_human` is always true on this claims path. Nothing is sent.

---

## Safety behaviors in this slice (deterministic)

- Sender ↔ shipper ContactEmail/domain corroboration (`matched` / `conflict` / `unavailable`); carrier mention alone does not overturn LoadNumber  
- Model payload gets `sender_corroboration`, not raw From/To addresses  
- Draft liability language filter + invented dollar-amount block  
- Multi-intent (claims + tracking/invoice/quote) → escalate; secondary request not executed  
- Forwarded/third-party markers → fail-closed Anthropic skip; envelope From only for shipper trust  
- POD text quality gate (garbage OCR → unreadable, no invented POD facts)  
- Multi-POD candidates → no silent pick; escalate  

---

## Repo map

```text
meridian_claims/     application code (python -m meridian_claims)
data/                sample emails, attachments, FreightPro CSVs
tests/               unit tests
DESIGN.md            client design (≤3 pages)
NOTES.md             reviewer notes (≤1 page)
requirements.txt
```

| Module | Role |
|---|---|
| `pipeline.py` | End-to-end orchestration |
| `resolve_load.py` | MF/PO/BOL/PRO → FreightPro + shipper corroboration |
| `pod.py` | PDF text / quality / blank-page; attachment inventory; vision blocked by default |
| `evidence.py` | Discrepancies + draft money/liability sanitize |
| `forward_mail.py` | Forward markers + fail-closed policy |
| `analysis.py` | Separated facts / unknowns / AI / draft |
| `pii_gate.py` | Outbound redaction + fail-closed residual scan |
| `agent.py` | Anthropic classify + draft |
| `eval.py` | Known-sample claims@ regression + optional all-60 behavioral harness |
| `pricing.py` | Configurable estimated model cost from Anthropic token usage |
| `review_server.py` / `review_ui/` | Minimal local coordinator review UI (no send) |

---

## Constraints (honest)

- FreightPro access in this repo is the **provided CSV snapshot** (dated **2026-08-26**), treated as read-only. Packets label Status as **recorded**, not live tracking; ~14h reporting lag is called out. **Per-field `as_of` timestamps are not in the export** and are not invented.  
- DriverName/DriverPhone are Legal PII. Text payloads are redacted and scanned before Anthropic; **this is leakage mitigation, not a DPA**.  
- POD **image** vision is **off** unless `MERIDIAN_ALLOW_POD_VISION=1` (pixels cannot be safely redacted).  
- Default model: `claude-sonnet-4-5-20250929` (override with `ANTHROPIC_MODEL`).  
- Do not commit API keys. `.env` is gitignored; `.env.example` is a template. The CLI loads `.env` from the repo root if present.
