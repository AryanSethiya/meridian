# Interview live drills — constraint changes

They will change a rule mid-interview. Practice **talking then editing**.  
Pattern: **restate → locate → tradeoff → edit → prove with one email**.

**Do not push** practice edits to the submitted remote unless they ask you to share a branch.

---

## Universal response template (10 seconds)

1. “Here’s how the system behaves today…”  
2. “Here’s the file that owns that decision…”  
3. “Tradeoff if we flip it…”  
4. “I’ll change X and re-run `035` / `036` / `058` to show it.”

---

## Drill 1 — “Allow auto-send when confidence &gt; 0.9”

**Today:** `needs_human=True` always on claims; draft `auto_send: false`.

**What they want to hear:** you resist blindly. Claims stay gated.

**Good answer:**  
“I’d refuse unsupervised claims send. If forced to sketch a gate: only non-claims tracking, `resolution.is_authoritative_for_action()`, no discrepancies, shipper corroboration matched, model confidence threshold, and Marcus accept rate. Wrong-load ≈ 0 first.”

**If they insist on a code sketch:**  
- `pipeline.py` — where `needs_human = True` is forced  
- `analysis.py` / draft block — `auto_send`  
- Gate with `resolution.is_authoritative_for_action()` in `models.py`

**Prove:** dry-run `035` still escalates damage-vs-clean-POD (should not auto-send even at high confidence).

---

## Drill 2 — “Skip human review for shortage claims”

**Locate:** `pipeline.py` (reasons list / `needs_human`), maybe claim_type from classification.

**Tradeoff:** Marcus’s rule; blank POD shortfalls (`036`) are exactly where AI is dangerous.

**Edit sketch:** only set `needs_human=False` if claim_type==shortage **and** POD mode==text **and** resolved **and** no discrepancies — then **argue you still wouldn’t ship it**.

**Prove:** `036` must still escalate (blank POD).

---

## Drill 3 — “Trust FreightPro Status as live tracking”

**Today:** Status is recorded-as-of snapshot (~14h lag); labeled in facts.

**Locate:** `freightpro.py` (`freightpro_source_meta`), `analysis.py` freightpro facts, `agent.py` SYSTEM_PROMPT, `DESIGN.md` assumptions.

**Edit sketch:** change copy from “recorded/snapshot” → “live” **only if** they force it — then say this is a **lie relative to the ETL**. Better: escalate when Status conflicts with shipper allegation / POD.

**Prove:** show packet freshness fields on `035`.

---

## Drill 4 — “Enable POD vision by default”

**Today:** vision off unless `MERIDIAN_ALLOW_POD_VISION=1`; pixels not redacted.

**Locate:** `pod.py` (`vision_allowed` / `vision_blocked`), `pii_gate.py`.

**Good answer:** “Legal: pixels can contain driver/receiver PII; text redaction doesn’t help. I’d keep default off until DPA + pixel policy.”

**If they flip env:** set `MERIDIAN_ALLOW_POD_VISION=1` and show a blank/unreadable case still fails closed or escalates.

---

## Drill 5 — “Resolve using carrier name if no load number”

**Today:** MF→PO→BOL→PRO; free-text carrier does not overturn LoadNumber.

**Locate:** `resolve_load.py`, carrier mention handling in pipeline/analysis.

**Tradeoff:** dirty names (`JB Hunt` / `JBHT`); wrong-load risk.

**Edit sketch:** fuzzy SCAC/MC only as **candidate list** with `status=ambiguous`, never silent `resolved`.

**Prove:** invent a thought experiment — two JB Hunt loads → must be ambiguous.

---

## Drill 6 — “Always call the LLM, even on forwards”

**Today:** forward + unsafe third-party PII → fail-closed, no Anthropic.

**Locate:** `forward_mail.py`, `pipeline.py` block around `forward_info.detected`.

**Tradeoff:** Legal DPA / third-party PII in forwarded threads.

**Edit sketch:** remove fail-closed → then show you’d need stronger redaction or Lexicon expansion; prefer keeping fail-closed.

**Prove:** `058` — today should show `llm_status` / pii blocked path.

---

## Drill 7 — “Change resolve order to PO first”

**Locate:** `resolve_load.py` — preference order in `resolve_load`.

**Tradeoff:** PO can collide across shippers more than MF; MF is Meridian’s primary key.

**Edit:** swap order PO → LoadNumber → BOL → PRO.

**Prove:** re-run `041` (PO path) and an MF email (`035`) — explain which got riskier.

---

## Drill 8 — “Add a new identifier type (e.g. PRO only emails)”

**Locate:** `extract_identifiers` regexes in `resolve_load.py`; `FreightProDB` indexes in `freightpro.py`; eval fixtures if labeled.

**Steps:** regex → index lookup → cross-check → packet evidence.

**Prove:** pick a sample email that already has PRO if present in corpus, or explain with a fake body string in a unit test.

---

## Drill 9 — “Don’t redact driver phone — Legal said OK”

**Today:** DriverName/Phone are Legal PII; redact + outbound scan.

**Locate:** `redact.py`, `pii_gate.py`, tests in `tests/test_pii_*.py`.

**Good answer:** “Assignment Legal memo says no PII to third party without DPA. If Legal **and** DPA signed, we can relax — until then fail-closed stays.”

**Prove:** run / cite `tests/test_pii_model_boundary.py` — model payload must not contain DriverPhone.

---

## Drill 10 — “Build quotes next instead of Graph on claims@”

**Today:** roadmap = Graph claims@ → tracking HITL → quote assist later.

**Answer as product sense:** quotes need rates + **stale availability sheet** — higher wrong-answer cost. Claims already has Marcus buy-in for HITL. Prefer Graph ingress on claims before quotes.

No code required — this is DESIGN judgment.

---

## Drill 11 — “Make dry-run the default in the review UI”

**Locate:** `review_server.py` (`dry_run = bool(body.get("dry_run", False))`), `review_ui/app.js` checkbox, `_api_config` `default_mode`.

**Edit:** flip default `dry_run=True` or checkbox default checked.

**Prove:** Process without key succeeds; live still works when unchecked + key present.

---

## Drill 12 — “Wrong load returned — how do you debug live?”

Talk track (no panic):

1. Open `output/{id}.json` → `resolution`, `identifiers`, `conflicts`  
2. Check raw_hits for multi-MF  
3. Check shipper corroboration conflict  
4. Re-run with `--dry-run` to remove LLM noise  
5. Check FreightPro CSV row for that MF/PO/BOL  

Files: `resolve_load.py`, packet JSON, maybe `eval.py` expected map.

---

## Practice loop (45 minutes)

Do each drill once out loud:

| # | Drill | Email to re-run |
|---|---|---|
| 1 | Auto-send refuse | `035` |
| 2 | Shortage skip HITL | `036` |
| 4 | Vision default | env + `036` |
| 6 | Forward always LLM | `058` |
| 7 | PO-first resolve | `041` + `035` |
| 12 | Debug wrong load | any |

After each: **revert** local edits so your submitted understanding stays clean.

---

## Commands cheat sheet

```bash
source .venv/bin/activate
python -m meridian_claims process data/emails/035.eml --dry-run
python -m meridian_claims process data/emails/036.eml --dry-run
python -m meridian_claims process data/emails/041.eml --dry-run
python -m meridian_claims process data/emails/058.eml --dry-run
pytest -q
python -m meridian_claims eval --dry-run
# review UI
python -m meridian_claims review --port 8765
# if busy:
lsof -ti :8765 | xargs kill
```

---

## Soft skills during live edit

- Narrate before typing.  
- Prefer smallest change.  
- If unsure of a line, `rg` / Jump to symbol — don’t scroll forever silently.  
- If the change is unsafe, say so **and** still show where you’d wire it.  
- Offer a test: “I’d add a pytest that locks this behavior.”

Companion: [`INTERVIEW_PREP.md`](INTERVIEW_PREP.md) · [`INTERVIEW_CODE_MAP.md`](INTERVIEW_CODE_MAP.md)
