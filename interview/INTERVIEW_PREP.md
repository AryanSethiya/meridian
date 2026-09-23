# Interview prep — 60-minute talk track

~Use this as a **spoken script**, not a document to read aloud line-by-line.  
Reviewers will: walk design + code → run emails → probe AI use → change a constraint live.

**Repo they have:** [https://github.com/AryanSethiya/meridian-freight-claims-triage](https://github.com/AryanSethiya/meridian-freight-claims-triage)  
**Do not push local edits** during prep unless you intentionally want them on the submitted repo.

---

## 0. Opening (60–90 seconds)

> Meridian has four shared ops inboxes and ~800 emails/day. Dana wants 90%+ automation so headcount can move to sales. Marcus’s real pain is **damage claims** — ~4% of volume, ~35% of coordinator time — and he will **not** let an AI reply reach a shipper unread. Priya has no FreightPro API, a ~14h-stale ETL, dirty carrier names, Legal DPA blockers for PII, and write access 6–8 weeks out.
>
> I scoped a **claims intake + POD↔FreightPro triage assistant**: email in → resolve load → compare POD → produce a **human-review action packet**. Deterministic first, LLM second for classify/draft only, **never auto-send**, never write FreightPro.

Then offer: “Want design first, or should I run `035` end-to-end?”

---

## 1. Problem statement (if they ask you to restate it)

Cover **all three stakeholders** in one breath:


| Person           | Says                                                                                      | Tension                                  |
| ---------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------- |
| **Dana (COO)**   | 90% automation, move 8/14 coordinators to sales, “data is clean”                          | Over-indexes volume; underweights risk   |
| **Marcus (Ops)** | Claims burn 20 min each; every email has a wrinkle; **HITL required**                     | Will not accept unread auto-send         |
| **Priya (IT)**   | No API, SQL 2012, 14h lag, dirty names, Graph OK, DPA before PII, writes 6–8 wks, 20% eng | Constraints kill “full agent in 6 weeks” |


**Your framing sentence:**  
“The evaluation is not ‘build Dana’s agent.’ It’s ‘pick the highest-ROI path that respects Marcus’s risk rule and Priya’s constraints.’”

---

## 2. Why this slice (scope judgment — they grade this hard)

**Why claims, not quotes/tracking/all four inboxes:**

1. Highest time ROI (Marcus’s numbers).
2. Naturally HITL-compatible (Marcus already refuses unread send).
3. Sample data has the hard wrinkles (blank POD, clean POD vs damage allegation, PO/BOL resolve, forwards).
4. Depth > breadth under ~8h.

**What I explicitly did *not* build:** 4-inbox agent, auto-send, FreightPro writes, training on Meridian data, “fixing” the availability sheet, inventing per-field `as_of`.

**Stretch honesty (from NOTES):** brief said pick **at most one** stretch; I added thin wrappers for eval / PII tests / cost / review UI that all reuse the same pipeline — call that out if asked; demo **one** path.

---

## 3. Approach in 5 layers

```text
1. Parse .eml + attachments
2. Extract MF / PO / BOL / PRO → FreightPro CSV lookup + shipper corroboration
3. POD inventory → text / blank / vision-blocked
4. Deterministic discrepancies + forward/multi-intent gates + PII redact
5. Optional Anthropic classify+draft → ActionPacket (JSON + MD) → human only
```

**Mantra to repeat:**  
“Deterministic owns truth. LLM owns interpretation and draft language. Human owns send.”

---

## 4. Stakeholder conflicts — how you handled them

Memorize this table (it’s in `DESIGN.md`):


| Conflict                    | Your answer                                                                                        |
| --------------------------- | -------------------------------------------------------------------------------------------------- |
| Dana 90% vs Marcus HITL     | HITL drafts for full 6 weeks on claims; any later auto-send is metric-gated and **not** for claims |
| “Data clean” vs dirty/stale | MF→PO→BOL→PRO; MC/SCAC; sender↔shipper domain; snapshot freshness labeled                          |
| Headcount vs 20% IT         | No FreightPro rebuild; read-only path; coordinators stay review layer                              |
| Legal DPA                   | Redact + fail-closed + vision off; **mitigation ≠ DPA**                                            |


---

## 5. Metrics you would defend


| Metric                  | Target              | Why                     |
| ----------------------- | ------------------- | ----------------------- |
| Minutes/claim           | toward <5 from ~20  | ROI                     |
| Correct email→load      | ≥95% on labeled set | Wrong load is expensive |
| Draft ≤1 edit accept    | ≥70%                | Assist quality          |
| Wrong-load / false-send | ≈0                  | Marcus                  |
| Review completion       | tracked             | Anti rubber-stamp       |


**Important honesty:** repo `8/8` is a **known-sample regression**, not production accuracy. Say that before they do.

**Automation gate:** only `resolution.status == "resolved"` is authoritative (`Resolution.is_authoritative_for_action()`). Ambiguous best-guess is coordinator context only.

---

## 6. Demo script (practice cold)

```bash
source .venv/bin/activate
python -m meridian_claims process data/emails/035.eml --dry-run   # no key needed
# or live: needs ANTHROPIC_API_KEY
python -m meridian_claims process data/emails/035.eml
open output/035.md   # or cat
```

**Emails to have ready:**


| Email | Punchline                                                                                    |
| ----- | -------------------------------------------------------------------------------------------- |
| `035` | Damage claim; FreightPro/POD look clean; discrepancy = allegation vs clean POD; JBHT→JB Hunt |
| `036` | Shortage; blank POD → `blank_page`, escalate                                                 |
| `041` | No MF; resolve via PO → `MF-10032`                                                           |
| `042` | Resolve via BOL                                                                              |
| `058` | Forward + multi-MF → model fail-closed                                                       |


Optional UI: `python -m meridian_claims review` → [http://127.0.0.1:8765/](http://127.0.0.1:8765/)  
(If port busy: `--port 8766` or kill leftover process.)

---

## 7. AI tools — what to say when they ask

Structure: **used → wrong turn → override → why.**

Examples from `NOTES.md` (own these):

- AI pushed **4-inbox / auto-send** → you forced claims + HITL  
- AI wanted to **“OCR succeed”** blank POD scans → you mark blank/unreadable, skip vision  
- AI trusted free-text carrier names → you prefer MF/PO/BOL/PRO + MC/SCAC  
- AI treated redaction as Legal done → you insist **DPA still required**  
- AI skipped ID cross-checks → conflicting MF/PO → `ambiguous`, not silent resolve

Close with: “I can change any module live without depending on chat history.”

---

## 8. Weaknesses — volunteer them (builds trust)

1. FreightPro is a CSV snapshot, not a live replica/query layer.
2. Stretch breadth vs “pick one” — owned in NOTES.
3. Carrier free-text matching is still fragile.
4. Unlabeled personal names outside FreightPro lexicon can slip past redaction.
5. No Graph poller / production queue yet (honest next step).

---

## 9. Closing ask / next steps (if they ask “what next?”)

Order from DESIGN:  
**Claims HITL (done) → Graph on claims@ → wrong-load logging with Marcus → tracking drafts HITL → maybe quote assist → limited auto-send only after metrics, never unsupervised claims.**

---

## 10. Mental checklist the night before

- Restate problem in <90s with Dana/Marcus/Priya tension  
- Explain why claims slice without apologizing  
- Trace `035` through pipeline verbally  
- Name where LLM is called (`agent.classify_and_draft`) and what it must *not* invent  
- Name one file for: resolve, POD, PII, evidence, pipeline  
- Run dry-run `035` successfully once  
- Know how to free port 8765  
- Have `DESIGN.md` + `NOTES.md` open for quick quotes

Companion docs: `[INTERVIEW_CODE_MAP.md](INTERVIEW_CODE_MAP.md)` · `[INTERVIEW_LIVE_DRILLS.md](INTERVIEW_LIVE_DRILLS.md)` · `[INTERVIEW_QA.md](INTERVIEW_QA.md)`