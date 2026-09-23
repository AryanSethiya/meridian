# ⚡ Meridian Freight Claims Triage: Golden Interview Cheat Sheet
> **Live Interview Heads-Up Display (HUD)** — Keep this open on your secondary screen. Short, crisp, high-yield, and backed by this repo’s code + stakeholder numbers.  
> **Repo:** https://github.com/AryanSethiya/meridian-freight-claims-triage  
> **Mantra:** Deterministic owns truth. LLM owns interpretation + draft. Human owns send.

---

## 🗺️ 1. Architecture Flow & Packet Lifecycle

### 1A. Complete Claims Pipeline (Excalidraw Flow)

```
 1. INBOUND .eml
┌────────────────────────────────────────┐
│ claims@ email + POD PDF attachment(s)  │
└───────────────────┬────────────────────┘
                    │
                    ▼
 2. PARSE (email_parser.py) -------------------------> [headers, body, attachments]
┌─────────────────────────────────────────────────────────────────────────────┐
│ • Subject / From / To / Date / body                                         │
│ • Attachments written under output/attachments/<email_id>/                  │
└───────────────────┬─────────────────────────────────────────────────────────┘
                    │
                    ▼
 3. RESOLVE LOAD + SHIPPER CORROBORATION -------------> [emit: resolution.*]
┌─────────────────────────────────────────────────────────────────────────────┐
│ • Extract IDs: MF-##### → PO-####### → BOL → PRO (resolve_load.py)         │
│ • FreightProDB read-only CSV indexes (freightpro.py)                        │
│ • Unique + consistent → status=resolved                                     │
│ • Multi-MF / ID conflict / sender≠shipper → status=ambiguous (+ best guess) │
│ • Shipper corroboration: From domain ↔ ContactEmail → matched|conflict|n/a │
│ • Gate: Resolution.is_authoritative_for_action() ⇔ status == "resolved"    │
└───────────────────┬─────────────────────────────────────────────────────────┘
                    │
                    ▼
 4. DECIDE / PROCESS POD (pod.py) --------------------> [emit: pod.*]
┌─────────────────────────────────────────────────────────────────────────────┐
│ • Inventory all attachments; strong POD = PDF with "pod" in name            │
│ • Exactly one candidate → select; multi-POD → escalate (no silent pick)     │
│ • Text layer + quality gate → mode=text / readable                          │
│ • Blank/placeholder scan → mode=blank_page (skip vision)                    │
│ • Vision ONLY if: no usable text ∧ not blank ∧ MERIDIAN_ALLOW_POD_VISION=1 │
│ • Default: vision_blocked (pixels can’t be redacted; DPA pending)           │
└───────────────────┬─────────────────────────────────────────────────────────┘
                    │
                    ▼
 5. EVIDENCE + GATES (evidence / forward_mail) -------> [emit: discrepancies]
┌─────────────────────────────────────────────────────────────────────────────┐
│ • damage_vs_clean_pod · pod_vs_load IDs · email_vs_load equipment/date/DNU  │
│ • Forward markers → model fail-closed (envelope From only for shipper)      │
│ • Multi-intent (claims + tracking/invoice/quote) → escalate; don’t execute  │
└───────────────────┬─────────────────────────────────────────────────────────┘
                    │
                    ▼
 6. REDACT + PII GATE (redact.py / pii_gate.py) ------> [emit: pii.passed|blocked]
┌─────────────────────────────────────────────────────────────────────────────┐
│ • Lexicon from FreightPro DriverName/DriverPhone + pattern masks            │
│ • Residual known PII → fail-closed (no Anthropic call)                      │
│ • Model sees sender_corroboration — NOT raw From/To                         │
│ • Mitigation ≠ Legal DPA                                                    │
└───────────────────┬─────────────────────────────────────────────────────────┘
                    │
                    ▼
 7. LLM (optional) OR FALLBACK -----------------------> [emit: llm.ok|dry_run|blocked]
┌─────────────────────────────────────────────────────────────────────────────┐
│ • agent.classify_and_draft — interpretation + draft ONLY                    │
│ • Must not invent null facts / IDs / $ amounts                              │
│ • evidence.sanitize_claim_draft — strip liability language + invented $     │
│ • Dry-run / PII blocked / API error → heuristic fallback draft              │
└───────────────────┬─────────────────────────────────────────────────────────┘
                    │
                    ▼
 8. ACTION PACKET → HITL ----------------------------> [never auto-send]
┌─────────────────────────────────────────────────────────────────────────────┐
│ • analysis: freightpro_facts · email_facts · pod_facts · discrepancies      │
│             · unknowns · AI suggestion · draft · why HITL                   │
│ • output/{id}.{json,md,decision.json}                                       │
│ • needs_human=True ALWAYS · draft auto_send: false                          │
│ • review UI = same process_email behind HTTP (localStorage Accept/Reject)   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1B. Demo Email Telemetry Map (what each fixture proves)

```
                     [ process_email(.eml) ]
                              │
     ┌────────────┬───────────┼───────────┬────────────┬────────────┐
     ▼            ▼           ▼           ▼            ▼            ▼
   035.eml     036.eml     041.eml     042.eml      058.eml     (conflict)
     │            │           │           │            │            │
 Damage vs     Blank POD   Resolve via  Resolve via  Forward +    MF + wrong PO
 clean POD     escalate    PO→MF-10032  BOL         multi-MF     → ambiguous
 text POD      blank_page              fail-closed  gate=False
 resolved      resolved
 matched       matched
```

---

## ⚡ 2. The 30-Second Elevator Pitch

> *“Meridian has four shared ops inboxes and ~800 emails/day. Dana wants 90%+ automation; Marcus’s real pain is **damage claims** — ~4% of volume, ~35% of coordinator time (~20 min each comparing POD to FreightPro) — and he will **not** let an AI reply reach a shipper unread. Priya has no FreightPro API, a ~14h-stale ETL, dirty carrier names, Legal DPA blockers, and writes 6–8 weeks out.
>
> I scoped a **claims intake + POD↔FreightPro triage assistant**: email in → resolve load → compare POD → produce a **human-review action packet**. Deterministic first, LLM second for classify/draft only, **never auto-send**, never write FreightPro.
>
> Strongest demos: `035` damage-vs-clean-POD contradiction, `036` blank-POD honesty, shipper domain corroboration, forward/multi-intent fail-closed, and PII gates that are mitigation — not a DPA.”*

Then offer: *“Want design first, or should I run `035` end-to-end?”*

---

## 📊 3. The “Numbers That Matter” Matrix (Commit to Memory)

| Metric | Baseline / Constraint | Target / Result | Verification | Confounder & Defense |
| :--- | :--- | :--- | :--- | :--- |
| **Claims time ROI** | ~**20 min**/claim POD↔FreightPro | Toward **&lt;5 min** to decision-ready packet | DESIGN success metrics; Marcus’s stated burn | **Confounder:** Packet ≠ closed claim.<br>**Defense:** Metric is “email → reviewable packet,” not full settlement. |
| **Volume mix** | Claims ~**4%** of mail | Drive ~**35%** of coordinator time | Stakeholder brief / DESIGN | **Defense:** That’s why claims beat 4-inbox breadth under ~8h. |
| **Correct email→load** | Dirty IDs, multi-MF, stale ETL | **≥95%** on labeled validation set | Future Marcus-labeled set | **Confounder:** Repo **8/8** looks like accuracy.<br>**Defense:** 8/8 = known-sample **fixture regression** only — say it first. |
| **Draft ≤1-edit accept** | N/A (prototype) | **≥70%** | Coordinator review metrics (roadmap) | **Defense:** Not claimed from current eval. |
| **Wrong-load / false-send** | Catastrophic if wrong | **≈0** before any auto-send | Gate + HITL always | **Defense:** `is_authoritative_for_action()`; claims never unsupervised. |
| **Fixture regression** | 8 labeled `claims@` emails | **8/8** load match dry-run | `eval --dry-run` / `EXPECTED_LOADS` | **Defense:** Not production accuracy; not draft quality. |
| **Unit safety net** | Resolve / redact / PII / forwards | **~94** pytest | `pytest -q` (no API key) | **Defense:** Behavioral locks, not live Anthropic quality. |
| **FreightPro freshness** | Snapshot **2026-08-26**, ~**14h** lag | Labeled on facts; escalate disputes | `freightpro_source_meta` on packet | **Defense:** Status/PODReceived = recorded-as-of, not live tracking. |
| **IT capacity** | **20%** eng; writes **6–8 wks** | Read-only CSV path now | DESIGN / Priya constraints | **Defense:** No TMS rebuild; Graph poller is next, not built. |

---

## 🛡️ 4. The 5 Production AI Pillars at a Glance

```
┌───────────────────────────────┬─────────────────────────────────────────────────────────────────────────────┐
│ Pillar                        │ Meridian Claims Implementation                                              │
├───────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ 1. Deterministic truth layer  │ • MF→PO→BOL→PRO resolve + ID cross-checks → ambiguous on conflict          │
│                               │ • Shipper corroboration matched|conflict|unavailable                        │
│                               │ • POD inventory / text quality / blank_page (no invented delivery facts)    │
│                               │ • evidence.py: damage-vs-clean, POD IDs vs load, equipment/date/DNU         │
├───────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ 2. Guardrails & security      │ • redact.py + pii_gate residual scan fail-closed before Anthropic           │
│                               │ • Model payload: sender_corroboration, not raw From/To                      │
│                               │ • POD vision off by default (MERIDIAN_ALLOW_POD_VISION); pixels unsafe      │
│                               │ • Forward third-party → model fail-closed; envelope From only               │
│                               │ • Mitigation ≠ DPA                                                          │
├───────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ 3. LLM bounded role           │ • agent.classify_and_draft only after facts + PII gate                      │
│                               │ • Must not invent null facts / IDs; invented IDs scrubbed                   │
│                               │ • Draft sanitize: liability language + invented $ amounts blocked           │
│                               │ • Dry-run / blocked / error → heuristic fallback; llm_status explicit       │
├───────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ 4. HITL & automation gate     │ • needs_human=True always on claims path; auto_send: false                  │
│                               │ • Resolution.is_authoritative_for_action() ⇔ status=="resolved"             │
│                               │ • Ambiguous keeps best-guess load for humans — NOT for future auto-action   │
│                               │ • Multi-intent: continue claims only; do not execute secondary asks         │
├───────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ 5. Demo / eval / deploy path  │ • CLI: process / eval / review                                              │
│                               │ • eval --dry-run: 8-fixture regression; --all: behavioral crash/HITL        │
│                               │ • Local review UI reuses process_email; Accept/Reject = localStorage only   │
│                               │ • Next (MISSING intentional): Graph poller on claims@, wrong-load metrics   │
└───────────────────────────────┴─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔍 5. Code Anchors & Exact Files to Name-Drop

| When they ask… | Open / say |
|---|---|
| “Walk me through one email” | `pipeline.py` → `process_email()` |
| “How do I run this?” | `cli.py` — `process` / `eval` / `review` |
| “How do you find the load?” | `resolve_load.py` + `apply_shipper_corroboration` |
| “Where is FreightPro?” | `freightpro.py` + `data/freightpro/*.csv` |
| “How do PODs work?” | `pod.py` — `decide_pod_attachments` / `process_pod_attachment` |
| “Where do comparisons live?” | `evidence.py` — especially `damage_vs_clean_pod` |
| “Forwards?” | `forward_mail.py` |
| “PII / Legal?” | `redact.py` + `pii_gate.py` (`vision_allowed`) |
| “Where is the LLM?” | `agent.py` → `classify_and_draft` |
| “Packet structure?” | `analysis.py` + `models.py` (`ActionPacket`, gate) |
| “What does the coordinator see?” | `render.py` → `output/{id}.{json,md}` |
| “Eval?” | `eval.py` — `EXPECTED_LOADS` (8 fixtures) |
| “Review UI?” | `review_server.py` + `review_ui/` → same pipeline |
| “Cost?” | `pricing.py` (estimated token $) |

**Four tabs to keep open:** `pipeline.py` · `resolve_load.py` · `pod.py` · `agent.py`

**Unused by design:** `rates.csv`, `carrier_availability.csv` (quote/dispatch — out of slice).

---

## 🎯 6. Top 5 Killer “Gotcha” Questions & Rapid Answers

#### Q1: “Why not Dana’s 90% automation / 4-inbox agent?”
> **Answer:** *“The evaluation isn’t ‘build Dana’s agent.’ It’s pick the highest-ROI path that respects Marcus’s unread-send ban and Priya’s constraints. Claims are ~4% of mail but ~35% of time, naturally HITL, and the sample data has the hard wrinkles. Wrong send costs more than a week of coordinator time.”*

#### Q2: “Is 8/8 your accuracy?”
> **Answer:** *“No. It’s a known-sample regression on eight labeled claims fixtures (`EXPECTED_LOADS`). It does not measure draft quality, live Anthropic behavior, or unseen mail. Production bar I’d defend is ≥95% email→load on a Marcus-labeled set, with wrong-load ≈ 0 before any auto-send.”*

#### Q3: “Humans always review — so what’s `is_authoritative_for_action()` for?”
> **Answer:** *“Today HITL is always on — the gate isn’t a second workflow. Ambiguous resolutions still attach a best-guess `load` for coordinator context. The gate separates ‘we showed a guess’ from ‘this load is authoritative for future send/write.’ Only `status == resolved` returns True. Presence of `load` ≠ authority.”*

#### Q4: “Why is POD vision off by default?”
> **Answer:** *“Text we can redact. POD image pixels can contain receiver names, phones, signatures — you can’t scrub those safely before upload. Default is `vision_blocked` until Legal approves a DPA and `MERIDIAN_ALLOW_POD_VISION=1`. Blank scans never reach the model — we mark `blank_page` and escalate.”*

#### Q5: “Where did AI coding tools push you wrong?”
> **Answer:** *“Four-inbox/auto-send → I forced claims + HITL. ‘OCR succeed’ on blank PODs → blank/unreadable, skip vision. Free-text carrier trust → MF/PO/BOL/PRO + MC/SCAC. Redaction as Legal-done → mitigation ≠ DPA. Skipped ID cross-checks → conflicting MF/PO becomes `ambiguous`, not silent resolve. I can change any module live without chat history.”*

---

## ⚖️ 7. The Thoughtful Engineering Tradeoff (Your Story)

> ***The Tradeoff I Accepted:*** A **deterministic-first, HITL claims slice** over Dana’s autonomous 4-inbox / auto-send agent.
>
> ***The Rationale:*** In broker claims, wrong-load and unread AI replies to shippers are catastrophic. Dirty/stale FreightPro + missing DPA make unsupervised send indefensible in week 1. A linear pipeline (parse → resolve → POD → evidence → redact → optional draft) bounds non-determinism to classify/draft only, with explicit fail-closed exits.
>
> ***What I’d do next (not built):*** Graph poller on `claims@` → wrong-load logging with Marcus → tracking HITL drafts → maybe limited auto-send on high-confidence **tracking only** after metrics. Claims stay human-gated. Any future auto-action must pass `is_authoritative_for_action()`, no discrepancies, matched shipper corroboration, and wrong-load ≈ 0.

---

## 🧪 8. Live Demo Cheat Card

```bash
source .venv/bin/activate
python -m meridian_claims process data/emails/035.eml --dry-run   # no key
python -m meridian_claims process data/emails/035.eml             # needs ANTHROPIC_API_KEY
open output/035.md   # or cat
python -m meridian_claims eval --dry-run
pytest -q
python -m meridian_claims review   # http://127.0.0.1:8765/  (--port 8766 if busy)
```

| Email | Punchline |
|---|---|
| `035` | Damage claim; FreightPro/POD clean; discrepancy = allegation vs clean POD; JBHT→JB Hunt |
| `036` | Shortage; blank POD → `blank_page`, escalate |
| `041` | No MF; resolve via PO → `MF-10032` |
| `042` | Resolve via BOL |
| `058` | Forward + multi-MF → model fail-closed |

**035 verbal trace:** parse prairiegraincooper → resolve MF-10487 matched → POD text “no exceptions” → damage-vs-clean discrepancies → redact → optional LLM → packet `needs_human=True`.

---

## 🧨 9. Live Constraint Drill Pattern

**Template (10s):** Restate today → locate file → tradeoff → edit → prove with one email.

| They say… | You refuse / gate with… | Prove |
|---|---|---|
| Auto-send if confidence &gt; 0.9 | No unsupervised claims; gate + metrics first | `035` still escalates damage-vs-clean |
| Skip HITL for shortage | Blank POD (`036`) is exactly where AI is dangerous | `036` must escalate |
| Trust Status as live | Snapshot / 14h lag — escalate disputes | Packet freshness on `035` |
| Enable vision by default | Pixels ≠ redactable; DPA first | `pii_gate` / `vision_blocked` |
| Resolve by carrier name | Dirty names; wrong-load; don’t overturn MF | Keep MF→PO→BOL→PRO |

---

## 🧭 10. Stakeholder One-Liners

| Person | Tension | Your answer |
|---|---|---|
| **Dana (COO)** | 90% automation, move headcount to sales | HITL claims 6 weeks; measure minutes + wrong-load; auto-send later ≠ claims |
| **Marcus (Ops)** | 20 min/claim; every email has a wrinkle; no unread AI | Action packet + always human; never auto-send |
| **Priya (IT)** | No API, 14h lag, dirty names, DPA, 20% eng, writes 6–8 wks | Read-only CSV path; redact + vision off; Graph next; no TMS rebuild |

---

## ✅ Night-Before Checklist

- [ ] Restate Dana/Marcus/Priya in &lt;90s  
- [ ] Why claims slice (ROI + HITL + sample wrinkles)  
- [ ] Trace `035` verbally through `process_email`  
- [ ] Name LLM call (`agent.classify_and_draft`) + what it must not invent  
- [ ] Name one file: resolve · POD · PII · evidence · pipeline  
- [ ] Dry-run `035` once successfully  
- [ ] Know port 8765 / `--port 8766`  
- [ ] Say **8/8 ≠ production accuracy** before they do  
- [ ] Volunteer weaknesses: CSV scale · carrier free-text · stretch breadth · no Graph yet  

**Companions:** `INTERVIEW_PREP.md` · `INTERVIEW_CODE_MAP.md` · `INTERVIEW_LIVE_DRILLS.md` · `INTERVIEW_QA.md` · `DESIGN.md` · `NOTES.md`
