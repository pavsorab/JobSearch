# JobSearch

An AI-agent job hunter for India. It scrapes fresh jobs from **LinkedIn, Naukri and
Indeed**, filters and scores them against *your* profile, and tailors your LaTeX CV to
the best matches. It runs inside **Claude Code** or **OpenAI Codex CLI**.

- **Deterministic first, AI second.** Freshness, location, experience band, dedupe,
  already-seen and already-applied filtering and a 35-point keyword score are plain
  Python (stdlib only). The agent adds only the judgment: does the role's real work
  fit you, and does the JD ask for something rare that you have?
- **Honest tailoring.** `/tailor` reorders and rewords your Master CV for one job and
  never invents experience. It stops twice for your approval.
- **Your data stays local.** Everything personal lives in `profiles/`, which git
  ignores.

```
/find_jobs  →  scrape (Apify)  →  stage A filters  →  descriptions  →  stage B score
            →  agent judges role shape + differentiator  →  ranked table + top-5 deep dive
/tailor <Company>  →  tailored .tex  →  /publish (optional, Overleaf)  →  /mark_applied
```

> **Status.** Scraping goes through third-party Apify actors whose input formats can
> change, and LinkedIn, Naukri and Indeed have their own terms of use. You run this at
> your own risk and on your own accounts. Not affiliated with LinkedIn, Naukri, Indeed,
> Apify, Overleaf, Anthropic or OpenAI.

---

## What you need

| | Why | Cost |
|---|---|---|
| macOS or Linux with Python 3.9+ | runs the pipeline (no `pip install`) | free |
| Claude Code **or** Codex CLI | runs the agent | your Claude / ChatGPT plan |
| An [Apify](https://apify.com) account + API token | runs the three scrapers | pay per run; the free tier's monthly credit covers some runs. Check each actor's pricing page |
| Your resume (PDF, DOCX or LaTeX) | becomes your Master CV | |
| Overleaf account + Node.js (optional) | `/publish` uploads tailored CVs | free |

## Quick start

### 1. Get the code

```bash
git clone https://github.com/<you>/JobSearch.git ~/JobSearch
```
```bash
cd ~/JobSearch && cp .env.example .env
```

### 2. Fill in `.env`

Open `.env` and set:

- `APIFY_TOKEN`: console.apify.com → Settings → **API & Integrations** → Personal
  API token.
- `APIFY_EXPECTED_USER`: your Apify username. Every run checks that the token
  belongs to this account.
- Leave `JOBSEARCH_PROFILE=examples/demo` for now.

Check the install with the fictional demo profile:
```bash
python3 jptools.py doctor --online
```
```bash
bash examples/demo/run-offline.sh
```

### 3. Start your agent

**Claude Code**
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
```bash
cd ~/JobSearch && claude
```

**Codex CLI.** Merge `codex/config.toml.example` into `~/.codex/config.toml`: the
scraper needs network access from the sandbox. Then:
```bash
cd ~/JobSearch && codex
```
Codex reads `AGENTS.md`, so typing `/find_jobs` etc. as a message works. For real
slash commands, optionally copy the prompts (Codex then lists them as
`/prompts:find_jobs` etc.):
```bash
mkdir -p ~/.codex/prompts && cp codex/prompts/*.md ~/.codex/prompts/
```

### 4. Create your profile from your resume

In the agent:
```
/setup_profile ~/Downloads/my-resume.pdf
```
It creates `profiles/<name>/` with your Master CV (transcribed into the LaTeX
template), `candidate.md`, `skills.json`, scoring and tailoring notes, and
`profile.json` with searches and filters. Then it **stops** so you can review. Set
`JOBSEARCH_PROFILE=<name>` in `.env`.

Before the first search, read and correct these:

1. `candidate.md`: answer the **Open questions**. Everything downstream trusts this.
2. `profile.json` → `search`: your queries (see [Writing search queries](#writing-search-queries)).
3. `profile.json` → `config.blocked_companies`: your current employer's name variants.
4. `master-cv.tex`: the SOURCE DISCREPANCIES comments at the top.

Then run:
```bash
python3 jptools.py build-inputs && python3 jptools.py doctor --online
```

### 5. Find jobs

```
/find_jobs 7d
```
Use a 7-day window for the first run and plain `/find_jobs` (48 hours) after that. A
run takes several minutes. Results go to `profiles/<name>/results/triage-<date>.md`
and are pasted into the chat.

## Commands

| Command | What it does |
|---|---|
| `/setup_profile <resume>` | draft a profile from a resume, then stop for review |
| `/doctor` | check Python, `.env`, profile files, Master CV and Apify account |
| `/find_jobs` · `/find_jobs 72h` · `/find_jobs 7d` | scrape, filter, score, deep-dive the top 5 |
| `/tailor <Company>` | tailor the Master CV for one job; paste a JD after the name to use it instead |
| `/tailor <Company> --publish` | tailor, then upload to Overleaf |
| `/publish` · `/publish "<file>.tex"` · `/publish --all` | upload tailored CVs to private Overleaf projects |
| `/mark_applied [Company]` | record the application so future runs skip it |
| `/check_cv [file]` | static LaTeX check plus a 2-page estimate (no compile) |

Use one chat for `/find_jobs` and a fresh chat per role for `/tailor` → `/publish` →
`/mark_applied` (`/clear` in Claude Code, a new session in Codex).

## Writing search queries

Queries live in `profiles/<name>/profile.json` under `search`. Write each query once;
`python3 jptools.py build-inputs` copies them into the scraper inputs for all three
sites.

```json
"search": {
  "linkedin_keywords": ["platform engineer", "site reliability engineer kubernetes"],
  "naukri_keywords":   ["platform engineer", "devops engineer kubernetes"],
  "indeed_keywords":   ["platform engineer kubernetes"],
  "boolean_queries": [
    {"id": "platform", "query": "(\"platform engineer\" OR \"developer platform\" OR \"internal developer platform\") AND Kubernetes"},
    {"id": "gitops",   "query": "Kubernetes AND (GitOps OR ArgoCD) AND (platform OR developer)"}
  ],
  "linkedin_title_exclude": ["intern", "junior", "manager", "director"]
}
```

- **Short keywords** are plain phrases; each runs as its own search.
- **Boolean queries** use `AND`, `OR`, parentheses and `"quoted phrases"`. They run
  on all three sites; Indeed's support for boolean syntax is partial, so keep a plain
  `indeed_keywords` entry too.
- 4–8 boolean queries is a good range. Each query costs Apify credit on every run.
- Rerun `build-inputs` after every edit (`/doctor` warns if you forget).

## Profile reference (`profile.json`)

| Key | Meaning |
|---|---|
| `name` | your name, exactly as in the Master CV footer `<name> --- Master CV` |
| `config.blocked_companies` | current employer name variants. Exact match after normalisation, so "Volvo Cars" doesn't block "Volvo Group" |
| `config.candidate_years`, `config.drop_ceiling_at_or_below`, `config.underbanded_ceilings` | experience band: a JD whose stated upper bound is at or below the ceiling is dropped |
| `config.allowed_locations` | substrings a job location must contain (default `bengaluru, bangalore, remote, india`) |
| `config.freshness_hours` | default 48 |
| `location.linkedin` / `location.indeed` | location strings sent to the scrapers |
| `location.naukri_city_gid` | Naukri city code. 97 = Bengaluru. For another city, see below |
| `experience.min` / `max` | Naukri experience filter, in years |
| `search.*` | see [Writing search queries](#writing-search-queries); also `linkedin_company_exclude`, `linkedin_experience_level`, `linkedin_resume_keywords` and `naukri_state_key` (optional) |
| `keyword_groups` | about 12 groups of your core skills: `{"Kubernetes": [["kubernetes", false], ["k8s", false]], "Go": [["Go", true]]}`. `true` = case-sensitive, for short tokens. The keyword score counts matched groups |
| `filters.title_drop` | regex of titles you never want (for example `\b(director\|vp )`) |
| `filters.out_of_domain` | regex of out-of-domain titles |
| `filters.dev_hint` + `dev_hint_label` | regex that only **flags** a job for the agent to judge |

**Naukri city code:** the Naukri actor filters by a numeric city id. Bengaluru is 97.
For another city, open the Naukri actor's input page on Apify and check the city
options. If you can't find it, leave 97 and rely on `allowed_locations` plus the
LinkedIn and Indeed searches.

## Overleaf publishing (optional)

`/publish` uploads a tailored `.tex` as `main.tex` into a new **private** Overleaf
project, reads it back to verify the upload, then removes the local copy. It never
compiles or shares anything.

Install Node.js (`brew install node` on macOS), then install olcli and log in to
**your own** Overleaf account:
```bash
npm install -g @aloth/olcli
```
```bash
olcli auth --browser
```
Connect it to your agent. For Claude Code:
```bash
claude mcp add --scope user olcli -- olcli-mcp
```
For Codex, uncomment `[mcp_servers.olcli]` in `~/.codex/config.toml`.

Set `OVERLEAF_EXPECTED_EMAIL` in `.env`. `/publish` checks it first and stops if olcli
is logged in to someone else's account. If olcli can't report the account, it asks
you to confirm once per chat.

Without olcli, upload the file from `profiles/<name>/output/` yourself: Overleaf →
New Project → Upload Project, set the compiler to pdfLaTeX, and compile.

## Privacy and security

- `profiles/`, `.env` and `.claude/settings.local.json` are git-ignored. Only the
  fictional `examples/demo` profile is committed.
- Secrets are read from `.env` by the scripts. The agents are told never to print
  them, and Claude Code is blocked from reading `.env`.
- The agents never use an Apify MCP connector: it could be logged in to a different
  account. `apify_fetch.py whoami` checks the token's owner on every run.
- Before you push changes to a public fork:
  ```bash
  python3 jptools.py scan-public
  ```
  It lists any email, phone number, Apify token, profile name or profile file that
  git would publish.

**Moving to another computer:** clone the repo there, then copy your
`profiles/<name>/` folder and `.env` across directly (AirDrop, USB, a private repo).
Never through the public repository.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No profile selected` / `Profile not found` | set `JOBSEARCH_PROFILE` in `.env`; run commands from the repo root |
| `APIFY_TOKEN is not set` | fill `.env` |
| `ERROR: expected Apify user …` | the token belongs to another account; fix `.env` |
| `Cannot reach api.apify.com` in Codex | enable `network_access` (see `codex/config.toml.example`) |
| Apify HTTP 402/403 | out of credit, or the actor needs enabling or renting in the Apify console |
| Zero results | often genuine for 48 hours; try `/find_jobs 7d`. The coverage block names the bottleneck |
| Everything "already seen" on a re-run | restore `runs/<date>/seen-jobs.before.json` to `state/seen-jobs.json` (the agent knows how) |
| `/tailor` scaffold missing the header | your Master CV must follow `templates/master-cv-template.tex`; run `/doctor` |
| `/publish` says olcli isn't connected | redo the Overleaf steps, then restart the agent |

## Repository layout

```
AGENTS.md              agent instructions (Codex reads it; CLAUDE.md imports it)
jobpipe.py             stage A/B pipeline: filters, dedupe, keyword score, gap analysis
jptools.py             brief, render, jd, scaffold, check-tex, new-profile,
                       build-inputs, doctor, scan-public
apify_fetch.py         runs the Apify actors; reads the token from .env
jsprofile.py           .env + profile loader shared by the three scripts
prompts/               SCORING.md and TAILOR.md: generic method, with per-profile notes
templates/             actor-input template, Master CV template, new-profile skeleton
examples/demo/         fictional profile + fixtures used by the self-tests and CI
profiles/              your data (git-ignored)
.claude/  codex/       Claude Code commands/permissions; Codex prompts/config example
```

## Contributing

Run the tests after any code change:
```bash
python3 jobpipe.py self-test && python3 jptools.py self-test && python3 apify_fetch.py self-test && bash examples/demo/run-offline.sh
```
CI runs the same on Python 3.9 and 3.12. The self-tests use only `examples/demo`, so
they pass whatever profile you have locally. Keep personal data out of pull requests
(`scan-public`).

## License

MIT. See `LICENSE`.
