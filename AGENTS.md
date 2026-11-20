# Agent instructions: JobSearch (jobpipe + CV tailoring)

You help the user find high-probability jobs with **jobpipe** and tailor their Master
CV when asked. This file is shared by Codex CLI (reads `AGENTS.md`) and Claude Code
(reads `CLAUDE.md`, which imports this file). Start the agent from the repo root.

## Profiles

All personal data lives in the **active profile**: `profiles/<name>/` (git-ignored),
selected by `JOBSEARCH_PROFILE` in `.env`. Below, `P` means that folder.

```
P/profile.json      searches, filters, keyword groups, tunables (input to build-inputs)
P/candidate.md      who you score for          P/skills.json     skills + vocabulary
P/scoring-notes.md  this person's scoring rules  P/tailor-notes.md this person's CV rules
P/master-cv.tex     Master CV (source of truth)  P/job-search-notes.md  outreach notes
P/actor-inputs.json generated scraper inputs     P/state/          seen-jobs, applied, Overleaf
P/runs/<date>/      scrape + judging workfiles   P/results/  P/output/ (tailored CVs)
```
Find P with: `python3 -c "import jsprofile as j;print(j.profile_dir())"`.
Every `python3 jobpipe.py|jptools.py|apify_fetch.py` command uses the active profile.

## Commands

Nothing runs unless the user types the command. In Claude Code these are slash
commands; in Codex the user types the same words and you follow this file.

| Command | Does |
|---|---|
| `/setup_profile <resume file>` | draft a new profile from a resume, then STOP for review |
| `/doctor` | `python3 jptools.py doctor --online` and explain any FAIL |
| `/find_jobs [72h\|7d]` | Steps 0-6 below, then STOP. Never touches a `.tex` |
| `/tailor <Company> [--publish]` | tailor the Master CV to one job (pasted JD wins) |
| `/publish [file.tex\|--all]` | upload tailored CVs to private Overleaf projects |
| `/mark_applied [Company]` | record the application in `P/state/applied-companies.json` |
| `/check_cv [file]` | `python3 jptools.py check-tex [file]` (default: Master CV) |

Use one chat for `/find_jobs` and a fresh chat per shortlisted role for `/tailor` →
`/publish` → `/mark_applied`. In that chat, keep the confirmed company, exact title,
jobId (if known) and exact output filename, so bare `/publish` and `/mark_applied`
need no arguments. Never pick a file by modification time; if unclear, ask.

---

## Accounts and secrets: hard rules

1. **Apify:** only through `apify_fetch.py`, which reads `APIFY_TOKEN` from `.env` or
   the environment. **Never** use an Apify MCP server or any `mcp__Apify__*` tool, not
   even as a fallback: it may be logged in to someone else's account. No network →
   stop and tell the user.
2. Never print, echo, log or copy `APIFY_TOKEN`, and never read `.env` with a file
   tool. Check variables with code that prints only "set"/"unset".
3. **Overleaf only through `/publish`**, only through the `olcli` MCP server, and only
   after the account guard passes. Never compile: no `compile`, `download_pdf`,
   `compile_with_outputs`, pdflatex, latexmk or any renderer. Never enable sharing,
   delete projects, handle cookies/passwords, or set `OLCLI_MCP_UNCONFIRMED_WRITES`.
4. Never `git add`, commit or push anything under `profiles/`, `.env`, or state files.
   Before any push of this repo, run `python3 jptools.py scan-public`.

## Token budget: hard rules

1. **Never open bulky files** (Read, `cat`, `head`, `jq .`, `sed`): `P/runs/**/*.json`,
   `P/state/*.json`, `P/results/*`, any `*.py`, and tailored CVs other than the one
   being written. `jptools.py` prints what you need.
2. **Don't explore.** No `ls -R`, `find`, `tree` or repo-wide search. Files you need
   are named in each step.
3. **Read each needed file once per session.**
4. **Use code for mechanical work.** In /find_jobs you write only `judgments.jsonl`
   and `deepdive.md`; `jptools.py render` writes the results.
5. **Keep tool output small.** One field from JSON → a `python3 -c` one-liner.
6. **Keep chat short.** Paste render's table and the bottleneck; don't restate JDs.

## The one rule that matters

**jobpipe does the mechanical work in code. Don't redo it in prose.** Freshness,
location, experience band, dedupe, seen/applied filtering, keyword matching and the
35-point STACK score come from `jobpipe.py`. You add only the 65 judgment points in
`prompts/SCORING.md`, plus the deep dive.

Never: filter or score from raw scraper JSON; drop a job because a field is missing;
invent a job, company, salary or URL ("not listed" instead); adjust `stack_score`.
Already handled in code: missing posted date (kept, `POSTED_DATE_UNPARSED`); "10+
years" (only a stated ceiling ≤ `drop_ceiling_at_or_below` drops; the next ceilings
are `UNDER_BANDED`); 48h freshness; "Greater Bengaluru"/"India"/"Remote" (allowed
per profile); no Naukri description (pass 2 fetches primaries only); "skill fit <
75%" (75 is the final bar, not a pre-filter).

---

## /setup_profile <resume file>

1. Ask for a short profile name if none is obvious; run
   `python3 jptools.py new-profile <name>` (exit 3 = exists: ask before overwriting).
   Tell the user to set `JOBSEARCH_PROFILE=<name>` in `.env` (don't edit `.env`
   yourself).
2. Read the resume once (PDF/DOCX/TeX). If it's a `.tex` that already follows
   `templates/master-cv-template.tex`, copy it to `P/master-cv.tex`. Otherwise
   **transcribe** it into `P/master-cv.tex` using that template's preamble, macros and
   CONTRACT. Transcription only: no new claims, metrics or tools; list anything
   unclear under SOURCE DISCREPANCIES.
3. Draft, from the resume only: `P/candidate.md` (Now, Artifacts, Stack, Known gaps,
   What they're looking for, Open questions), `P/skills.json` (`have` ⊆ `vocabulary`;
   `shallow` for soft spots), `P/scoring-notes.md` and `P/tailor-notes.md` (fill the
   templates), and `P/profile.json`:
   - `name` exactly as in the Master CV footer; `config.blocked_companies` = current
     employer's name variants; `candidate_years`; `experience` {min, max}.
   - `location`: LinkedIn location string, Indeed location, Naukri `naukri_city_gid`
     (97 = Bengaluru; for another city ask the user, see README).
   - `search`: 3-6 short `linkedin_keywords` / `naukri_keywords`, 1-4
     `indeed_keywords`, 4-8 `boolean_queries` ({id, query}), `linkedin_title_exclude`.
   - `keyword_groups`: about 12 groups of the person's core skills, `[term, strict]`
     (strict = case-sensitive, for short tokens like "Go", "QA").
   - `filters`: `title_drop` (titles never wanted), `out_of_domain`, `dev_hint` (a
     flag for the model, not a drop). Regexes, case-insensitive.
4. Run `python3 jptools.py build-inputs` and `python3 jptools.py doctor`. Fix FAILs.
5. **STOP.** Summarise in ≤ 15 lines what to review (Open questions, queries, blocked
   employer, keyword groups). Don't scrape.

---

## /find_jobs

Run from the repo root. Set `D=$(date +%F)` and `R=P/runs/$D` (inline real paths in
each shell call; variables may not persist between calls).

**Step 0, preflight**
```sh
python3 jobpipe.py self-test && python3 jptools.py self-test && python3 jptools.py doctor --online
```
A FAIL → stop and tell the user (say which Apify username is in use). Then read
`P/candidate.md`, `prompts/SCORING.md`, `P/scoring-notes.md` and
`P/job-search-notes.md`. Nothing else. `mkdir -p $R`.

**Step 1, scrape** (don't edit `P/actor-inputs.json`; change `P/profile.json` and
rerun `build-inputs` instead). Takes several minutes: run it in the background or
with a long timeout.
```sh
python3 apify_fetch.py scrape --outdir $R
```

**Step 2, stage A**
```sh
cp P/state/seen-jobs.json $R/seen-jobs.before.json
python3 jobpipe.py stage-a --linkedin $R/linkedin.json --naukri $R/naukri.json \
    --indeed $R/indeed.json --out $R/triage.json
```
Add `--no-network` only if link checks can't run, and say apply links weren't
verified. Re-running on the same data: `cp $R/seen-jobs.before.json
P/state/seen-jobs.json` first, otherwise every job is dropped as "already seen".

**Step 3, descriptions + stage B + brief**
```sh
python3 apify_fetch.py details --triage $R/triage.json --out $R/details.json
python3 jobpipe.py stage-b --triage $R/triage.json --details $R/details.json --out $R/scored.json
python3 jptools.py brief --scored $R/scored.json --outdir $R
```
*3b, only on `/find_jobs 72h|7d`:* write `{"freshness_hours": 72}` (or 168) to
`$R/window.json` and pass `--config $R/window.json` to stage-a and stage-b. Never
widen the window on your own.

**Step 4, judge** (follow `prompts/SCORING.md` + `P/scoring-notes.md`). Read the brief
files one at a time; after each, append one line per job to `$R/judgments.jsonl` with
a quoted heredoc (never `python3 -c`: JSON `null` breaks Python):
```sh
cat >> <R>/judgments.jsonl <<'EOF'
{"id":"123","rs":30,"diff":15,"quote":"<exact JD phrase>","cap":null,"why":""}
EOF
```
The last line for an id wins. Use `python3 jptools.py jd <job_id>` only when a
decision hinges on JD text the brief trimmed (`[…]`).

**Step 5, render**
```sh
python3 jptools.py render --scored $R/scored.json --judgments $R/judgments.jsonl --date $D
```
Writes `P/results/triage-$D.md` and `results-$D.json`. On `ERROR`/`WARN`, append
corrections and rerun.

**Step 6, deep dive, then STOP.** Render prints the top 5 (≥65). Write
`$R/deepdive.md`, one section per job, ≤ 8 lines, headed exactly
`## <job_id> · <Company> | <Title>`: fit in 3 lines citing real artifacts from
`candidate.md` plus one quoted JD phrase; the 3 highest-signal gaps from the brief;
**one draft bullet** for the biggest gap (a draft: `/tailor` checks it); likely
first-round topic. Rerun render to fold it in. In chat, paste render's table
**verbatim**, then coverage, the bottleneck, and one line per deep-dive job. Zero or
few results is fine: don't loosen filters. **STOP.**

---

## /tailor <Company> [--publish]

1. **JD:** pasted → use it. Otherwise `python3 jptools.py jd "<Company>"` (exit 3 =
   several matches: ask which job_id; exit 4 = not found: ask for the JD).
2. Read `prompts/TAILOR.md`, `P/tailor-notes.md` and `P/master-cv.tex`, once each.
3. Follow `prompts/TAILOR.md`. **Stop and wait** after Stage 0, after Stage 1, and
   before writing the file.
4. **Write:** `python3 jptools.py scaffold "<Company>"` → `P/output/<Name> <Year> -
   <Company>.tex` (exit 3 = exists: ask `--force` or `--suffix " v2"`). Replace
   `<<HEADLINE>>`, the `<<BODY>>` line and the contact block in one edit. Never touch
   `P/master-cv.tex`.
5. **Check:** `python3 jptools.py check-tex "<output filename>"`; fix and rerun.
6. **Report:** CHANGELOG, GAPS, traps, the output filename, "Not compiled."
   `--publish` → run /publish for that file and include the project link.
7. Ask whether to record the application (`/mark_applied`). Tailoring or publishing
   alone never marks a role as applied.

---

## /publish [file.tex | --all]  (Overleaf through the olcli MCP server)

Publishing = uploading source into **private** Overleaf projects. Not compiling,
not sharing.

**0. Account guard (always first, before any remote write)**
1. Find the olcli MCP tools (`list_projects`, `create_project`, `push_file`,
   `get_project_info`, `download_file`) and read their schemas. Never invent
   arguments. Not connected → point the user to README > Overleaf, then stop.
2. `python3 -c "import os,jsprofile;jsprofile.load_env();e=os.environ.get('OVERLEAF_EXPECTED_EMAIL','');print(e if '@' in e else 'UNSET')"`.
   `UNSET` → stop and ask the user to set it in `.env`.
3. Call `list_projects` (also validates the session). If any olcli tool reports the
   logged-in user, compare it case-insensitively with `OVERLEAF_EXPECTED_EMAIL`.
   Mismatch → **stop**: "olcli is logged in to a different Overleaf account". Never
   continue on a mismatch, even if asked in the same message.
4. If olcli exposes no account identity, show the expected email and up to 5 existing
   project names, and ask the user to reply "yes, this is my Overleaf". Once per
   chat; no reply, no write.
5. Explicit expired/invalid-session error (not timeouts or rate limits): run
   `olcli auth --browser` once, ask the user to restart the agent so the MCP server
   reloads, and stop. Never loop logins.
6. Respect olcli's write-confirmation dialogs. If unsupported, show the returned
   preview/CLI command for the user to run; never bypass them.

**1. Scope.** Bare `/publish` = the tailored CV established in this chat; a filename
= that file in `P/output/`; `--all` = every standalone `.tex` in `P/output/` (has
uncommented `\documentclass`, `\begin{document}`, `\end{document}`). Regular,
non-symlink files only. Missing → report, never substitute another version. Already
published with `local_deleted` → report "already published; local copy removed" with
its link. Skip files with local `\input`/`\include`/graphics/bibliography
dependencies.

**2. Check.** `python3 jptools.py check-tex "<file>"`; publish only files with no
errors; report warnings. Show a compact `filename → project name` plan.

**3. Project.** Project name = filename without `.tex`; remote file `main.tex` (root
document). Track in `P/state/overleaf-projects.json` (key = filename; instance,
project ID, `main.tex`, last verified SHA-256, `pending`/`verified`, `local_deleted`;
never credentials), read and written only by short `python3` scripts with atomic
writes. Unmapped file: list projects; no same-name match → create a blank project and
save its ID as `pending` **before** uploading; a same-name match → ask which ID to
adopt. After a create timeout, re-list before retrying. A mapped project that's
missing → error, never a silent replacement.

**4. Update safety.** For a mapped project, download its `main.tex` to a temp folder
(never over the local file) and compare hashes in code. Same → skip upload, mark
verified. Differs from the last verified hash → remote-edit conflict: stop and ask.

**5. Upload and verify.** `push_file` one file per project with the explicit project
ID and `main.tex`. Read it back, compare SHA-256 with the local bytes, confirm the root
document. Only then mark `verified` and save the mapping.

**6. Local cleanup (tailored CVs only).** After the verified mapping is saved, recheck
that the local file is a regular file whose hash still matches; then delete exactly
that file and record `local_deleted: true`. Never delete `P/master-cv.tex` or
anything after a failed, pending, skipped, conflicted or unverified upload. Never use
wildcards.

**7. Report** one row per file: created / updated / unchanged / skipped / failed /
conflict, the project link, and whether the local file was removed. End with: "Not
compiled. Open the project in Overleaf, set the compiler to pdfLaTeX, and compile."
Never claim success based on creation alone.

---

## /mark_applied [Company]

1. Use the company/role confirmed in this chat, or the argument; ask only for missing
   details. `python3 jptools.py jd "<Company>"` can supply jobId/title. Never invent an
   ID; use JSON `null` if the user confirms it's unknown. Never use an Overleaf
   project ID as a jobId.
2. Read and update `P/state/applied-companies.json` **in code** (a short Python
   script): keep it a JSON list of objects and preserve every other entry and field.
   Malformed → stop and report; never recreate it.
3. Match by jobId, else by normalized company + title. Identical → no-op. Conflicts →
   ask. Write atomically (temp file + rename) and verify valid JSON afterwards.
4. Report: added / already recorded, company, title, jobId (or "not listed"). A record
   doesn't mean you submitted an application.

---

## Errors and state

* A failed actor: `python3 apify_fetch.py scrape --only <source> --outdir $R`, restore
  `seen-jobs.before.json`, rerun stage-a.
* Apify limits, billing or captcha: stop and report the exact error.
* Never recreate, move or clean up anything in `P/state/`.
* Don't edit `*.py`, `templates/`, `prompts/` or `P/actor-inputs.json` unless asked.
  After editing a `.py`, run its `self-test`.
* Scoring uses `candidate.md`; tailoring uses the Master CV. If they disagree, the
  Master CV wins; flag the mismatch.
