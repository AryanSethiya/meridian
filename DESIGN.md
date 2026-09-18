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
| Dana: 90%+ automation / move 8 of 14 to sales vs Marcus: no unread AI replies | **HITL drafts for the full 6 weeks on claims.** Measure minutes saved and wrong-load rate. Any later auto-send is limited, metric-gated, and **not** for claims. |
| Dana: “data is clean” vs Priya: dirty names, missing IDs, 14h ETL lag | MF → PO → BOL → PRO; MC/SCAC for carriers; sender↔shipper domain corroboration; snapshot freshness on FreightPro facts. |
| Headcount pressure vs 20% IT + keep best coordinators on ops | No FreightPro rebuild. Read-only data path. Coordinators remain the review layer. |
| Legal: no PII to vendors without DPA | Redact DriverName/DriverPhone before text model calls; block POD image uploads by default; withhold raw From/To from the model payload. **Mitigation ≠ DPA.** |

---

## Architecture (working slice — what this repo does)

```text
.eml → parse → IDs → FreightPro lookup → shipper corroboration
     → attachment inventory → POD text/quality/blank
     → forward + multi-intent checks → PII gate
     → Anthropic (classify + draft) or fail-closed / dry-run fallback
     → action packet (analysis + decision) → human review (no send)
```

**Deterministic first:** load resolution, sender/shipper corroboration, POD quality, attachment selection, discrepancies, unknowns, draft money/liability sanitize, forward fail-closed, multi-intent escalate.  
**LLM second:** interpretation + draft only; must not invent null facts; invented IDs scrubbed. Model sees `sender_corroboration`, not raw mailbox addresses.  
**Never auto-sends. Never writes FreightPro. Never executes secondary tracking/invoice/quote asks.**

Packet `analysis` separates: FreightPro facts (with source/snapshot freshness) · email facts · POD facts · discrepancies · unknowns · AI suggestion · draft · why HITL. Attachments are inventoried (selected vs present-not-processed).

---

## What we build in 6 weeks / what we do not

**In scope**

| Weeks | Deliverable |
|---|---|
| 1–2 | Claims slice (**this repo**): `.eml` → packet, HITL draft, read-only FreightPro, safety gates above. Stretch demos in-repo: all-60 behavioral eval, estimated cost/latency, local review UI (no send) — not production readiness. |
| 3–4 | Graph watch on `claims@`; thin accept/edit/reject queue; wrong-load logging with Marcus |
| 5–6 | Optional HITL **tracking** status drafts; optional HITL **quote assist** — still no unsupervised send |

**Explicitly not built in 6 weeks:** full 4-inbox agent; auto-send; FreightPro writes/claim-file creation; training on Meridian data; “fixing” the availability Google Sheet; FreightPro schema changes; per-field `as_of` timestamps (export does not provide them).

---

## Human-in-the-loop, PII, read-only, stale data

- **HITL:** `needs_human` is required for claims. Drafts are suggestions. Coordinators send (or not) from their mail client / a future review queue — not this CLI.  
- **PII:** DriverName/DriverPhone are Legal PII. Text payloads are redacted and scanned before Anthropic; residual known PII fails closed. Forwarded mail fail-closes the model call when third-party PII cannot be guaranteed. POD **vision is off by default**. Production Anthropic use still needs a **Legal-approved DPA**.  
- **Read-only:** FreightPro is replica/export only until change board allows writes (6–8 weeks).  
- **Stale data:** Status/PODReceived are **recorded as of the snapshot** (2026-08-26; ~14h lag called out). Disputed conditions escalate rather than “trust the flag.”

---

## Assumptions

- Sample CSVs stand in for the read replica.  
- Claims slice ignores rates.csv and carrier_availability (quote/dispatch concerns).  
- Blank/placeholder or low-quality POD text is escalated, not treated as delivery truth.  
- Anthropic only for model calls; missing key fails clearly.

---

## Success metrics (week 6)

| Metric | Target | Why |
|---|---|---|
| Minutes per claim (email → decision-ready packet) | Toward **under 5** from ~20 | ROI |
| Correct email-to-load association on a labeled validation set | **≥95%** | Wrong load is expensive |
| Draft accepted with ≤1 edit | **≥70%** | Assist quality |
| Wrong-load / false-send | **≈0** | Marcus |
| Review completion (opened/edited, not rubber-stamp) | Tracked | Safety |

Association is evaluated against labeled historical claims. The current 8/8 fixture result is a known-sample regression check, not a production accuracy estimate.

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
