# Assignment: Meridian Freight ops inbox

## Context

Meridian Freight is a mid-size freight brokerage. They connect companies that need to ship
goods (shippers) with trucking companies (carriers). Their operations team works out of four
shared email inboxes: `ops@`, `quotes@`, `claims@` and `dispatch@`. Shippers and carriers
email them all day: "what would you charge for this load", "here is a load, please find a
truck", "where is my truck", "the freight arrived damaged", "here is our invoice".

Their system of record is a TMS (transport management system) called **FreightPro**. It holds
loads, carriers, shippers and rates. Truck availability lives in a separate Google Sheet.

You are the engineer on this engagement. Below is what you would have on day one: a kickoff
email from the COO, notes from two calls, and a snapshot of their data. Some of what the
three people say does not agree. That is normal, and part of the assignment.

## What the client told us

### Kickoff email from Dana Whitfield, COO

> Our ops team handles about 1,200 inbound emails a day across four shared inboxes. Carriers
> and shippers send us everything: quote requests, load tenders, delivery confirmations,
> damage claims, invoices, and a lot of "where is my truck".
>
> I want an AI agent that handles this end to end: reads the email, works out what it is,
> pulls what it needs from FreightPro, and replies. Ops says 90% of these are routine. I want
> 90%+ automated within 6 weeks, so we can move 8 of our 14 coordinators to sales.
>
> The data is clean, it is all in FreightPro. Legal is fine as long as nobody trains on our
> data. Budget is not the constraint, headcount is. Marcus and Priya can fill in details.

### Call with Marcus Oyelaran, Ops Lead

- On Dana's "90% routine" claim: "The categories are routine. Every email has some wrinkle." In
  other words, the *types* of email repeat, but each individual email has something unusual in it.
- The biggest pain is not email volume, it is **damage claims**. For each claim, a coordinator
  spends about 20 minutes comparing the proof-of-delivery (POD) document against the load record
  in FreightPro. Claims are only about 4% of emails but take about 35% of coordinator time.
- He will **not** let an AI-written reply go to a shipper unless a human has read it first.
  "One wrong rate confirmation costs more than a week of coordinator time." He has not told Dana
  this yet.
- Real volume is about 800 emails a day on a normal day, and 1,500+ during weather events
  (storms, road closures). Weather events are also when mistakes are most expensive.
- Even a "routine" quote needs three pieces of information: lane history and the fuel surcharge
  (both in FreightPro), plus current carrier availability. Carrier availability is not in
  FreightPro. It lives in a Google Sheet that the dispatch team updates only when they remember
  to, so it is often out of date.
- His worry about automation: "If the AI drafts something and I just click send, I stop reading.
  That is how we get burned."
- He wants his best coordinators to stay on the ops team.

### Call with Priya Natarajan, IT Manager

- FreightPro runs on SQL Server 2012 and has no API. There is a read replica you can query. A
  nightly ETL job copies data into a reporting database, but that copy is about 14 hours behind.
  The vendor charges $40k for any schema change.
- Email is Microsoft 365, and the Graph API is available. Attachments are mostly PDFs (some are
  scanned images), plus some `.xls` files.
- The data is **not** clean. Carrier names are free text, so the same carrier shows up as
  `JB Hunt`, `J.B. Hunt`, `JBHT`. Only about 70% of emails mention a load number. The rest quote
  a purchase order (PO) number, a bill of lading (BOL) number, or nothing at all.
- Legal's actual memo says: no personal data (PII) may go to a third party without a data
  processing agreement (DPA). Driver names and phone numbers count as PII, and most dispatch
  emails contain them. No vendor DPA has been reviewed yet.
- Read-only access to FreightPro can be granted next week. Write access has to go through the
  change board and will take 6–8 weeks.
- IT can give this project one engineer at 20% of their time.

## Problem statement

Two deliverables. Both matter; the second is where your engineering shows.

1. **`DESIGN.md` — at most 3 pages, written to Dana, Marcus and Priya.** What you would build
   in the first 6 weeks, what you would not build, and why. How you would measure whether it
   worked. What you need from each of them. Where their goals conflict, say so and propose a
   way through.
2. **One working slice.** Pick one narrow path through the problem and make it work end to end
   on the sample data: an email comes in, your system reads it, looks things up in the
   FreightPro snapshot, and produces a result a coordinator can act on. Which slice you pick,
   and why, is part of what we evaluate. Depth beats breadth.
3. Where the data or this brief is ambiguous, make a reasonable assumption and write it down.

## What we provide

Everything is in the `assignment/` folder. No access to any system is needed.

- `data/emails/*.eml` — 60 inbound emails to the four inboxes
- `data/attachments/` — the PDF and `.xls` files those emails reference
- `data/freightpro/` — `loads.csv`, `carriers.csv`, `shippers.csv`, `rates.csv`
- `data/carrier_availability.csv` — a dump of the dispatch Google Sheet
- `data.md` — what every file and column means, plus a glossary of freight terms

The snapshot is dated **2026-08-26**. It is synthetic, but treat it exactly as you would treat
real customer data when deciding what may leave the building. Treat the exports as you would
treat real exports.

## What to submit

Within 2 calendar days of receiving this, send us:

1. **A private GitHub repository.** Invite `dipen-ik` and `madhav-ik` as collaborators and send
   the link to the recruiter who sent you this packet. The README must take us from a cold
   clone to a working demo in at most three commands, and show how to run your slice on an
   email from the sample.
2. **`DESIGN.md`** as described above (at most 3 pages, addressed to the client).
3. **`NOTES.md`** (one page at most), addressed to us, not the client:
   - how you spent your time (e.g., understanding the assignment, research, …);
   - how you used AI tools — what they got wrong, what you overrode, and why;
   - what you would do next.
4. **A 5–10 minute video** (Loom or similar) walking through your design and running the slice
   on one email end to end.

Model calls must go through the **Anthropic API**, reading the key from the
`ANTHROPIC_API_KEY` environment variable. Do not commit a key. If the variable is missing,
fail with a clear message. Use your own key while building; we will set ours at review and run
what you shipped.

## Ground rules

- **The right candidate won't take more than 8 hours.** If you hit that, stop, write down what
  you would do next in `NOTES.md`, and submit. That is fine. Padding the submission is not.
- AI coding tools (Claude Code, Codex, Cursor, …) are expected. In the review you will explain
  every part of the system and change it live, so build something you understand.
- Python is preferred (it is our stack); another language is fine if you tell us why.
- We will not add constraints beyond this packet. Put your assumptions in `DESIGN.md`.
- Logistics questions (deadline, access, how to submit) go to the recruiter.

## Evaluation criteria

- Problem understanding
- Scope judgment
- Engineering quality
- Communication
- Use of AI tools

## Optional stretch

Only if you have time left inside the 8 hours, pick at most one: an evaluation harness that
runs your slice over all 60 emails and reports how it did · redaction of PII before any model
call, with a test proving it · cost and latency per email, measured · a minimal review UI for
coordinators.

## What happens next

Two of us review your submission independently. If you advance, there is a 60-minute
conversation: you walk us through the design and the code, we run your slice on a few emails
(some from the sample, some new), we ask where your tools led you astray, and we change a
constraint or two to see how you reason.
