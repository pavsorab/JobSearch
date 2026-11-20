#!/usr/bin/env python3
"""jptools: token-saving helpers for the Claude Code agent. Stdlib only.

The agent calls these instead of reading or writing big files itself. Each
command prints a short summary; the bulky data stays on disk.

  brief      scored.json -> compact judging packets  (runs/<D>/brief-N.md)
  render     judgments.json + scored.json -> triage-<D>.md + results-<D>.json
  jd         one job's JD + jobpipe verdict + draft bullet    (for /tailor)
  scaffold   new "<Name> <Year> - <Company>.tex" in output/, Master preamble
  check-tex  structural check + 2-page estimate, no compile   (/check_cv)
  new-profile  create profiles/<name>/ from templates/profile
  build-inputs profile.json + template -> profiles/<name>/actor-inputs.json
  doctor     setup check: python, .env, profile, master CV, Apify account
  scan-public  pre-push check of git-tracked files for personal data and tokens
  self-test  run this after any edit to this file

All paths below are inside the active profile folder (see jsprofile.py).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import jsprofile

ROOT = Path(__file__).resolve().parent
# Set by _activate() from the active profile. ROOT stays the repo root.
PDIR = ROOT
NAME = "Candidate"
MASTER = ROOT / "master-cv.tex"
OUTDIR = ROOT / "output"
OUT_FMT = "Candidate {year} - {{}}.tex"
NOTES = ROOT / "job-search-notes.md"


def _activate() -> None:
    global PDIR, NAME, MASTER, OUTDIR, OUT_FMT, NOTES
    PDIR = jsprofile.profile_dir()
    prof = jsprofile.load_profile(PDIR)
    NAME = jsprofile.display_name(prof)
    MASTER = PDIR / "master-cv.tex"
    OUTDIR = PDIR / "output"
    OUT_FMT = f"{NAME} {date.today().year} - {{}}.tex"
    NOTES = PDIR / "job-search-notes.md"
TIER_RANK = {"FOREIGN-MNC": 4, "FUNDED-PRODUCT/STARTUP": 3,
             "INDIAN-MNC/MNC": 2, "SERVICES": 1, "UNKNOWN": 0}

# Rendered word counts of the 10 earlier tailored CVs (all 2 pages): 766-911.
PAGE_OK, PAGE_BORDER = 900, 960


# ---------------------------------------------------------------------------
# JD trimming: drop company blurb, benefits, EEO. Never rewrites a line, so a
# quote taken from the trimmed text is still a verbatim quote of the JD.
# ---------------------------------------------------------------------------

_Q = r"(?:'|’)?"
KEEP_HEAD = re.compile(
    r"about (the )?(role|job|position|team|opportunit|you)|responsib|"
    rf"what (you{_Q}ll|you will)|you{_Q}ll (do|be|bring|need)|you will|day[- ]to[- ]day|"
    r"the role|role (overview|summary)|job (description|summary)|requirement|"
    r"qualif|skills|must|nice|preferred|bonus|experience|what you bring|"
    r"who you are|you have|ideal candidate|minimum|basic|technical|tech stack|"
    rf"what we{_Q}re looking|looking for|opportunit|key (respons|skill|qualif)",
    re.I)
DROP_HEAD = re.compile(
    r"^about [^\n]{1,40}$|who we are|our (story|mission|values|"
    r"culture|company|benefits|commitment)|benefits|perks|what we offer|"
    rf"we offer|why (join|work|you{_Q}ll love)|life at|equal (employment|"
    r"opportunit)|\beeo\b|diversity|inclusion|accommodation|privacy|"
    r"how to apply|additional information|disclaimer|recruit\w* (fraud|scam)|"
    r"compensation|total rewards|working at|hiring process|^why |location|"
    rf"where you{_Q}ll (be )?work|^language|scam|fraud|notice|visa|"
    r"work authori[sz]ation", re.I)
DROP_LINE = re.compile(
    r"equal opportunity|without regard to|regardless of (race|gender)|"
    r"sexual orientation|gender identity|protected (veteran|status|class)|"
    r"reasonable accommodation|e-verify|privacy (policy|notice)|#[A-Z]{2}-\w+|"
    r"^show (more|less)( show less)?$|recruitment fraud|"
    r"never ask (you )?for (money|payment)", re.I)
BULLET = re.compile(r"^\s*([-•*·▪◦●]|\d+[.)])\s+")


def _heading(line: str) -> tuple[bool, bool]:
    """-> (is_heading, is_strong)."""
    raw = line.strip()
    if not raw or (BULLET.match(raw) and not raw.startswith("**")):
        return False, False
    s = raw.strip("*#: ").strip()
    if not (2 <= len(s) <= 70) or len(s.split()) > 8 or s.endswith((".", ",", ";")):
        return False, False
    strong = (raw.endswith(":") or raw.startswith(("#", "**"))
              or (s.isupper() and len(s) > 3))
    return True, strong


def trim_jd(text: str | None, max_chars: int | None = None,
            job_id: str = "") -> tuple[str, int]:
    """-> (trimmed text, original length)."""
    text = text or ""
    orig = len(text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    sections: list[list[str]] = [[]]       # sections[0] = intro before any heading
    prio = [1]                               # 0 = KEEP_HEAD section, 1 = intro/other
    mode, seen_heading, intro = "keep", False, 0
    for ln in lines:
        if DROP_LINE.search(ln) or not ln.strip():
            continue
        is_h, strong = _heading(ln)
        if is_h:
            s = ln.strip().strip("*#: ")
            if KEEP_HEAD.search(s):
                mode, seen_heading = "keep", True
                sections.append([])
                prio.append(0)
            elif DROP_HEAD.search(s):
                mode, seen_heading = "drop", True
                continue
            elif strong:
                mode, seen_heading = "keep", True
                sections.append([])
                prio.append(1)
        if mode == "drop":
            continue
        if not seen_heading:            # pre-heading intro: often a company blurb
            if intro > 600:
                continue
            intro += len(ln)
        sections[-1].append(ln)
    prio = [p for p, s in zip(prio, sections) if s]
    sections = [s for s in sections if s]
    kept = sum(len(ln) + 1 for s in sections for ln in s)
    # safety net: a misread heading must never eat the responsibilities
    if orig > 1500 and kept < 800 and kept < 0.35 * orig:
        sections = [[ln for ln in lines if ln.strip() and not DROP_LINE.search(ln)]]
        prio = [0]
        kept = sum(len(ln) + 1 for ln in sections[0])
    if not max_chars or kept <= max_chars:
        return "\n".join(ln for s in sections for ln in s), orig
    # over budget: take lines round-robin so every kept section (responsibilities
    # AND requirements) is represented, instead of cutting the tail off
    take = [0] * len(sections)
    used = 0
    for tier in (0, 1):                      # role/requirements first, then the rest
        idx = [i for i, p in enumerate(prio) if p == tier]
        full = False
        while not full and any(take[i] < len(sections[i]) for i in idx):
            for i in idx:
                if take[i] < len(sections[i]):
                    cost = min(len(sections[i][take[i]]), 321) + 1
                    if used + cost > max_chars:
                        full = True
                        break
                    take[i] += 1
                    used += cost
    out = []
    for s, n in zip(sections, take):
        out += [ln if len(ln) <= 320 else ln[:320] + "…" for ln in s[:n]]
        if 0 < n < len(s):
            out.append("  […]")
    out.append(f"[trimmed {kept} -> {used} chars; full JD: python3 jptools.py jd {job_id}]")
    return "\n".join(out), orig


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return " ".join(s.lower().split())


def _term_in(term: str, text: str) -> bool:
    if term == "go":
        return re.search(r"\bGo\b", text) is not None
    return re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I) is not None


def _load(path: Path) -> Any:
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# brief
# ---------------------------------------------------------------------------

def _brief_entry(j: dict[str, Any], max_chars: int, max_sec: int) -> str:
    rf = j.get("resume_fit") or {}
    band = (j.get("experience_band") or {}).get("stated") or "not stated"
    no_fit = "" if j.get("resume_fit") else " (no resume_fit: not cross-checked)"
    cap = max_chars if j.get("triage_priority") == "primary" else max_sec
    jd, orig = trim_jd(j.get("description"), cap, str(j.get("job_id")))
    hard = "; ".join(f'{h.get("term")}: "{h.get("phrase", "")}"'
                     for h in rf.get("hard_requirement_gaps") or []) or "none"
    lines = [
        f"### {j.get('job_id')} · {j.get('company')} | {j.get('title')}",
        f"stack {j.get('stack_score')} ({j.get('stack_method')}) · "
        f"{j.get('company_tier')} · exp {band} · {j.get('triage_priority')}",
        "flags: " + ("; ".join(j.get("flags") or []) or "none"),
        "covered: " + (", ".join(rf.get("covered") or []) or "-") + no_fit,
        "gaps: " + ", ".join(rf.get("gaps") or []),
        "hard-req gaps: " + hard,
        f"JD ({orig} -> {len(jd)} chars):",
        jd,
    ]
    return "\n".join(lines) + "\n"


def cmd_brief(a: argparse.Namespace) -> int:
    s = _load(Path(a.scored))
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("brief-*.md"):
        old.unlink()
    jobs = [j for j in s["jobs"] if j.get("description")]
    no_desc = [j for j in s["jobs"] if not j.get("description")]
    chunks: list[list[str]] = [[]]
    size = 0
    for j in jobs:
        e = _brief_entry(j, a.max_chars, a.max_chars_secondary)
        if chunks[-1] and size + len(e) > a.chunk_bytes:
            chunks.append([])
            size = 0
        chunks[-1].append(e)
        size += len(e)
    files = []
    for i, ch in enumerate(chunks, 1):
        p = outdir / f"brief-{i}.md"
        head = (f"# Judging brief {i}/{len(chunks)} · {len(jobs)} jobs total · "
                f"{s.get('freshness_hours')}h window\n\n")
        p.write_text(head + "\n".join(ch))
        files.append(f"{p} ({p.stat().st_size // 1024} KB)")
    print(f"{len(jobs)} jobs with a description -> {len(files)} brief file(s):")
    print("\n".join("  " + f for f in files))
    if no_desc:
        print(f"{len(no_desc)} without a description (not judged): " +
              ", ".join(f"{j.get('company')} ({j.get('job_id')})" for j in no_desc))
    full = sum(len(j.get("description") or "") for j in jobs)
    kept = sum(Path(f.split(" (")[0]).stat().st_size for f in files)
    print(f"JD text {full // 1024} KB -> brief {kept // 1024} KB")
    return 0


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def _salary(j: dict[str, Any]) -> str:
    sal = j.get("salary")
    if sal in (None, "", [], {}):
        return "not listed"
    txt = sal if isinstance(sal, str) else json.dumps(sal, ensure_ascii=False)
    return f"{txt} (LinkedIn, unreliable)" if j.get("source") == "linkedin" else txt


def score_job(j: dict[str, Any], jd: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Apply one judgment. -> (scored fields, warnings)."""
    jid = str(j.get("job_id"))
    warn: list[str] = []
    flags = list(j.get("flags") or [])
    rs = int(jd.get("rs", 0))
    diff = int(jd.get("diff", 0))
    quote = (jd.get("quote") or "").strip().strip('"')
    if not 0 <= rs <= 40:
        warn.append(f"{jid}: rs {rs} outside 0-40, clamped")
        rs = max(0, min(40, rs))
    if not 0 <= diff <= 25:
        warn.append(f"{jid}: diff {diff} outside 0-25, clamped")
        diff = max(0, min(25, diff))
    if diff:
        why_zero = None
        rf = j.get("resume_fit")              # secondaries may not have one
        covered = [c.lower() for c in (rf or {}).get("covered") or []]
        if not quote:
            why_zero = "no quote"
        elif len(quote.split()) >= 15:
            why_zero = "quote is 15+ words"
        elif _norm(quote) not in _norm(j.get("description") or ""):
            why_zero = "quote not found verbatim in JD"
        elif rf and not any(_term_in(c, quote) for c in covered):
            why_zero = "quote has no resume_fit.covered term"
        if why_zero:
            warn.append(f"{jid} {j.get('company')}: differentiator zeroed ({why_zero})")
            flags.append(f"DIFF-ZEROED ({why_zero})")
            diff = 0
    cap = jd.get("cap")
    if cap not in (None, 60, 65, 70):
        warn.append(f"{jid}: cap {cap} not one of 60/65/70, ignored")
        cap = None
    if any(f.startswith("HARD-REQ-GAP") for f in flags) and cap is None and not jd.get("why"):
        warn.append(f"{jid} {j.get('company')}: HARD-REQ-GAP flag but no cap and no 'why'")
    raw = j.get("stack_score", 0) + rs + diff
    final = min(raw, cap) if cap else raw
    if jd.get("drop"):
        status = "DROPPED"
    elif final >= 75:
        status = "KEEP"
    elif final >= 65:
        status = "NEAR MISS"
    else:
        status = "BELOW"
    if status == "NEAR MISS" and not jd.get("why"):
        warn.append(f"{jid} {j.get('company')}: NEAR MISS needs a one-line 'why'")
    return ({"role_shape": rs, "differentiator": diff,
             "differentiator_quote": quote if diff else None,
             "uncapped_score": raw, "cap": cap, "score": final, "status": status,
             "why": jd.get("why") or jd.get("drop") or "", "flags": flags}, warn)


def _row(j: dict[str, Any]) -> str:
    link = j.get("apply_url") or j.get("url") or "not listed"
    link = f"[Apply]({link})" if link.startswith("http") else link

    def cell(x: Any) -> str:
        return str(x if x not in (None, "") else "not listed").replace("|", "/")
    return "| " + " | ".join(cell(x) for x in [
        j["score"], j.get("title"), j.get("company"), j.get("location"),
        j.get("posted_raw"), j.get("source"), j.get("stack_method"),
        _salary(j), j.get("company_tier"), link,
        "; ".join(j["flags"]) or "none"]) + " |"


def chat_table(rows: list[dict[str, Any]]) -> str:
    """Markdown table for the chat reply: the triage columns, flags shortened."""
    if not rows:
        return "(no roles at 65+)"
    out = ["| Score | Status | Title | Company | Location | Posted | Source | "
           "Stack method | Salary | Tier | Apply link | Flags |", "|---" * 12 + "|"]
    for r in rows:
        link = r.get("apply_url") or r.get("url") or ""
        flags = "; ".join(f.split(" —")[0] for f in r["flags"]) or "none"
        cells = [r["score"], r["status"], r.get("title"), r.get("company"),
                 r.get("location"), r.get("posted_raw"), r.get("source"),
                 r.get("stack_method"), _salary(r), r.get("company_tier"),
                 f"[Apply]({link})" if link.startswith("http") else "not listed", flags]
        out.append("| " + " | ".join(str(c if c not in (None, "") else "not listed")
                                     .replace("|", "/") for c in cells) + " |")
    return "\n".join(out)


HEADER = ("| Score | Title | Company | Location | Posted | Source | Stack method | "
          "Salary | Tier | Apply link | Flags |\n" + "|---" * 11 + "|")


def load_judgments(path: Path) -> dict[str, dict[str, Any]]:
    """.json = {job_id: {...}}; .jsonl = one {"id": ..., ...} per line, and a
    later line for the same id replaces the earlier one (corrections = append)."""
    if path.suffix != ".jsonl":
        return {str(k): v for k, v in _load(path).items()}
    out: dict[str, dict[str, Any]] = {}
    for n, ln in enumerate(path.read_text().splitlines(), 1):
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except ValueError as e:
            raise SystemExit(f"ERROR {path.name} line {n}: bad JSON ({e})")
        out[str(row.pop("id"))] = row
    return out


def cmd_render(a: argparse.Namespace) -> int:
    s = _load(Path(a.scored))
    judg = load_judgments(Path(a.judgments))
    d = a.date or date.today().isoformat()
    window = s.get("freshness_hours")
    notes = NOTES.read_text().lower() if NOTES.exists() else ""
    jobs = [j for j in s["jobs"] if j.get("description")]
    missing = [f"{j.get('job_id')} {j.get('company')}" for j in jobs
               if str(j.get("job_id")) not in judg]
    if missing and not a.allow_missing:
        print("ERROR: no judgment for:\n  " + "\n  ".join(missing))
        print("Add them to the judgments file (or pass --allow-missing).")
        return 2
    known = {str(j.get("job_id")) for j in jobs}
    warnings = [f"judgment for unknown job_id {k} ignored" for k in judg if k not in known]
    rows = []
    for j in jobs:
        jd = judg.get(str(j.get("job_id")))
        if jd is None:
            continue
        fields, w = score_job(j, jd)
        warnings += w
        r = {k: v for k, v in j.items() if k not in ("description", "raw_extra")}
        r.update(fields)
        r["window_hours"] = window
        if notes and r.get("company") and r["company"].lower() in notes:
            r["flags"].append("SEE job-search-notes.md")
        rows.append(r)
    rows.sort(key=lambda r: (r["score"], TIER_RANK.get(r.get("company_tier"), 0)),
              reverse=True)
    by = {k: [r for r in rows if r["status"] == k]
          for k in ("KEEP", "NEAR MISS", "BELOW", "DROPPED")}
    top5 = [r for r in rows if r["status"] in ("KEEP", "NEAR MISS")][:5]
    dd_path = Path(a.deepdive) if a.deepdive else Path(a.judgments).with_name("deepdive.md")
    deepdive = dd_path.read_text().strip() if dd_path.exists() else ""

    cov = s.get("coverage") or {}
    cov_lines = [f"- raw: {cov.get('raw')}"]
    for k, v in sorted((cov.get("dropped") or {}).items(), key=lambda kv: -kv[1]):
        cov_lines.append(f"- dropped, {k}: {v}")
    for k in ("unique_after_dedupe", "applied_companies_removed", "dedupe_removed",
              "survivors", "primary", "secondary", "biggest_noise_source"):
        if k in cov:
            cov_lines.append(f"- {k}: {cov[k]}")
    if cov.get("below_my_band"):
        cov_lines.append(f"- below my band: {len(cov['below_my_band'])} "
                         "(list in results json)")

    md = [f"# Job triage, {d}, {window}-hour window", "",
          f"{len(by['KEEP'])} roles at 75+; {len(by['NEAR MISS'])} near misses; "
          f"{len(by['BELOW'])} below 65; {len(by['DROPPED'])} dropped on judgment. "
          f"{len(rows)} of {len(jobs)} descriptions judged.", "",
          "## Results (75+)", "", HEADER] + [_row(r) for r in by["KEEP"]]
    md += ["", "## Near misses (65-74)", "", HEADER] + [_row(r) for r in by["NEAR MISS"]]
    md += [""] + [f"- {r['company']}, {r['title']}: {r['why']}" for r in by["NEAR MISS"]]
    md += ["", "## Top-5 deep dive", "", deepdive or "_Not written yet._", ""]
    md += ["## Below 65 and dropped on judgment", ""]
    md += [f"- {r['score']} · {r['status']} · {r['company']}, {r['title']}"
           + (f": {r['why']}" if r["why"] else "") for r in by["BELOW"] + by["DROPPED"]]
    md += ["", "## Coverage", ""] + cov_lines
    if warnings:
        md += ["", "## Render warnings", ""] + [f"- {w}" for w in warnings]
    (PDIR / "results").mkdir(exist_ok=True)
    out_md = PDIR / "results" / f"triage-{d}.md"
    out_js = PDIR / "results" / f"results-{d}.json"
    out_md.write_text("\n".join(md) + "\n")
    out_js.write_text(json.dumps({
        "date": d, "freshness_hours": window, "coverage": cov, "results": rows,
        "top_5_deep_dive": deepdive, "warnings": warnings}, indent=1, ensure_ascii=False))

    print(f"wrote results/{out_md.name}, results/{out_js.name}")
    print(f"KEEP {len(by['KEEP'])} · NEAR MISS {len(by['NEAR MISS'])} · "
          f"BELOW {len(by['BELOW'])} · DROPPED {len(by['DROPPED'])} (of {len(jobs)})")
    print(chat_table(by["KEEP"] + by["NEAR MISS"]))
    print("top-5 for the deep dive: " + ", ".join(
        f"{r['job_id']} {r['company']} ({r['score']})" for r in top5)
        + f"  [deepdive.md: {'included' if deepdive else 'MISSING'}]")
    print(f"coverage: raw {cov.get('raw')} -> survivors {cov.get('survivors')} · "
          f"biggest noise: {cov.get('biggest_noise_source')}")
    for w in warnings:
        print("WARN " + w)
    return 0


# ---------------------------------------------------------------------------
# jd (for /tailor)
# ---------------------------------------------------------------------------

def _candidates() -> list[dict[str, Any]]:
    """Jobs with descriptions, newest source first, deduped by job_id."""
    out, seen = [], set()
    scored = sorted(PDIR.glob("runs/*/scored.json"), reverse=True)
    for p in scored:
        try:
            jobs = _rows(_load(p))
        except (OSError, ValueError):
            continue
        for j in jobs:
            jid = str(j.get("job_id"))
            if j.get("description") and jid not in seen:
                seen.add(jid)
                out.append(dict(j, _src=str(p.relative_to(PDIR))))
    for p in sorted(PDIR.glob("runs/*/triage.json"), reverse=True):
        det = p.with_name("details.json")
        if not det.exists():
            continue
        try:
            jobs, desc = _rows(_load(p)), _load(det)
        except (OSError, ValueError):
            continue
        if not isinstance(desc, dict):
            continue
        for j in jobs:
            jid = str(j.get("job_id"))
            d = desc.get(jid) or desc.get(str(j.get("url")))
            if d and jid not in seen:
                seen.add(jid)
                out.append(dict(j, description=d, _src=str(det.relative_to(PDIR))))
    return out


def _rows(res: Any) -> list[dict[str, Any]]:
    """Results files come in several shapes: {"results": [...]}, {"jobs": [...]}, or a bare list."""
    if isinstance(res, list):
        rows = res
    elif isinstance(res, dict):
        rows = res.get("results") or res.get("jobs") or []
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def _key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def cmd_jd(a: argparse.Namespace) -> int:
    q = _key(a.query)
    cands = _candidates()
    hits = [j for j in cands if str(j.get("job_id")) == a.query.strip()]
    if not hits:
        hits = [j for j in cands if q and q in _key(j.get("company"))]
    if not hits:
        hits = [j for j in cands if q and q in _key(j.get("title"))]
    if not hits:
        print(f"NOT FOUND: no job matching '{a.query}' in runs/. "
              "Ask the user to paste the JD.")
        return 4
    if len(hits) > 1 and not a.first:
        print(f"{len(hits)} matches. Ask the user which one, then rerun with the job_id:")
        for j in hits[:15]:
            print(f"  {j.get('job_id')} · {j.get('company')} | {j.get('title')} "
                  f"· {j.get('posted_raw')} · {j['_src']}")
        return 3
    j = hits[0]
    jid = str(j.get("job_id"))
    print(f"# {j.get('company')} | {j.get('title')}  (job_id {jid}, from {j['_src']})")
    print(f"url: {j.get('apply_url') or j.get('url')}")
    verdict = None
    for p in sorted(PDIR.glob("results/results-*.json"), reverse=True):
        try:
            res = _load(p)
        except (OSError, ValueError):
            continue
        for r in _rows(res):
            if str(r.get("job_id")) == jid:
                verdict = (p.name, r, res)
                break
        if verdict:
            break
    if verdict:
        name, r, res = verdict
        print(f"jobpipe verdict ({name}): final {r.get('score')} · {r.get('status')} · "
              f"stack {r.get('stack_score')} + role_shape {r.get('role_shape')} + "
              f"diff {r.get('differentiator')} · cap {r.get('cap')}")
        if r.get("differentiator_quote"):
            print(f'differentiator quote: "{r["differentiator_quote"]}"')
        if r.get("why") or r.get("reason"):
            print(f"note: {r.get('why') or r.get('reason')}")
        bullet = _draft_bullet(jid, j, res)
        print("step-6 draft: " + (bullet or "none"))
    else:
        print("jobpipe verdict: none (job was never judged). Run Stage 0 from scratch.")
    rf = j.get("resume_fit") or {}
    print("covered: " + ", ".join(rf.get("covered") or []))
    print("gaps: " + ", ".join(rf.get("gaps") or []))
    print("hard-req gaps: " + ("; ".join(f'{h.get("term")}: "{h.get("phrase", "")}"'
                                       for h in rf.get("hard_requirement_gaps") or [])
                               or "none"))
    print("shallow: " + ", ".join(rf.get("shallow_hits") or []))
    desc = j.get("description") or ""
    text = desc if a.raw else trim_jd(desc)[0]
    print(f"\n## JD ({len(desc)} chars raw, {len(text)} shown)\n{text}")
    return 0


def _draft_bullet(jid: str, j: dict[str, Any], res: Any) -> str | None:
    dd = res.get("top_5_deep_dive") if isinstance(res, dict) else None
    if isinstance(dd, str) and dd:
        m = re.search(rf"^## {re.escape(jid)}\b.*?(?=^## |\Z)", dd, re.S | re.M)
        return ("\n" + m.group(0).strip()) if m else None
    if isinstance(dd, list):                      # legacy hand-written format
        for e in dd:
            blob = json.dumps(e, ensure_ascii=False)
            if jid in blob or (j.get("company") or "~") in blob:
                return e.get("bullet") if isinstance(e, dict) else None
    return None


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

def _split_master(text: str) -> tuple[list[str], str, str, str]:
    """-> (rule comments, preamble, header block, body from first \\section)."""
    pre_doc, _, rest = text.partition("\\begin{document}")
    head_comments, _, preamble = pre_doc.partition("\\documentclass")
    preamble = "\\documentclass" + preamble
    rules = [ln for ln in head_comments.splitlines()
             if ln.strip() and not re.match(r"^%\s*\[[A-Z]+\]", ln)
             and not ln.startswith("% SOURCE KEY")]
    i = rest.find("\\section")
    return rules, preamble, rest[:i], rest[i:]


def cmd_scaffold(a: argparse.Namespace) -> int:
    name = a.company.strip() + (a.suffix or "")
    OUTDIR.mkdir(exist_ok=True)
    target = OUTDIR / OUT_FMT.format(name)
    if target.resolve() == MASTER.resolve():
        print("ERROR: refusing to overwrite the Master CV")
        return 2
    if target.exists() and not a.force:
        print(f"EXISTS: {target.name}. Ask the user: overwrite (--force) or "
              "keep both (--suffix ' v2').")
        return 3
    _, preamble, header, _ = _split_master(MASTER.read_text())
    preamble = preamble.replace(f"{NAME} --- Master CV", NAME)
    header = header.replace("{\\large Master CV}", "{\\large <<HEADLINE>>}")
    target.write_text(
        f"% Tailored from master-cv.tex for {a.company} on "
        f"{date.today().isoformat()}.\n% Not compiled locally: render in Overleaf.\n"
        + preamble + "\\begin{document}" + header.rstrip() + "\n\n"
        "% <<BODY>> Replace this line with the tailored sections.\n\n"
        "\\end{document}\n")
    print(f"created output/{target.name}: Master preamble + contact header. Replace "
          "<<HEADLINE>> and the <<BODY>> line. Do not touch the preamble.")
    return 0


# ---------------------------------------------------------------------------
# check-tex
# ---------------------------------------------------------------------------

STD_MACROS = set("""
begin end item section subsection subsubsection textbf textit emph underline
texttt textsc textsf textrm mbox makebox href url small large Large LARGE huge Huge
normalsize footnotesize scriptsize tiny bfseries itshape ttfamily sffamily rmfamily
mdseries upshape hfill vfill vspace hspace newline linebreak par quad qquad
textbar textendash textemdash textbullet ldots dots noindent centering today
newpage clearpage pagebreak smallskip medskip bigskip hrule rule textwidth
linewidth baselineskip setlength addtolength label ref mathrm times cdot sim
approx ge le pm textasciitilde textasciicircum textbackslash and
""".split())


def _strip_comment(line: str) -> str:
    m = re.search(r"(?<!\\)%", line)
    return line[:m.start()] if m else line


def _packages(text: str) -> set[str]:
    pk = set()
    for m in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}",
                         "\n".join(_strip_comment(x) for x in text.splitlines())):
        pk |= {p.strip() for p in m.group(1).split(",") if p.strip()}
    return pk


def visible_words(body: str) -> int:
    t = "\n".join(_strip_comment(x) for x in body.splitlines())
    t = re.sub(r"\\href\{[^}]*\}", " ", t)
    t = re.sub(r"\\(begin|end)\{[^}]*\}(\[[^\]]*\])?", " ", t)
    t = re.sub(r"\\\\(\[[^\]]*\])?", " ", t)
    t = re.sub(r"\\(v|h)space\*?\{[^}]*\}", " ", t)
    t = re.sub(r"\\(setlength|addtolength)\{[^}]*\}\{[^}]*\}", " ", t)
    t = re.sub(r"\\[A-Za-z]+\*?", " ", t)
    t = re.sub(r"\\.", " ", t)
    t = re.sub(r"[{}\[\]]", " ", t)
    return len(re.findall(r"[A-Za-z0-9][\w'’./+#&-]*", t))


def _mask_macro_parameters(code: str) -> str:
    """Mask declared #n arguments only inside balanced LaTeX command bodies.

    Preserve offsets/newlines and leave invalid hashes for check_tex to report.
    """
    masked = list(code)
    declaration = re.compile(
        r"(?<!\\)\\(?:newcommand|renewcommand|providecommand)\*?\s*"
        r"(?:\{\s*\\[A-Za-z]+\s*\}|\\[A-Za-z]+)\s*"
        r"\[([1-9])\]\s*(?:\[[^\]]*\]\s*)?\{")
    for match in declaration.finditer(code):
        depth, i = 1, match.end()
        while i < len(code) and depth:
            char = code[i]
            if char == "\\":
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif (char == "#" and i + 1 < len(code)
                  and code[i + 1] in "123456789"[:int(match.group(1))]):
                masked[i] = " "
            i += 1
    return "".join(masked)


def check_tex(text: str, master_text: str, is_master: bool = False) -> tuple[list[str], list[str], list[str]]:
    """-> (errors, warnings, info)."""
    err, warn, info = [], [], []
    lines = text.splitlines()
    code = [_strip_comment(x) for x in lines]
    stack: list[tuple[str, int]] = []
    for n, ln in enumerate(code, 1):
        i = 0
        while i < len(ln):
            c = ln[i]
            if c == "\\":
                i += 2
                continue
            if c == "{":
                stack.append(("{", n))
            elif c == "}":
                if stack:
                    stack.pop()
                else:
                    err.append(f"line {n}: unmatched '}}'")
            i += 1
    for _, n in stack:
        err.append(f"line {n}: '{{' never closed")
    envs: list[tuple[str, int]] = []
    tabular = re.compile(r"tabular|array|align|longtable|eqnarray")
    in_doc = False
    parameter_safe = _mask_macro_parameters("\n".join(code)).split("\n")
    for n, ln in enumerate(code, 1):
        for m in re.finditer(r"\\(begin|end)\{([^}]+)\}", ln):
            kind, env = m.groups()
            if env == "document":
                in_doc = kind == "begin"
            if kind == "begin":
                envs.append((env, n))
            elif not envs:
                err.append(f"line {n}: \\end{{{env}}} without \\begin")
            elif envs[-1][0] != env:
                err.append(f"line {n}: \\end{{{env}}} closes \\begin{{{envs[-1][0]}}} "
                           f"from line {envs[-1][1]}")
                envs.pop()
            else:
                envs.pop()
        if not in_doc:
            continue
        body = re.sub(r"\\(href|url)\{[^}]*\}", "", ln)
        body = re.sub(r"(?<!\\)\$[^$]*(?<!\\)\$", "", body)
        if len(re.findall(r"(?<!\\)\$", body)) % 2:
            err.append(f"line {n}: unclosed $ math")
        in_tab = any(tabular.search(e) for e, _ in envs)
        if not in_tab and re.search(r"(?<!\\)&", body):
            err.append(f"line {n}: unescaped & (use \\&)")
        for ch, fix in (("#", "\\#"), ("_", "\\_"), ("^", "\\^{}")):
            checked = body
            if ch == "#":
                checked = re.sub(r"\\(href|url)\{[^}]*\}", "", parameter_safe[n - 1])
                checked = re.sub(r"(?<!\\)\$[^$]*(?<!\\)\$", "", checked)
            if re.search(r"(?<!\\)" + re.escape(ch), checked):
                err.append(f"line {n}: unescaped {ch} (use {fix})")
    for env, n in envs:
        err.append(f"line {n}: \\begin{{{env}}} never closed")
    flat = "\n".join(code)
    for o, c in (("(", ")"), ("[", "]")):
        no = len(re.findall(r"(?<!\\)\\" + re.escape(o), flat))
        nc = len(re.findall(r"(?<!\\)\\" + re.escape(c), flat))
        if no != nc:
            err.append(f"unbalanced \\{o} \\{c} math ({no} vs {nc})")
    for n, ln in enumerate(lines, 1):
        if "<<" in ln and ">>" in ln:
            err.append(f"line {n}: placeholder left in file: {ln.strip()[:60]}")
    new_pk = _packages(text) - _packages(master_text)
    if new_pk:
        err.append("packages not in the Master CV preamble: " + ", ".join(sorted(new_pk)))
    defined = set(re.findall(r"\\(?:re)?newcommand\*?\{?\\([A-Za-z]+)", text))
    defined |= set(re.findall(r"\\def\\([A-Za-z]+)", text))
    known = STD_MACROS | defined | set(re.findall(r"\\([A-Za-z]+)", master_text))
    used = set(re.findall(r"\\([A-Za-z]+)", "\n".join(code)))
    unknown = sorted(used - known)
    if unknown:
        warn.append("macros not used in the Master CV (verify in Overleaf): " +
                    ", ".join("\\" + u for u in unknown))
    body_txt = text.partition("\\begin{document}")[2]
    words = visible_words(body_txt)
    if is_master:
        info.append(f"visible words ~{words} (Master CV is intentionally longer than 2 pages)")
    else:
        verdict = ("likely 2 pages" if words <= PAGE_OK else
                   "borderline, may spill to page 3" if words <= PAGE_BORDER else
                   "likely over 2 pages: cut before Overleaf")
        info.append(f"visible words ~{words}: {verdict} (earlier 2-page tailored CVs "
                    "rendered 766-911 words). Estimate only.")
    body_code = "\n".join(_strip_comment(x) for x in body_txt.splitlines())
    n_sec = len(re.findall(r"\\section", body_code))
    n_item = len(re.findall(r"\\item\b", body_code))
    info.append(f"sections {n_sec}, bullets {n_item}")
    return err, warn, info


def cmd_check(a: argparse.Namespace) -> int:
    p = Path(a.file) if a.file else MASTER
    if not p.is_absolute() and not p.exists():
        for base in (OUTDIR, PDIR):
            if (base / p).exists():
                p = base / p
                break
    text = p.read_text()
    err, warn, info = check_tex(text, MASTER.read_text(), p.resolve() == MASTER.resolve())
    print(f"structural check: {'pass' if not err else 'FAIL'}  ({p.name})")
    for e in err:
        print("  ERROR " + e)
    for w in warn:
        print("  WARN  " + w)
    for i in info:
        print("  info  " + i)
    print("  Not compiled. Render in Overleaf to confirm layout and page count.")
    return 1 if err else 0


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    fails: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    jd = ("About Acme\nAcme is a leading company that does many great things for "
          "customers all over the world and has many offices.\n\nResponsibilities\n"
          "- Build Kubernetes operators in Go\n- Own the internal developer platform\n\n"
          "Benefits\n- Health insurance\n- Free lunch\n\nRequirements:\n- 10+ years\n"
          "Acme is an equal opportunity employer without regard to race.\n")
    t, _ = trim_jd(jd)
    ck("Build Kubernetes operators" in t, "trim keeps responsibilities")
    ck("10+ years" in t, "trim keeps requirements after a dropped section")
    ck("Free lunch" not in t, "trim drops benefits")
    ck("equal opportunity" not in t, "trim drops EEO line")
    ck("About the role" in trim_jd("About the role\n" + "x " * 500)[0],
       "About the role is kept")
    long = "Responsibilities\n" + "\n".join(f"- line {i} " + "y" * 60 for i in range(80))
    ck(len(trim_jd(long, 1000, "42")[0]) < 1200, "trim honours max_chars")
    ck("jptools.py jd 42" in trim_jd(long, 1000, "42")[0], "truncation names the jd command")
    two = long + "\nRequirements\n- 12+ years building platforms\n- Go"
    ck("12+ years" in trim_jd(two, 1000, "42")[0], "over budget keeps a later section")
    ck("Acme is a leading" not in trim_jd("About Acme®\nAcme is a leading co.\n" + two)[0],
       "About <Company>® blurb dropped")

    job = {"job_id": "1", "company": "Acme", "stack_score": 30, "flags": [],
           "description": "You will build Kubernetes operators in Go for our platform.",
           "resume_fit": {"covered": ["kubernetes", "go", "operator"]}}
    f, w = score_job(job, {"rs": 35, "diff": 20, "quote": "build Kubernetes operators in Go"})
    ck(f["score"] == 85 and f["status"] == "KEEP", "sum and KEEP")
    f, w = score_job(job, {"rs": 35, "diff": 20, "quote": "build Kubernetes operators", "cap": 70})
    ck(f["score"] == 70 and f["status"] == "NEAR MISS", "cap 70 applied")
    ck(any("why" in x for x in w), "NEAR MISS without why warns")
    f, _ = score_job(job, {"rs": 35, "diff": 20, "quote": "deep AWS landing zone expertise"})
    ck(f["differentiator"] == 0, "quote not in JD zeroes differentiator")
    f, _ = score_job(job, {"rs": 35, "diff": 20, "quote": "for our platform"})
    ck(f["differentiator"] == 0, "quote without covered term zeroes differentiator")
    f, _ = score_job(job, {"rs": 10, "diff": 0, "drop": "product backend"})
    ck(f["status"] == "DROPPED", "drop honoured")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "j.jsonl"
        p.write_text('{"id": "1", "rs": 10}\n\n{"id": 2, "rs": 5}\n{"id": "1", "rs": 30}\n')
        jl = load_judgments(p)
        ck(jl == {"1": {"rs": 30}, "2": {"rs": 5}}, f"jsonl: later line wins {jl}")

    master = ("\\documentclass{article}\n\\usepackage{enumitem}\n"
              "\\newcommand{\\role}[4]{#1}\n\\begin{document}\n\\role{a}{b}{c}{d}\n"
              "\\end{document}\n")
    good = master.replace("\\role{a}{b}{c}{d}", "Built \\textbf{CI/CD} \\& GitOps.")
    e, _, _ = check_tex(good, master)
    ck(not e, f"clean file passes: {e}")
    macro = "\\renewcommand{\\role}[4]{% comment\n  \\textbf{#1} {#3}\n #2\\# #4}\n"
    e, _, _ = check_tex(good.replace("Built", macro + "Built"), master)
    ck(not e, f"multiline macro parameters pass: {e}")
    for bad in (macro + "#1", macro.replace("#4", "#5"),
                macro.replace("#4", "#0"), macro.replace("#4", "#oops")):
        e, _, _ = check_tex(good.replace("Built", bad + "Built"), master)
        ck(any("unescaped #" in x for x in e), "invalid or out-of-body hash caught")
    e, _, _ = check_tex(good.replace("Built", macro.replace("#4", "bad_name") + "Built"), master)
    ck(any("unescaped _" in x for x in e), "macro body still checks other characters")
    e, _, _ = check_tex(good.replace("\\textbf{CI/CD}", "\\textbf{CI/CD"), master)
    ck(any("never closed" in x for x in e), "unclosed brace caught")
    e, _, _ = check_tex(good.replace("\\&", "&"), master)
    ck(any("unescaped &" in x for x in e), "unescaped & caught")
    e, _, _ = check_tex(good.replace("Built", "Built the_thing 100% of it"), master)
    ck(any("unescaped _" in x for x in e), "unescaped _ caught (and % treated as comment)")
    e, _, _ = check_tex(good.replace("\\usepackage{enumitem}",
                                     "\\usepackage{enumitem}\n\\usepackage{fontawesome5}"), master)
    ck(any("fontawesome5" in x for x in e), "new package caught")
    e, _, _ = check_tex(good.replace("\\end{document}", "\\begin{itemize}\n\\end{document}"), master)
    ck(any("itemize" in x for x in e), "env mismatch caught")
    e, _, _ = check_tex(good.replace("Built", "Built\\\\[5pt]"), master)
    ck(not e, f"\\\\[5pt] line break is not display math: {e}")
    e, _, _ = check_tex(good.replace("Built", "<<BODY>>"), master)
    ck(any("placeholder" in x for x in e), "placeholder caught")
    _, w, _ = check_tex(good.replace("Built", "\\faGithub Built"), master)
    ck(any("faGithub" in x for x in w), "unknown macro warned")
    ck(visible_words("\\section{Skills}\n\\item \\textbf{Go:} Python, Bash % note\n") == 4,
       "visible word count")

    ck(_rows([{"job_id": "1"}, "x"]) == [{"job_id": "1"}], "_rows accepts a bare list")
    ck(_rows({"jobs": [{"job_id": "2"}]}) == [{"job_id": "2"}], "_rows accepts {jobs: [...]}")
    ck(_rows({"date": "x"}) == [], "_rows tolerates no rows")

    if fails:
        print("FAIL\n  - " + "\n  - ".join(fails))
        return 1
    print("all jptools self-tests passed")
    return 0



# ---------------------------------------------------------------------------
# profiles: new-profile, build-inputs, doctor, scan-public
# ---------------------------------------------------------------------------

TEMPLATE_DIR = ROOT / "templates"
PROFILE_FILES = ("profile.json", "candidate.md", "skills.json", "master-cv.tex",
                 "scoring-notes.md", "tailor-notes.md", "job-search-notes.md")


def cmd_new_profile(a: argparse.Namespace) -> int:
    import shutil
    name = jsprofile.slug(a.name)
    dest = ROOT / "profiles" / name
    if dest.exists():
        print(f"EXISTS: profiles/{name}. Pick another name or edit it in place.")
        return 3
    shutil.copytree(TEMPLATE_DIR / "profile", dest)
    (dest / "state").mkdir(exist_ok=True)
    for f, body in (("seen-jobs.json", {"linkedin": {}, "indeed": {}}),
                    ("applied-companies.json", [])):
        if not (dest / "state" / f).exists():
            (dest / "state" / f).write_text(json.dumps(body, indent=2) + "\n")
    print(f"created profiles/{name}/. Next: set JOBSEARCH_PROFILE={name} in .env, then "
          "run /setup_profile with your resume (or fill the files by hand).")
    return 0


def build_inputs(prof: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """Fill the actor template from profile.json. Each query list is written once."""
    t = json.loads(json.dumps(template))
    s = prof.get("search") or {}
    loc = prof.get("location") or {}
    exp = prof.get("experience") or {}
    cfg = prof.get("config") or {}
    fam = s.get("boolean_queries") or []
    bq = [f["query"] for f in fam]
    li = t["linkedin"]
    li["keyword"] = list(s.get("linkedin_keywords") or []) + bq
    li["locations"] = [loc.get("linkedin") or "Bengaluru, Karnataka, India"]
    if s.get("linkedin_title_exclude") is not None:
        li["jobTitleExclude"] = s["linkedin_title_exclude"]
    li["companyExclude"] = s.get("linkedin_company_exclude") or cfg.get("blocked_companies") or []
    if s.get("linkedin_experience_level"):
        li["experienceLevel"] = s["linkedin_experience_level"]
    li["resumeKeywords"] = s.get("linkedin_resume_keywords") or [
        {"keyword": terms[0][0], "aliases": [x for x, _ in terms[1:]]}
        for terms in (prof.get("keyword_groups") or {}).values() if terms]
    nk = t["naukri_pass1"]
    nk["searchQueries"] = list(s.get("naukri_keywords") or s.get("linkedin_keywords") or []) + bq
    nk["cityTypeGid"] = loc.get("naukri_city_gid", 97)
    nk["experienceMin"] = exp.get("min", 5)
    nk["experienceMax"] = exp.get("max", 15)
    nk["stateKey"] = s.get("naukri_state_key") or jsprofile.slug(prof.get("name", "")) + "-jobs"
    ik = list(s.get("indeed_keywords") or (s.get("linkedin_keywords") or [])[:1])
    for key in ("indeed", "indeed_remote_pass"):
        t[key]["keyword"] = ik[0] if ik else (bq[0] if bq else "")
        t[key]["_searchQueries"] = ik + bq
    t["indeed"]["location"] = loc.get("indeed") or "Bengaluru, Karnataka"
    t["_search_families"] = fam
    return t


def _load_template() -> dict[str, Any]:
    return _load(TEMPLATE_DIR / "actor-inputs.template.json")


def cmd_build_inputs(a: argparse.Namespace) -> int:
    prof = jsprofile.load_profile(PDIR)
    errs = jsprofile.validate(prof)
    if errs:
        print("ERROR profile.json:\n  " + "\n  ".join(errs))
        return 2
    out = build_inputs(prof, _load_template())
    dest = PDIR / "actor-inputs.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    s = prof.get("search") or {}
    print(f"wrote {dest.relative_to(ROOT)}: linkedin {len(out['linkedin']['keyword'])} "
          f"queries, naukri {len(out['naukri_pass1']['searchQueries'])}, indeed "
          f"{len(out['indeed']['_searchQueries'])} x2 (city + remote). "
          f"{len(s.get('boolean_queries') or [])} boolean families.")
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    import os
    import subprocess
    bad = 0

    def say(level: str, msg: str) -> None:
        nonlocal bad
        bad += level == "FAIL"
        print(f"{level:4} {msg}")

    v = sys.version_info
    say("ok" if v >= (3, 9) else "FAIL", f"python {v.major}.{v.minor} (needs 3.9+)")
    jsprofile.load_env()
    say("ok" if (ROOT / ".env").exists() else "WARN", ".env present (copy .env.example)")
    tok = os.environ.get("APIFY_TOKEN", "")
    say("ok" if tok and "PASTE" not in tok else "FAIL", "APIFY_TOKEN set")
    eu = os.environ.get("APIFY_EXPECTED_USER", "")
    say("ok" if eu and not eu.startswith("your-") else "WARN", "APIFY_EXPECTED_USER set")
    pd = jsprofile.profile_dir(required=False)
    if not pd:
        say("FAIL", "JOBSEARCH_PROFILE points to a profile folder with profile.json")
        return 1
    say("ok", f"profile: {pd.relative_to(ROOT) if pd.is_relative_to(ROOT) else pd}")
    prof = jsprofile.load_profile(pd)
    for e in jsprofile.validate(prof):
        say("FAIL", "profile.json: " + e)
    for f in PROFILE_FILES:
        say("ok" if (pd / f).exists() else "FAIL", f"{f} present")
    for f in ("seen-jobs.json", "applied-companies.json"):
        say("ok" if (pd / "state" / f).exists() else "WARN", f"state/{f} present")
    try:
        sk = _load(pd / "skills.json")
        voc = {x.lower() for x in sk.get("vocabulary", [])}
        miss = [x for x in sk.get("have", []) if x.lower() not in voc]
        say("ok" if not miss else "FAIL",
            "skills.json: every `have` term is in `vocabulary`" + (f" (missing {miss[:5]})" if miss else ""))
    except (OSError, ValueError) as e:
        say("FAIL", f"skills.json unreadable: {e}")
    if MASTER.exists():
        txt = MASTER.read_text()
        err, _, _ = check_tex(txt, txt, True)
        say("ok" if not err else "FAIL", "master-cv.tex structural check" +
            (f" ({err[0]})" if err else ""))
        say("ok" if txt.count("\\begin{document}") == 1 else "FAIL",
            "master-cv.tex mentions \\begin{document} exactly once (not in comments)")
        for marker in (f"{NAME} --- Master CV", "{\\large Master CV}"):
            say("ok" if marker in txt else "WARN", f"master-cv.tex has scaffold marker `{marker}`")
    ai = pd / "actor-inputs.json"
    if not ai.exists():
        say("FAIL", "actor-inputs.json missing: run `python3 jptools.py build-inputs`")
    else:
        fresh = build_inputs(prof, _load_template())
        say("ok" if _load(ai) == fresh else "WARN",
            "actor-inputs.json matches profile.json (else rerun build-inputs)")
    oe = os.environ.get("OVERLEAF_EXPECTED_EMAIL", "")
    say("ok" if oe and "@" in oe else "info", "OVERLEAF_EXPECTED_EMAIL set (only for /publish)")
    if a.online:
        r = subprocess.run([sys.executable, str(ROOT / "apify_fetch.py"), "whoami"],
                           capture_output=True, text=True)
        for ln in (r.stdout + r.stderr).strip().splitlines():
            say("ok" if r.returncode == 0 else "FAIL", ln)
    print("doctor: " + ("all required checks passed" if not bad else f"{bad} problem(s)"))
    return 1 if bad else 0


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<![\w.])(?:\+91[ -]?)?[6-9]\d{9}(?!\d)")
_TOKEN = re.compile(r"apify_api_[A-Za-z0-9]{16,}")
_ALLOWED_EMAIL = re.compile(r"@(example\.(com|org)|anthropic\.com|users\.noreply\.github\.com)$", re.I)


def cmd_scan_public(a: argparse.Namespace) -> int:
    import subprocess
    try:
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"],
                               cwd=ROOT, capture_output=True, text=True, check=True
                               ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        print("ERROR: not a git repository (run `git init` first)")
        return 2
    names = set()
    for pj in (ROOT / "profiles").glob("*/profile.json"):
        try:
            n = json.loads(pj.read_text()).get("name", "").strip()
        except ValueError:
            continue
        names |= {w for w in n.split() if len(w) > 3}
    name_rx = re.compile(r"\b(" + "|".join(map(re.escape, sorted(names))) + r")\b", re.I) if names else None
    hits = []
    for f in files:
        if f.startswith("profiles/") and f not in ("profiles/README.md", "profiles/.gitkeep"):
            hits.append(f"{f}: personal profile file would be committed")
            continue
        if f == ".env" or f.endswith("settings.local.json"):
            hits.append(f"{f}: secrets file would be committed")
            continue
        try:
            text = (ROOT / f).read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, ln in enumerate(text.splitlines(), 1):
            for m in _EMAIL.finditer(ln):
                if not _ALLOWED_EMAIL.search(m.group(0)):
                    hits.append(f"{f}:{n}: email {m.group(0)}")
            if _PHONE.search(ln):
                hits.append(f"{f}:{n}: phone-like number")
            if _TOKEN.search(ln):
                hits.append(f"{f}:{n}: Apify token")
            if name_rx and name_rx.search(ln):
                hits.append(f"{f}:{n}: profile name '{name_rx.search(ln).group(0)}'")
    if hits:
        print(f"scan-public: {len(hits)} finding(s). Fix before pushing:")
        for h in hits[:200]:
            print("  " + h)
        return 1
    print(f"scan-public: clean ({len(files)} files that git would publish)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("--scored", required=True)
    b.add_argument("--outdir", required=True)
    b.add_argument("--max-chars", type=int, default=2400, help="JD chars, primary jobs")
    b.add_argument("--max-chars-secondary", type=int, default=1600,
                   help="JD chars, secondary jobs")
    b.add_argument("--chunk-bytes", type=int, default=24000, help="bytes per brief file")
    r = sub.add_parser("render")
    r.add_argument("--scored", required=True)
    r.add_argument("--judgments", required=True)
    r.add_argument("--deepdive", help="default: deepdive.md next to judgments")
    r.add_argument("--date")
    r.add_argument("--allow-missing", action="store_true")
    j = sub.add_parser("jd")
    j.add_argument("query", help="job_id, company or title fragment")
    j.add_argument("--raw", action="store_true", help="untrimmed JD")
    j.add_argument("--first", action="store_true", help="take the newest match")
    s = sub.add_parser("scaffold")
    s.add_argument("company")
    s.add_argument("--suffix", default="")
    s.add_argument("--force", action="store_true")
    c = sub.add_parser("check-tex")
    c.add_argument("file", nargs="?")
    n = sub.add_parser("new-profile")
    n.add_argument("name")
    sub.add_parser("build-inputs")
    d = sub.add_parser("doctor")
    d.add_argument("--online", action="store_true", help="also check the Apify account")
    sub.add_parser("scan-public")
    sub.add_parser("self-test")
    ap.add_argument("--profile", help="profile name or path (default: JOBSEARCH_PROFILE)")
    a = ap.parse_args()
    jsprofile.select(a.profile)
    if a.cmd not in ("self-test", "new-profile", "scan-public"):
        if a.cmd == "doctor" and not jsprofile.profile_dir(required=False):
            return cmd_doctor(a)
        _activate()
    return {"brief": cmd_brief, "render": cmd_render, "jd": cmd_jd,
            "scaffold": cmd_scaffold, "check-tex": cmd_check,
            "new-profile": cmd_new_profile, "build-inputs": cmd_build_inputs,
            "doctor": cmd_doctor, "scan-public": cmd_scan_public,
            "self-test": lambda _: self_test()}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
