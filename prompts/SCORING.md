# Job triage: judgment only (brief + judgments.jsonl)

Read, in order: the profile's `candidate.md` (who you score for), its
`scoring-notes.md` (this person's role-shape bands, differentiators, false positives
and cap examples), then the brief files `runs/<D>/brief-*.md`, one at a time. Never
read `scored.json`: the brief already holds everything you need from it.

`scoring-notes.md` specialises this method. Where they disagree, `scoring-notes.md`
wins for *what* scores high; this file wins for *how* to score and the output format.

**Already done in code, so treat it as settled:** 48-hour freshness, current-employer
block, seen-jobs ledger, applied-companies filter, cross-source dedupe,
location/contract/title/out-of-domain drops, LinkedIn match-score floor,
keyword-group counting, the 35-point STACK score, experience band, company tier,
apply-link liveness, and JD requirement and gap analysis (`covered`, `gaps`,
`hard-req gaps` in the brief).

**`jptools.py render` also does:** the sum, the caps, sorting, the table, the JSON and
the coverage block. It checks that every differentiator quote is verbatim from the
JD, has fewer than 15 words, and contains a `covered` term. If not, it zeroes the
points and warns.

**Your job is the 65 points that need reading comprehension**, plus the deep dive.

---

## 1. ROLE SHAPE: 40 points (`rs`)

Score the responsibilities, not the title. Score the **ratio** of the work the person
wants (the "up" verbs in `scoring-notes.md`) to the work they don't (the "down" verbs),
not whether either kind appears. Use the band table in `scoring-notes.md`. A title that
matches but whose responsibilities are mostly "down" work scores as "down" work.

`POSSIBLE-…` flags from the code are hints for you to adjudicate, not verdicts. For a
clearly out-of-domain role, write `"drop": "<reason>"` instead of a score.

## 2. DIFFERENTIATOR PULL: 25 points (`diff`)

Does the JD explicitly ask for something in `candidate.md` (Artifacts or Stack) that
most reqs don't? **Quote the phrase** (under 15 words, copied exactly). No quote, no
points. The quote must contain a term from the brief's `covered` line. Check the false
positives listed in `scoring-notes.md`.

## 3. Caps (`cap`)

`final = stack (from the brief) + rs + diff`, then render applies your cap:
- **70**: a hard requirement the person can't meet. `hard-req gaps` lists candidates
  with the triggering sentence. Decide whether it genuinely blocks or is a
  nice-to-have the regex over-read; if it doesn't block, say so in `why`.
  `SHALLOW-SKILL-DEMAND: <x>` points at a known soft spot (see `skills.json` →
  `shallow`). A JD that demands depth there caps at 70.
- **65**: the role is mostly the "down" work in `scoring-notes.md` (on-call, ticket
  queues, support desk…).
- **60**: `REQ-SHAPE-SPAM-SIGNAL` and the JD confirms it (recruiter-posted, "client
  requirement", "immediate joiners", bench language, no named hiring team).

≥75 is kept. 65-74 is NEAR MISS and **needs `why`**: one line on what held it back.
Never pad.

## 4. Output

**a. `runs/<D>/judgments.jsonl`.** One line per job, appended after each brief file:

```
{"id":"4483104370","rs":36,"diff":22,"quote":"<exact JD phrase>","cap":null,"why":""}
{"id":"4480674531","rs":22,"diff":10,"quote":"<exact JD phrase>","cap":70,"why":"<hard req> is mandatory"}
{"id":"4475086703","rs":0,"diff":0,"quote":"","cap":null,"why":"","drop":"<out-of-domain reason>"}
```
Keys: `id`, `rs` 0-40, `diff` 0-25, `quote`, `cap` (null/60/65/70), `why` (≤20
words; required for NEAR MISS, any cap, or a hard-req gap you judged non-blocking),
and optional `drop`. Write nothing else per job.

**b. `runs/<D>/deepdive.md`.** For the top 5 that render prints. See AGENTS.md
Step 6 for the format. Cite real artifacts listed in `candidate.md`. Take gaps from
the brief's `gaps`, ranked by how central they are to the JD.

**c. Chat.** Render's markdown table pasted verbatim, plus one sentence naming the
bottleneck if fewer than 5 cleared 75, and whether the 48-hour window was the binding
constraint.

## Hard rules

- Never invent a job, company, salary or URL.
- Never restate or adjust `stack`.
- `APPLY-LINK-UNVERIFIED`, `APPLIED-TITLE-REVIEW`, `UNDER_BANDED` and
  `POSTED_DATE_UNPARSED` jobs are judged like any other, never dropped for the flag.
- Don't drop a job because the company is an IT services firm. Filter on req shape,
  not company class.
- `SEE job-search-notes.md` in render's output means the user has a note on that
  company (outreach isn't an application). Mention it next to the row.
