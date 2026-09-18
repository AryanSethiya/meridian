# Design: Meridian Freight ops inbox — first 6 weeks

**To:** Dana Whitfield (COO), Marcus Oyelaran (Ops), Priya Natarajan (IT)  
**From:** Forward-deployed engineer  
**Re:** Working slice, 6-week plan, conflicts, metrics, asks  
**Data context:** FreightPro export dated **2026-08-26**, read-only

---

## The problem

Meridian brokers freight between shippers and carriers. Ops works four shared inboxes (`ops@`, `quotes@`, `claims@`, `dispatch@`). Volume is roughly 800 emails/day (1,500+ in weather events). Dana wants high automation so headcount can shift to sales. Marcus’s real pain is **damage claims**: ~4% of emails, ~35% of coordinator time — each claim burns ~20 minutes comparing a POD to FreightPro. He will not let an AI-written reply reach a shipper unread. Priya’s world: no FreightPro API, SQL Server 2012, read replica OK, nightly ETL ~14h stale, dirty carrier names, Graph API available, Legal requires a DPA before PII goes to a third party, write access is 6–8 weeks away, IT capacity is one engineer at 20%.

---

## Why the working slice is claims (not “all email”)

We build a **claims intake + POD↔FreightPro triage assistant**: email in → resolve load → compare POD → produce a **human-review action packet** (facts, discrepancies, unknowns, draft).  

**Why claims first:** highest time ROI, naturally human-gated (Marcus), and the sample data already contains the hard wrinkles (dirty IDs, blank PODs, clean POD vs damage allegation).  

**Why not 90% auto-send in week 1:** that goal conflicts with Marcus’s risk rule and with dirty/stale data. This repo demonstrates the claims slice only. It does **not** claim production readiness or 90% automation.

---

## Stakeholder conflicts and how we handle them

| Conflict | Handling |
|---|---|
| Dana: 90%+ automation / move 8 of 14 to sales vs Marcus: no unread AI replies; fear of rubber-stamping | **HITL drafts for the full 6 weeks on claims.** Measure minutes saved and wrong-load rate. Any later auto-send is limited, metric-gated, and **not** for claims. |
| Dana: “data is clean” vs Priya: dirty names, missing load numbers, 14h ETL lag | Design for dirty identity (MF → PO → BOL → PRO; MC/SCAC). Label FreightPro fields as snapshot/ETL, not live truth. |
| Headcount pressure vs 20% IT engineer + keep best coordinators on ops | No FreightPro rebuild/schema change. Read-only data path. Senior coordinators remain the review layer. |
| Legal: no PII to vendors without DPA; no DPA reviewed yet | Redact known DriverName/DriverPhone before text model calls; block POD image uploads by default. **Mitigation ≠ DPA approval.** |

---

## Architecture (working slice)

```text
.eml → parse → extract IDs → FreightPro read lookup
     → POD text / blank detect → PII gate
     → Anthropic (classify + draft) or deterministic fallback
     → action packet (analysis + decision audit) → human review
```

- **Deterministic first:** load resolution, POD/email comparisons, unknowns.  
- **LLM second:** interpretation + draft only; must not invent null facts; invented IDs are scrubbed.  
- **Never auto-sends. Never writes FreightPro.**  
- Packet `analysis` separates: FreightPro facts · email facts · POD facts · discrepancies · unknowns · AI suggestion · draft · why HITL.

---

## What we build in 6 weeks / what we do not

**In scope**

| Weeks | Deliverable |
|---|---|
| 1–2 | Claims slice (this repo): `.eml` → packet, HITL draft, read-only FreightPro |
| 3–4 | Graph watch on `claims@`; thin accept/edit/reject queue; wrong-load logging with Marcus |
| 5–6 | Optional HITL **tracking** status drafts; optional HITL **quote assist** — still no unsupervised send |

**Explicitly not built in 6 weeks:** full 4-inbox agent; auto-send; FreightPro writes/claim-file creation; training on Meridian data; “fixing” the availability Google Sheet; FreightPro schema changes.

---

## Human-in-the-loop, PII, read-only, stale data

- **HITL:** `needs_human` is required for claims. Drafts are suggestions. Coordinators send (or not) from their mail client / a future review queue — not this CLI.  
- **PII:** DriverName/DriverPhone are Legal PII. Text payloads are redacted and scanned before Anthropic; residual known PII fails closed. POD **vision is off by default**. Production use of customer PII with Anthropic still needs a **Legal-approved DPA**.  
- **Read-only:** FreightPro access is replica/export only until change board allows writes (6–8 weeks).  
- **Stale data:** Treat Status/PODReceived as of the snapshot/ETL. Packets state the 2026-08-26 context; disputed conditions escalate rather than “trust the flag.”

---

## Assumptions

- Sample CSVs stand in for the read replica.  
- Claims slice ignores rates.csv and carrier_availability (quote/dispatch concerns).  
- Blank/placeholder POD scans are escalated, not “OCR’d into truth.”  
- Anthropic only for model calls; missing key fails clearly.

---

## Success metrics (week 6)

| Metric | Target | Why |
|---|---|---|
| Minutes per claim (email → decision-ready packet) | Toward **under 5** from ~20 | ROI |
| Load resolution accuracy (when an ID exists) | **≥95%** | Wrong load is expensive |
| Draft accepted with ≤1 edit | **≥70%** | Assist quality |
| Wrong-load / false-send | **≈0** | Marcus |
| Review completion (opened/edited, not rubber-stamp) | Tracked | Safety |

If wrong-load rate is non-zero, we tighten — we do not expand automation.

---

## What we need from each of you

**Dana:** Define 6-week success as claims time saved + safe HITL, not 90% unsupervised send; protect Marcus’s review rule in the exec narrative.  
**Marcus:** Two coordinator design partners; weekly miss review; sample “good draft” language; confirm claims stay first.  
**Priya:** Read-only FreightPro next week; Graph creds for `claims@`; route Anthropic DPA through Legal; 20% engineer for mailbox/secrets.

---

## Honest roadmap

**Claims triage (HITL) → tracking drafts (HITL) → quote assist (HITL) → maybe limited auto-send on high-confidence tracking only after metrics.**  
Claims and rate confirmations stay human-gated.
