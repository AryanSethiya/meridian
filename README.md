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
export ANTHROPIC_API_KEY=sk-ant-...
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
The CLI reads the key from the environment only (does not auto-load `.env`).

### Optional checks

```bash
python -m meridian_claims eval --dry-run   # all 8 claims@ emails; load-resolution table
pytest -q                                 # unit tests; no API key required
```

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

Example eval table (dry-run): **8/8** expected load matches on sample `claims@` mail.

---

## How the packet is organized

The `analysis` object separates sources on purpose:

1. **FreightPro facts** (read-only snapshot)  
2. **Email facts** (extracted IDs / mentions)  
3. **POD facts** (or explicit unknown)  
4. **Comparisons / discrepancies** (deterministic)  
5. **Unknowns** (null + why a human is needed)  
6. **LLM interpretation** (suggestion only)  
7. **Draft response** (`auto_send: false`)

`needs_human` is always true on this claims path. Nothing is sent.

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
| `resolve_load.py` | MF/PO/BOL/PRO → FreightPro |
| `pod.py` | PDF text / blank-page detect; vision blocked by default |
| `analysis.py` | Separated facts / unknowns / AI / draft |
| `pii_gate.py` | Outbound redaction + fail-closed residual scan |
| `agent.py` | Anthropic classify + draft |
| `eval.py` | Claims@ harness |

---

## Constraints (honest)

- FreightPro access in this repo is the **provided CSV snapshot** (dated **2026-08-26**), treated as read-only.  
- DriverName/DriverPhone are Legal PII. Text payloads are redacted and scanned before Anthropic; **this is leakage mitigation, not a DPA**.  
- POD **image** vision is **off** unless `MERIDIAN_ALLOW_POD_VISION=1` (pixels cannot be safely redacted).  
- Default model: `claude-sonnet-4-5-20250929` (override with `ANTHROPIC_MODEL`).  
- Do not commit API keys. `.env` is gitignored; `.env.example` is a template only.
