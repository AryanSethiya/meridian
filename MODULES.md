# Module guide — Meridian claims slice

Short map of every Python module: what it does, and how they connect.  
Client-facing design lives in [`DESIGN.md`](DESIGN.md).

---

## End-to-end flow

```mermaid
flowchart TD
  CLI["cli.py / __main__"] --> PIPE["pipeline.py"]
  PIPE --> PARSE["email_parser.py"]
  PIPE --> FP["freightpro.py"]
  PIPE --> RES["resolve_load.py"]
  PIPE --> POD["pod.py"]
  PIPE --> FWD["forward_mail.py"]
  PIPE --> EV["evidence.py"]
  PIPE --> RED["redact.py"]
  PIPE --> GATE["pii_gate.py"]
  PIPE --> AGENT["agent.py"]
  PIPE --> AN["analysis.py"]
  PIPE --> OBS["observability.py"]
  PIPE --> REN["render.py"]
  AGENT --> PRICE["pricing.py"]
  PIPE --> MOD["models.py"]
  EVAL["eval.py"] --> PIPE
  REV["review_server.py + review_ui/"] --> OUT["output/*.json"]
  REN --> OUT
```

**Idea:** deterministic work first (parse → resolve → POD → compare → redact), then optional LLM, then a human-review packet. Nothing auto-sends.

---

## Pipeline stages (what runs for one email)

```mermaid
flowchart LR
  subgraph deterministic [Deterministic]
    A[Parse] --> B[IDs + FreightPro]
    B --> C[POD]
    C --> D[Evidence]
    D --> E[Forward / multi-intent]
    E --> F[Redact + PII gate]
  end
  subgraph model [LLM optional]
    F --> G{Call Anthropic?}
    G -->|yes| H[Classify + draft]
    G -->|no / blocked| I[Heuristic fallback]
  end
  subgraph out [Output]
    H --> J[analysis + ActionPacket]
    I --> J
    J --> K[JSON / MD / decision]
  end
```

---

## Modules (one-liners + detail)

### Entry & orchestration

| Module | Role |
|---|---|
| `__main__.py` | `python -m meridian_claims` → calls CLI |
| `cli.py` | Commands: `process`, `eval`, `review` |
| `pipeline.py` | End-to-end conductor for one `.eml` |

**`pipeline.py`** wires every step, always sets `needs_human=True`, writes the packet. Dry-run skips Anthropic (`use_llm=False`).

```mermaid
flowchart TD
  P["process_email(.eml)"] --> S1["parse + FreightProDB.load"]
  S1 --> S2["extract IDs → resolve_load → shipper check"]
  S2 --> S3["decide POD → process_pod"]
  S3 --> S4["discrepancies + forward + multi-intent"]
  S4 --> S5["redact → PII gate → agent or fallback"]
  S5 --> S6["analysis bundle + decision record"]
  S6 --> S7["write_packet → return ActionPacket"]
```

---

### Data in

| Module | Role |
|---|---|
| `email_parser.py` | `.eml` → headers, body, attachments |
| `freightpro.py` | Read-only CSV snapshot: loads / carriers / shippers + indexes |

**`email_parser.py`** — RFC 822 parse; optional save of attachments under `output/attachments/<id>/`.

**`freightpro.py`** — In-memory DB. Indexes by MF / PO / BOL / PRO / MC / SCAC. Joins shipper+carrier on enrich. Does **not** load `rates.csv` or `carrier_availability.csv`.

```mermaid
flowchart LR
  CSV["loads / carriers / shippers CSVs"] --> DB["FreightProDB"]
  DB --> IDX["by_load / by_po / by_bol / by_pro / by_mc / by_scac"]
  IDX --> RES["resolve_load / find_carrier_mention"]
```

---

### Resolve load & POD

| Module | Role |
|---|---|
| `resolve_load.py` | Extract MF/PO/BOL/PRO; look up load; sender↔shipper check |
| `pod.py` | Pick POD attachment; extract text / blank / vision-blocked |
| `forward_mail.py` | Detect forwards; fail-closed model policy |

**`resolve_load.py`** preference: LoadNumber → PO → BOL → PRO. Cross-checks other IDs. Multi-MF → ambiguous. Shipper corroboration: `matched` / `conflict` / `unavailable` (carrier mention never overturns LoadNumber).

**`pod.py`** — Deterministic selection (filename contains `pod`, or sole PDF). Multi-POD → escalate. Readable text → `mode=text`; blank scan → `blank_page`; vision off by default.

```mermaid
flowchart TD
  ATT["Attachments"] --> DEC{"decide_pod_attachments"}
  DEC -->|1 POD PDF| PROC["process_pod_attachment"]
  DEC -->|2+ PODs| ESC["ambiguous — human"]
  DEC -->|no PDF| NOP["no_pod"]
  PROC --> Q{Text quality?}
  Q -->|ok| TXT["mode=text"]
  Q -->|blank| BL["mode=blank_page"]
  Q -->|image only| VIS["vision_blocked or vision"]
```

---

### Evidence & safety

| Module | Role |
|---|---|
| `evidence.py` | Deterministic discrepancies + draft sanitize |
| `redact.py` | Pattern-mask phones, emails, driver/receiver labels |
| `pii_gate.py` | Last check before Anthropic; block residual PII; vision default off |

**`evidence.py`** — e.g. damage email vs clean POD; POD IDs vs load; equipment mismatch; strip liability language; block invented `$` amounts.

**`redact.py` → `pii_gate.py`**

```mermaid
flowchart LR
  RAW["Context for model"] --> R["redact.py masks"]
  R --> G["pii_gate deep-redact + scan"]
  G -->|clean| OK["prepare_text_payload → agent"]
  G -->|residual PII| BLOCK["PIIGateError — no API call"]
```

---

### Model & analysis

| Module | Role |
|---|---|
| `agent.py` | Anthropic classify + draft; JSON parse; usage |
| `analysis.py` | Separate freightpro / email / pod / comparisons / unknowns / AI / draft |
| `pricing.py` | Estimated $ from tokens × env rates |

**`agent.py`** — Requires `ANTHROPIC_API_KEY`. System prompt: don’t invent facts, don’t approve/deny claims. Returns JSON interpretation fields.

**`analysis.py`** — Filing cabinet for the packet:

```mermaid
flowchart TD
  subgraph facts [What we know]
    FP[freightpro_facts]
    EM[email_facts]
    PD[pod_facts]
  end
  facts --> CMP[comparisons]
  CMP --> UNK[unknowns]
  UNK --> AI[llm_interpretation]
  AI --> DR[draft_response auto_send false]
```

Also scrub invented MF/PO/BOL from model prose via `validate_llm_against_known_facts`.

---

### Output, audit, eval, UI

| Module | Role |
|---|---|
| `models.py` | Dataclasses: `ActionPacket`, `Resolution`, `PodResult`, … → `to_dict()` |
| `render.py` | Write `*.json`, `*.md`, `*.decision.json`; CLI summary |
| `observability.py` | PII-safe decision/audit record |
| `eval.py` | 8-claim fixture regression + optional all-60 behavioral run |
| `review_server.py` | Local HTTP server for review UI |
| `review_ui/` | Static HTML/JS/CSS over `output/*.json` (no send) |

```mermaid
flowchart TD
  AP["ActionPacket"] --> REN["render.py"]
  REN --> J["output/ID.json"]
  REN --> M["output/ID.md"]
  REN --> D["output/ID.decision.json"]
  J --> UI["review_ui via review_server"]
  EVAL["eval.py"] -->|"process_email × N"| AP
```

**`eval.py`**
- Default: 8 `claims@` emails vs `EXPECTED_LOADS` → known-sample **8/8** regression (not production accuracy).
- `--all`: all 60 sample emails behavioral run.

**`review_server.py` + `review_ui/`** — `python -m meridian_claims review` → browse packets at `127.0.0.1:8765`. Read-only; never emails or calls models.

---

## Data this slice uses

| Path | Used? |
|---|---|
| `data/emails/*.eml` | Yes |
| `data/attachments/*.pdf` (PODs) | Yes |
| `data/freightpro/loads.csv` | Yes |
| `data/freightpro/carriers.csv` | Yes |
| `data/freightpro/shippers.csv` | Yes |
| `data/freightpro/rates.csv` | No (quotes) |
| `data/carrier_availability.csv` | No (dispatch) |
| `data/attachments/*.xls` | Inventoried only, not parsed |

---

## Quick “who owns what”

| Concern | Module(s) |
|---|---|
| CLI | `cli.py` |
| Orchestration | `pipeline.py` |
| Email bytes → fields | `email_parser.py` |
| TMS snapshot | `freightpro.py` |
| Which load? | `resolve_load.py` |
| Which POD / can we read it? | `pod.py` |
| Conflicts + safe draft | `evidence.py` |
| Forwards | `forward_mail.py` |
| Mask PII | `redact.py` |
| Block dirty payloads | `pii_gate.py` |
| Call Claude | `agent.py` |
| Fact buckets | `analysis.py` |
| Schema | `models.py` |
| Write files | `render.py` |
| Audit + $ | `observability.py`, `pricing.py` |
| Regression / all-60 | `eval.py` |
| Local review UI | `review_server.py`, `review_ui/` |
