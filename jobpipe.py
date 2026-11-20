#!/usr/bin/env python3
"""
jobpipe — deterministic pre-triage for the Apify job-sourcing agent.

Everything in here used to be English in the v2 prompt. It is now code, so it
runs the same way every time and you can test it:

    python3 jobpipe.py self-test

Pipeline (stage-a, before any description is fetched):
    ingest -> normalize -> 48h cutoff -> seen-jobs -> applied-companies
    -> mechanical drops -> dedupe -> rank -> liveness -> triage.json

Then you fetch descriptions for the primary set only, and run:

    python3 jobpipe.py stage-b --triage triage.json --details details.json

stage-b recomputes the STACK score and the experience band against the real
description and emits scored.json for the model to judge ROLE SHAPE and
DIFFERENTIATOR PULL on.

All person-specific settings (keyword groups, title filters, blocked employer,
search queries, tunables) come from the active profile: see jsprofile.py.

Stdlib only. No dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import jsprofile

HERE = os.path.dirname(os.path.abspath(__file__))
PDIR: str = HERE          # active profile folder; set by install_profile()
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731  (patched in self-test)

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "freshness_hours": 48,
    "candidate_years": 10,
    # experience band
    "drop_ceiling_at_or_below": 9,
    "underbanded_ceilings": [10, 11],
    # mechanical drops
    "min_keyword_groups": 3,           # naukri / indeed
    "linkedin_min_match_score": 35,    # keywordMatchScorePercentage
    # how many jobs get a description fetched (cost control, not a hard cap —
    # the rest are still emitted as `secondary` and listed in coverage)
    "primary_set_size": 15,
    # permanent company-level block: current employer. EXACT match after
    # normalization, so "Volvo Cars" never catches "Volvo Group". Set per profile.
    "blocked_companies": [],
    "allowed_locations": ["bengaluru", "bangalore", "remote", "india"],
    "liveness": {
        "enabled": True,
        "timeout_s": 15,
        "sleep_between_s": 1.5,
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    },
    "title_match": {
        # exact-after-normalization -> EXCLUDED
        # jaccard >= review_threshold -> REVIEW (surfaced, never auto-dropped)
        "review_threshold": 0.75,
    },
    "paths": {
        "applied_companies": "state/applied-companies.json",
        "seen_jobs": "state/seen-jobs.json",
    },
}


def _merge(cfg: dict[str, Any], user: dict[str, Any]) -> None:
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v


def load_config(path: str | None = None,
                prof: dict[str, Any] | None = None) -> dict[str, Any]:
    """DEFAULT_CONFIG <- profile.json `config` <- optional --config override file."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    prof = prof if prof is not None else _PROFILE
    if prof:
        _merge(cfg, prof.get("config") or {})
    if path:
        with open(path) as fh:
            _merge(cfg, json.load(fh))
    return cfg


# ---------------------------------------------------------------------------
# keyword groups (from the profile)
# ---------------------------------------------------------------------------

# strict=True  -> match is case-sensitive (protects short/ambiguous tokens like
#                 "Go" from matching "go-to-market", "ongoing", etc.)
KEYWORD_GROUPS: dict[str, list[tuple[str, bool]]] = {}


def _kw_regex(term: str, strict: bool) -> re.Pattern[str]:
    flags = 0 if strict else re.IGNORECASE
    return re.compile(r"(?<![\w/-])" + re.escape(term) + r"(?![\w-])", flags)


_KW_COMPILED: dict[str, list[tuple[re.Pattern[str], str]]] = {}


def matched_groups(*texts: str | Iterable[str] | None) -> dict[str, list[str]]:
    """Return {group: [matched terms]} across all supplied text."""
    blob_parts: list[str] = []
    for t in texts:
        if t is None:
            continue
        if isinstance(t, str):
            blob_parts.append(t)
        else:
            blob_parts.extend(str(x) for x in t)
    blob = "\n".join(blob_parts)
    out: dict[str, list[str]] = {}
    for group, pats in _KW_COMPILED.items():
        hits = [term for pat, term in pats if pat.search(blob)]
        if hits:
            out[group] = hits
    return out


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------

# ONLY legal-entity suffixes. Descriptive words (Cars, Group, Trucks, Labs)
# are load-bearing and are never stripped: "Volvo Cars" != "Volvo Group".
_LEGAL_SUFFIXES = [
    "private limited", "pvt ltd", "pvt. ltd.", "pvt", "private", "limited",
    "ltd", "llp", "llc", "inc", "incorporated", "corp", "corporation",
    "gmbh", "ag", "sas", "sa", "bv", "nv", "plc", "co",
]

_COMPANY_PAREN = re.compile(r"\s*\((india|bangalore|bengaluru|r&d|gcc)[^)]*\)\s*", re.I)

# Trailing geography on a legal entity name: "Volvo Cars India" is Volvo Cars.
# Descriptive words (cars, group, trucks, labs) are NEVER in this list.
_GEO_SUFFIXES = ["india", "usa", "us", "uk", "apac", "emea", "asia",
                 "bangalore", "bengaluru", "global"]


def normalize_company(name: str | None) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    s = _COMPANY_PAREN.sub(" ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    changed = True
    while changed:
        changed = False
        for suf in _LEGAL_SUFFIXES + _GEO_SUFFIXES:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
    return s


_TITLE_ABBREV = {
    "sr": "senior", "snr": "senior", "jr": "junior",
    "engr": "engineer", "engg": "engineer", "eng": "engineer",
    "dev": "developer", "ops": "operations",
    "k8s": "kubernetes", "swe": "software engineer",
    "sre": "site reliability engineer",
}
_TITLE_NOISE = {
    "the", "a", "an", "of", "and", "for", "to", "at", "in", "with",
    "remote", "hybrid", "onsite", "on-site", "india", "bengaluru",
    "bangalore", "blr", "f", "m", "d", "x", "w", "hiring", "urgent",
    "immediate", "opening", "openings", "position", "positions", "role",
}
_TITLE_PAREN = re.compile(r"\([^)]*\)")
_TITLE_REQID = re.compile(r"\b(req|job|jr|id)[-_ ]?\d{3,}\b", re.I)


# Stripped only for MATCHING keys (dedupe / applied-companies), so that
# "Nutanix" and "Nutanix Technologies India Pvt Ltd" resolve to one company.
# Deliberately excludes descriptive words like cars / group / trucks.
_GENERIC_DESCRIPTORS = ["technologies", "technology", "solutions", "systems",
                        "software", "labs", "lab", "digital"]


def company_key(name: str | None) -> str:
    s = normalize_company(name)
    changed = True
    while changed:
        changed = False
        for d in _GENERIC_DESCRIPTORS:
            if s.endswith(" " + d):
                s = s[: -(len(d) + 1)].strip()
                changed = True
    return s


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    s = title.strip().lower()
    s = _TITLE_PAREN.sub(" ", s)
    s = _TITLE_REQID.sub(" ", s)
    s = re.sub(r"[/|,]", " ", s)
    s = re.sub(r"[^\w\s+-]", " ", s)
    toks: list[str] = []
    for raw in s.split():
        t = raw.strip("-").strip()
        if not t:
            continue
        t = _TITLE_ABBREV.get(t, t)
        for part in t.split():
            if part in _TITLE_NOISE:
                continue
            toks.append(part)
    return " ".join(toks)


def title_similarity(a: str, b: str) -> float:
    ta, tb = set(normalize_title(a).split()), set(normalize_title(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def titles_match_exact(a: str, b: str) -> bool:
    """Order-insensitive: 'Senior Engineer - DevSecOps' == 'Sr. DevSecOps Engineer'."""
    ta, tb = sorted(normalize_title(a).split()), sorted(normalize_title(b).split())
    return bool(ta) and ta == tb


# ---------------------------------------------------------------------------
# posted-date parsing (never silently drops on a parse failure)
# ---------------------------------------------------------------------------

_REL = re.compile(
    r"(?:(\d+)\+?\s*)?(second|sec|minute|min|hour|hr|day|week|month)s?\s*(?:ago)?", re.I
)
_UNIT_H = {"second": 1 / 3600, "sec": 1 / 3600, "minute": 1 / 60, "min": 1 / 60,
           "hour": 1, "hr": 1, "day": 24, "week": 168, "month": 730}


def parse_posted(value: Any) -> tuple[datetime | None, str]:
    """-> (utc datetime | None, method). None means UNPARSED, not old."""
    if value is None or value == "":
        return None, "missing"
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e11:  # epoch millis
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc), "epoch"
        except (OSError, ValueError, OverflowError):
            return None, "bad-epoch"
    s = str(value).strip()
    if re.fullmatch(r"\d{10}|\d{13}", s):
        return parse_posted(int(s))
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)), "iso"
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc), "date"
        except ValueError:
            continue
    low = s.lower()
    if low in {"just now", "today", "few hours ago", "a few hours ago"}:
        return NOW(), "relative"
    if low == "yesterday":
        return NOW() - timedelta(days=1), "relative"
    m = _REL.search(low)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        return NOW() - timedelta(hours=n * _UNIT_H[m.group(2)]), "relative"
    return None, "unparsed"


# ---------------------------------------------------------------------------
# experience band
# ---------------------------------------------------------------------------

_RANGE = re.compile(
    r"(\d{1,2})\s*(?:\+)?\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:yrs?|years?)", re.I)
_FLOOR = re.compile(
    r"(?:(?:minimum|min\.?|at\s*least|over|more\s*than)\s*)?(\d{1,2})\s*\+\s*"
    r"(?:yrs?|years?)|(?:minimum|min\.?|at\s*least)\s*(?:of\s*)?(\d{1,2})\s*"
    r"(?:yrs?|years?)", re.I)


def parse_experience_band(text: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply ONLY to an explicit upper bound. Never infer a ceiling."""
    res: dict[str, Any] = {"stated": None, "floor": None, "ceiling": None,
                           "verdict": "KEEP", "reason": "no years stated"}
    if not text:
        return res
    m = _RANGE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo:
            lo, hi = hi, lo
        res.update(stated=m.group(0).strip(), floor=lo, ceiling=hi)
        if hi <= cfg["drop_ceiling_at_or_below"]:
            res.update(verdict="DROP", reason=f"ceiling {hi} is below my band")
        elif hi in cfg["underbanded_ceilings"]:
            res.update(verdict="KEEP", reason=f"borderline ceiling {hi}",
                       tag="UNDER_BANDED")
        else:
            res.update(reason=f"ceiling {hi} reaches my level")
        return res
    m = _FLOOR.search(text)
    if m:
        floor = int(m.group(1) or m.group(2))
        res.update(stated=m.group(0).strip(), floor=floor,
                   reason=f"floor {floor}+ with no ceiling")
    return res


# ---------------------------------------------------------------------------
# ledgers
# ---------------------------------------------------------------------------

class SeenLedger:
    """seen-jobs ledger for LinkedIn + Indeed. Naukri does this itself."""

    TRACKED = {"linkedin", "indeed"}

    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, dict[str, str]] = {}
        if os.path.exists(path):
            with open(path) as fh:
                self.data = json.load(fh)
        for s in self.TRACKED:
            self.data.setdefault(s, {})

    def is_seen(self, job: dict[str, Any]) -> bool:
        src = job["source"]
        if src not in self.TRACKED:
            return False
        return str(job.get("job_id") or job.get("url")) in self.data[src]

    def record(self, jobs: Iterable[dict[str, Any]]) -> None:
        stamp = NOW().isoformat()
        for j in jobs:
            if j["source"] in self.TRACKED:
                self.data[j["source"]][str(j.get("job_id") or j.get("url"))] = stamp

    def save(self) -> None:
        with open(self.path, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)


class AppliedFilter:
    """
    Company + title matching, with a defined algorithm and three outcomes:

      EXCLUDED  exact match on normalized(company) AND normalized(title)
      REVIEW    same company, title similarity >= threshold but not exact
                -> surfaced with a flag, NEVER auto-dropped
      PASS      everything else

    An entry with no title falls back to a full company-level block.
    jobId is stored for audit only and is never used for matching.
    """

    def __init__(self, path: str, cfg: dict[str, Any]):
        self.threshold = cfg["title_match"]["review_threshold"]
        self.entries: list[dict[str, Any]] = []
        if os.path.exists(path):
            with open(path) as fh:
                self.entries = json.load(fh)
        self.by_company: dict[str, list[dict[str, Any]]] = {}
        for e in self.entries:
            self.by_company.setdefault(company_key(e.get("company")), []).append(e)

    def check(self, company: str, title: str) -> dict[str, Any]:
        key = company_key(company)
        hits = self.by_company.get(key)
        if not hits:
            return {"verdict": "PASS"}
        for e in hits:
            if not e.get("title"):
                return {"verdict": "EXCLUDED", "matched": e,
                        "reason": "entry has no title -> company-level block"}
            if titles_match_exact(e["title"], title):
                return {"verdict": "EXCLUDED", "matched": e,
                        "reason": f"already applied: {e['title']}"}
        best, score = None, 0.0
        for e in hits:
            s = title_similarity(e.get("title", ""), title)
            if s > score:
                best, score = e, s
        if best and score >= self.threshold:
            return {"verdict": "REVIEW", "matched": best, "similarity": round(score, 2),
                    "reason": f"close to applied title '{best['title']}' — you decide"}
        return {"verdict": "PASS"}


# ---------------------------------------------------------------------------
# source adapters
# ---------------------------------------------------------------------------

def from_linkedin(it: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "linkedin",
        "job_id": it.get("jobId") or it.get("id") or it.get("jobUrl"),
        "title": it.get("jobTitle") or it.get("title") or "",
        "company": it.get("companyName") or it.get("company") or "",
        "location": it.get("location") or "",
        "url": it.get("jobUrl") or it.get("url") or "",
        "apply_url": it.get("applyUrl") or it.get("jobUrl") or it.get("url") or "",
        "apply_type": it.get("applyType"),
        "posted_raw": it.get("postedTime") or it.get("publishedAt") or it.get("postedAt"),
        "salary": it.get("salary"),
        "skills": it.get("matchedKeywords") or [],
        "description": it.get("jobDescription") or "",
        "linkedin_kms": it.get("keywordMatchScorePercentage"),
        "li_industry": it.get("companyIndustry"),
        "li_employees": it.get("companyEmployeeCount"),
        "raw_extra": {"dynamicFilterMatch": it.get("dynamicFilterMatch")},
    }


def from_naukri(it: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "naukri",
        "job_id": it.get("jobId") or it.get("id"),
        "title": it.get("title") or "",
        "company": it.get("company") or it.get("companyName") or "",
        "location": it.get("location") or "",
        "url": it.get("portalUrl") or it.get("url") or "",
        "apply_url": it.get("portalUrl") or it.get("url") or "",
        "apply_type": "NAUKRI",
        "posted_raw": it.get("createdDate") or it.get("postedDate"),
        "salary": it.get("salary"),
        "skills": it.get("skills") or [],
        "description": it.get("description") or "",
        "experience_raw": it.get("experienceText") or it.get("experience"),
        "naukri_company_type": str(it.get("companyType") or ""),
        "posted_by": it.get("postedBy"),
    }


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path through nested dicts. Returns None on any miss."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _pick(it: dict[str, Any], *paths: str) -> Any:
    """First non-empty value among dotted paths.

    Tolerates a field that is a bare scalar in one actor version and a dict in
    another: a dict hit falls through to its text/name/formatted/value key.
    """
    for path in paths:
        v = _dig(it, path)
        if isinstance(v, dict):
            for k in ("text", "name", "formatted", "value"):
                if v.get(k):
                    v = v[k]
                    break
            else:
                v = None
        if v not in (None, "", [], {}):
            return v
    return None


def _labels(v: Any) -> list[str]:
    """Normalise a skills/attributes field to a flat list of strings.

    kaix/indeed-scraper emits [{"key": ..., "label": ...}]; older shapes emit
    a bare list of strings.
    """
    if not v:
        return []
    if isinstance(v, dict):
        v = list(v.values())
    out: list[str] = []
    for x in v if isinstance(v, list) else [v]:
        if isinstance(x, dict):
            x = x.get("label") or x.get("name") or x.get("text")
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def from_indeed(it: dict[str, Any]) -> dict[str, Any]:
    """Adapter for kaix/indeed-scraper.

    That actor emits a nested shape (id / title.text / company.name /
    location.formatted / urls.indeed / urls.external / dates.posted /
    description.text). The flat spellings are kept as fallbacks so datasets
    from the older flat actor still parse.

    NOTE: searchMode "basic" returns no skills array, so an Indeed row cannot
    clear the 3-of-12 keyword-group gate on skills alone — it needs the
    description, which pass-1 does return.
    """
    external = _pick(it, "urls.external", "externalApplyLink", "applyUrl")
    url = _pick(it, "urls.indeed", "url", "jobUrl") or ""
    return {
        "source": "indeed",
        "job_id": _pick(it, "id", "jobkey", "jobKey") or url,
        "title": _pick(it, "title.text", "positionName", "title") or "",
        "company": _pick(it, "company.name", "company", "companyName") or "",
        "location": _pick(it, "location.formatted", "location", "formattedLocation") or "",
        "url": url,
        "apply_url": external or url,
        "apply_type": "EXTERNAL" if external else "INDEED",
        "posted_raw": _pick(it, "dates.posted", "postingDateParsed", "postedAt",
                            "date", "dates.postedRaw"),
        "salary": _pick(it, "salary.text", "salary", "salaryText"),
        "skills": _labels(it.get("skills")
                          or _dig(it, "classification.skills")
                          or _dig(it, "requirements.attributes")),
        "description": _pick(it, "description.text", "description",
                             "descriptionText") or "",
    }


ADAPTERS = {"linkedin": from_linkedin, "naukri": from_naukri, "indeed": from_indeed}


# ---------------------------------------------------------------------------
# mechanical drops
# ---------------------------------------------------------------------------

# Title filters come from the profile (filters.title_drop / out_of_domain /
# dev_hint). Empty pattern = never matches.
_NEVER = re.compile(r"(?!x)x")
_MGMT_TITLE = _NEVER
_OUT_OF_DOMAIN = _NEVER
TITLE_DROP_LABEL = "management/presales title"
OUT_OF_DOMAIN_LABEL = "out-of-domain title"
DEV_HINT_LABEL = "POSSIBLE-APP-DEV — judge domain, not title"
_CONTRACT = re.compile(r"\b(contract|c2h|contract to hire|internship|intern|"
                       r"trainee|fresher|apprentice)\b", re.I)
_SPAM_SHAPE = re.compile(
    r"\b(client requirement|immediate joiner|multiple positions|bench|"
    r"deployment to client|walk-?in|payroll of|c2c|hiring for our client)\b", re.I)

# NOT a drop: a hint for the model, which decides domain from the JD.
_APPDEV_HINT = _NEVER
_PROFILE: dict[str, Any] | None = None


def install_profile(prof: dict[str, Any]) -> None:
    """Load keyword groups, title filters and paths from a profile dict."""
    global KEYWORD_GROUPS, _KW_COMPILED, _MGMT_TITLE, _OUT_OF_DOMAIN, _APPDEV_HINT
    global TITLE_DROP_LABEL, OUT_OF_DOMAIN_LABEL, DEV_HINT_LABEL, _PROFILE, PDIR
    _PROFILE = prof
    PDIR = prof.get("_dir") or HERE
    KEYWORD_GROUPS = {g: [(t, bool(st)) for t, st in terms]
                      for g, terms in (prof.get("keyword_groups") or {}).items()}
    _KW_COMPILED = {g: [(_kw_regex(t, st), t) for t, st in terms]
                    for g, terms in KEYWORD_GROUPS.items()}
    f = prof.get("filters") or {}

    def rx(key: str) -> re.Pattern[str]:
        return re.compile(f[key], re.I) if f.get(key) else _NEVER

    _MGMT_TITLE, _OUT_OF_DOMAIN, _APPDEV_HINT = (
        rx("title_drop"), rx("out_of_domain"), rx("dev_hint"))
    TITLE_DROP_LABEL = f.get("title_drop_label") or "management/presales title"
    OUT_OF_DOMAIN_LABEL = f.get("out_of_domain_label") or "out-of-domain title"
    DEV_HINT_LABEL = f.get("dev_hint_label") or "POSSIBLE-APP-DEV — judge domain, not title"


def mechanical_drop(job: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    t, blob = job["title"], f"{job['title']} {job.get('location','')}"
    if _MGMT_TITLE.search(t):
        return TITLE_DROP_LABEL
    if _OUT_OF_DOMAIN.search(t):
        return OUT_OF_DOMAIN_LABEL
    if _CONTRACT.search(blob):
        return "contract/intern/fresher"
    loc = (job.get("location") or "").lower()
    if loc and not any(a in loc for a in cfg["allowed_locations"]):
        return f"location out of scope: {job['location']}"
    if job["source"] == "linkedin":
        kms = job.get("linkedin_kms")
        if isinstance(kms, (int, float)) and kms < cfg["linkedin_min_match_score"]:
            return f"linkedin match score {kms} < {cfg['linkedin_min_match_score']}"
    else:
        n = len(matched_groups(job["title"], job.get("skills"), job.get("description")))
        if n < cfg["min_keyword_groups"]:
            return f"only {n} of {len(KEYWORD_GROUPS)} keyword groups"
    return None


def blocked_company(job: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """EXACT normalized match. Never substring — Volvo Group stays in scope."""
    return company_key(job["company"]) in {
        company_key(c) for c in cfg["blocked_companies"]}


# ---------------------------------------------------------------------------
# dedupe / tier / scoring
# ---------------------------------------------------------------------------

_APPLY_RANK = {"EXTERNAL": 3, "NAUKRI": 2, "INDEED": 2, "EASY_APPLY": 1, None: 0}


def dedupe(jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for j in jobs:
        buckets.setdefault((company_key(j["company"]),
                            normalize_title(j["title"])), []).append(j)
    kept, removed = [], 0
    for group in buckets.values():
        group.sort(key=lambda j: _APPLY_RANK.get(j.get("apply_type"), 0), reverse=True)
        winner = group[0]
        winner["also_seen_on"] = sorted({g["source"] for g in group[1:]})
        removed += len(group) - 1
        kept.append(winner)
    return kept, removed


_NAUKRI_TIER = {"213": "FOREIGN-MNC", "62": "FUNDED-PRODUCT/STARTUP",
                "217": "INDIAN-MNC/MNC", "60": "INDIAN-MNC/MNC"}
TIER_ORDER = {"FOREIGN-MNC": 4, "FUNDED-PRODUCT/STARTUP": 3,
              "INDIAN-MNC/MNC": 2, "SERVICES": 1, "UNKNOWN": 0}


def company_tier(job: dict[str, Any]) -> str:
    ct = job.get("naukri_company_type")
    if ct and ct in _NAUKRI_TIER:
        return _NAUKRI_TIER[ct]
    ind = (job.get("li_industry") or "").lower()
    emp = job.get("li_employees") or 0
    if "it services" in ind or "consulting" in ind or "staffing" in ind:
        return "SERVICES"          # tiebreak only — never a filter
    if ind:
        if isinstance(emp, (int, float)) and emp >= 5000:
            return "FOREIGN-MNC"
        if isinstance(emp, (int, float)) and 0 < emp < 5000:
            return "FUNDED-PRODUCT/STARTUP"
    return "UNKNOWN"


def stack_score(job: dict[str, Any]) -> dict[str, Any]:
    """The deterministic 35 points. ROLE SHAPE and DIFFERENTIATOR stay with the model."""
    groups = matched_groups(job["title"], job.get("skills"), job.get("description"))
    if job["source"] == "linkedin" and isinstance(job.get("linkedin_kms"), (int, float)):
        return {"stack_score": round(job["linkedin_kms"] * 0.35),
                "stack_method": "linkedin_kms x0.35",
                "matched_groups": groups,
                "group_count": len(groups),
                "unmatched_groups": sorted(set(KEYWORD_GROUPS) - set(groups))}
    return {"stack_score": min(35, round(len(groups) * 2.9)),
            "stack_method": "group_count x2.9 (cap 35)",
            "matched_groups": groups,
            "group_count": len(groups),
            "unmatched_groups": sorted(set(KEYWORD_GROUPS) - set(groups))}


# ---------------------------------------------------------------------------
# resume gap analysis — the direction the STACK score does not measure
#
# STACK asks "how much of MY stack does this JD mention".
# This asks the reverse: "what does this JD require that I do not have".
# ---------------------------------------------------------------------------

_HARD_REQ = re.compile(
    r"[^.!?\n]*\b(must have|must possess|required|requirement|mandatory|"
    r"essential|strong (?:hands-on )?experience (?:in|with)|expert(?:ise)? in|"
    r"deep (?:experience|knowledge|expertise)|proven experience (?:in|with)|"
    r"\d+\+? years? of (?:hands-on )?experience (?:in|with))\b[^.!?\n]*", re.I)


def load_skills(path: str | None = None) -> dict[str, Any]:
    p = path or os.path.join(PDIR, "skills.json")
    if not os.path.exists(p):
        return {"have": [], "vocabulary": [], "shallow": {}}
    with open(p) as fh:
        return json.load(fh)


def analyze_gaps(description: str, skills: dict[str, Any]) -> dict[str, Any]:
    """-> jd_requirements / covered / gaps / hard_requirement_gaps / shallow_hits"""
    if not description:
        return {"jd_requirements": [], "covered": [], "gaps": [],
                "hard_requirement_gaps": [], "shallow_hits": []}
    have = {h.lower() for h in skills.get("have", [])}
    shallow = {k.lower() for k in skills.get("shallow", {}) if not k.startswith("_")}

    found = [term for term in skills.get("vocabulary", [])
             if _kw_regex(term, term == "Go").search(description)]
    covered = sorted({t for t in found if t.lower() in have})
    # shallow terms are neither covered nor a gap — they get their own flag
    gaps = sorted({t for t in found if t.lower() not in have and t.lower() not in shallow})

    # a gap term inside a sentence that states a hard requirement is what the
    # Cap-70 rule is actually looking for
    hard: list[dict[str, str]] = []
    for sent in _HARD_REQ.findall(description) and \
            [m.group(0).strip() for m in _HARD_REQ.finditer(description)]:
        for g in gaps:
            if _kw_regex(g, False).search(sent):
                hard.append({"term": g, "phrase": " ".join(sent.split())[:160]})
    seen_terms, hard_uniq = set(), []
    for h in hard:
        if h["term"] not in seen_terms:
            seen_terms.add(h["term"])
            hard_uniq.append(h)

    return {"jd_requirements": sorted(set(found)),
            "covered": covered,
            "gaps": gaps,
            "hard_requirement_gaps": hard_uniq,
            "shallow_hits": sorted({t for t in found if t.lower() in shallow}),
            "coverage_pct": round(100 * len(covered) / len(found)) if found else None}


# ---------------------------------------------------------------------------
# liveness — a failed fetch is UNVERIFIED, never a silent drop
# ---------------------------------------------------------------------------

_EXPIRED = re.compile(
    r"no longer accepting applications|this job is no longer available|"
    r"job has expired|position has been filled|posting is closed", re.I)


def check_liveness(url: str, cfg: dict[str, Any]) -> dict[str, Any]:
    lv = cfg["liveness"]
    if not url:
        return {"liveness": "UNVERIFIED", "http": None, "note": "no url"}
    req = urllib.request.Request(url, headers={"User-Agent": lv["user_agent"],
                                               "Accept-Language": "en-IN,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=lv["timeout_s"]) as resp:
            body = resp.read(200_000).decode("utf-8", "ignore")
            if _EXPIRED.search(body):
                return {"liveness": "DEAD", "http": resp.status, "note": "expired marker"}
            return {"liveness": "LIVE", "http": resp.status, "final_url": resp.url}
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return {"liveness": "DEAD", "http": e.code}
        # 403 / 429 / 999 = bot wall or throttle, NOT an expired post
        return {"liveness": "UNVERIFIED", "http": e.code, "note": "blocked or throttled"}
    except Exception as e:  # noqa: BLE001 - network is best-effort by design
        return {"liveness": "UNVERIFIED", "http": None, "note": type(e).__name__}


# ---------------------------------------------------------------------------
# stage A
# ---------------------------------------------------------------------------

def stage_a(inputs: dict[str, str], cfg: dict[str, Any],
            do_network: bool = True) -> dict[str, Any]:
    cov: dict[str, Any] = {"raw": {}, "dropped": {}, "unique_after_dedupe": {}}
    drops: list[dict[str, Any]] = []
    below_band: list[dict[str, Any]] = []

    def drop(job, reason, bucket="mechanical"):
        cov["dropped"][reason] = cov["dropped"].get(reason, 0) + 1
        drops.append({"title": job["title"], "company": job["company"],
                      "source": job["source"], "reason": reason, "bucket": bucket})

    jobs: list[dict[str, Any]] = []
    for source, path in inputs.items():
        if not path:
            continue
        with open(path) as fh:
            items = json.load(fh)
        items = items.get("items", items) if isinstance(items, dict) else items
        cov["raw"][source] = len(items)
        jobs.extend(ADAPTERS[source](it) for it in items)

    # 1. freshness — exact 48h, enforced here because server-side buckets
    #    (Naukri's 1/3/7-day options) do not align to 48 hours.
    cutoff = NOW() - timedelta(hours=cfg["freshness_hours"])
    fresh = []
    for j in jobs:
        dt, how = parse_posted(j.get("posted_raw"))
        j["posted_at"] = dt.isoformat() if dt else None
        j["posted_parse"] = how
        if dt is None:
            j.setdefault("flags", []).append("POSTED_DATE_UNPARSED")
            fresh.append(j)                      # keep + flag, never silent-drop
        elif dt < cutoff:
            drop(j, f"older than {cfg['freshness_hours']}h", "freshness")
        else:
            fresh.append(j)
    jobs = fresh

    # 2. permanent company block (current employer), exact normalized match
    kept = []
    for j in jobs:
        if blocked_company(j, cfg):
            drop(j, "blocked company (current employer)", "policy")
        else:
            kept.append(j)
    jobs = kept

    # 3. seen-jobs
    ledger = SeenLedger(os.path.join(PDIR, cfg["paths"]["seen_jobs"]))
    unseen = []
    for j in jobs:
        if ledger.is_seen(j):
            drop(j, "already seen in a previous run", "seen")
        else:
            unseen.append(j)
    jobs = unseen

    # 4. applied-companies — post-scrape, all three sources
    applied = AppliedFilter(os.path.join(PDIR, cfg["paths"]["applied_companies"]), cfg)
    kept = []
    applied_removed = 0
    for j in jobs:
        v = applied.check(j["company"], j["title"])
        if v["verdict"] == "EXCLUDED":
            applied_removed += 1
            drop(j, f"applied-companies: {v['reason']}", "applied")
        else:
            if v["verdict"] == "REVIEW":
                j.setdefault("flags", []).append(
                    f"APPLIED-TITLE-REVIEW ({v['similarity']}): {v['reason']}")
            kept.append(j)
    jobs = kept
    cov["applied_companies_removed"] = applied_removed

    # 5. mechanical drops
    kept = []
    for j in jobs:
        r = mechanical_drop(j, cfg)
        if r:
            drop(j, r)
        else:
            kept.append(j)
    jobs = kept

    # 6. shape / hint flags (flags only — the model decides)
    for j in jobs:
        blob = f"{j['title']} {j.get('description','')}"
        if _SPAM_SHAPE.search(blob):
            j.setdefault("flags", []).append("REQ-SHAPE-SPAM-SIGNAL (cap 60)")
        if _APPDEV_HINT.search(blob):
            j.setdefault("flags", []).append(DEV_HINT_LABEL)
        if j["source"] == "linkedin" and j.get("apply_type") == "EXTERNAL":
            j.setdefault("flags", []).append(
                "LINKEDIN-EXTERNAL — verify experience range at destination")

    # 7. experience band on whatever text exists pre-detail
    for j in jobs:
        band = parse_experience_band(
            " ".join(filter(None, [j.get("experience_raw"), j.get("description", "")[:2000]])),
            cfg)
        j["experience_band"] = band
        if band.get("tag"):
            j.setdefault("flags", []).append(band["tag"])
        if band["verdict"] == "DROP":
            below_band.append({"title": j["title"], "company": j["company"],
                               "stated": band["stated"], "reason": band["reason"]})
    jobs = [j for j in jobs if j["experience_band"]["verdict"] != "DROP"]
    for b in below_band:
        cov["dropped"]["below my band"] = cov["dropped"].get("below my band", 0) + 1

    # 8. dedupe
    per_source_before = {}
    for j in jobs:
        per_source_before[j["source"]] = per_source_before.get(j["source"], 0) + 1
    jobs, dupes = dedupe(jobs)
    cov["dedupe_removed"] = dupes
    for j in jobs:
        cov["unique_after_dedupe"][j["source"]] = \
            cov["unique_after_dedupe"].get(j["source"], 0) + 1

    # 9. score + tier + rank
    for j in jobs:
        j.update(stack_score(j))
        j["company_tier"] = company_tier(j)
    jobs.sort(key=lambda j: (j["stack_score"], TIER_ORDER[j["company_tier"]]), reverse=True)

    n = cfg["primary_set_size"]
    for i, j in enumerate(jobs):
        j["triage_priority"] = "primary" if i < n else "secondary"

    # 10. liveness on the primary set
    if do_network and cfg["liveness"]["enabled"]:
        for j in jobs:
            if j["triage_priority"] != "primary":
                continue
            j.update(check_liveness(j["apply_url"], cfg))
            if j.get("liveness") == "UNVERIFIED":
                j.setdefault("flags", []).append("APPLY-LINK-UNVERIFIED — check manually")
            time.sleep(cfg["liveness"]["sleep_between_s"])
        dead = [j for j in jobs if j.get("liveness") == "DEAD"]
        for j in dead:
            drop(j, "apply link dead (404/410/expired)", "liveness")
        jobs = [j for j in jobs if j.get("liveness") != "DEAD"]

    ledger.record(jobs)
    ledger.save()

    cov["survivors"] = len(jobs)
    cov["primary"] = sum(1 for j in jobs if j["triage_priority"] == "primary")
    cov["secondary"] = sum(1 for j in jobs if j["triage_priority"] == "secondary")
    cov["biggest_noise_source"] = max(cov["dropped"].items(),
                                      key=lambda kv: kv[1], default=("none", 0))[0]
    cov["below_my_band"] = below_band
    return {"jobs": jobs, "coverage": cov, "drops": drops,
            "generated_at": NOW().isoformat(),
            "freshness_hours": cfg["freshness_hours"]}


# ---------------------------------------------------------------------------
# stage B — after descriptions are fetched for the primary set
# ---------------------------------------------------------------------------

def stage_b(triage: dict[str, Any], details: dict[str, str],
            cfg: dict[str, Any]) -> dict[str, Any]:
    """details: {job_id: description}. Recomputes STACK + band + gaps on real text."""
    skills = load_skills()
    for j in triage["jobs"]:
        d = details.get(str(j.get("job_id"))) or details.get(str(j.get("url")))
        if not d:
            continue
        j["description"] = d
        j.update(stack_score(j))
        j["resume_fit"] = analyze_gaps(d, skills)
        if j["resume_fit"]["hard_requirement_gaps"]:
            terms = ", ".join(h["term"] for h in j["resume_fit"]["hard_requirement_gaps"])
            j.setdefault("flags", []).append(f"HARD-REQ-GAP ({terms}) — consider cap 70")
        if j["resume_fit"]["shallow_hits"]:
            j.setdefault("flags", []).append(
                "SHALLOW-SKILL-DEMAND: " + ", ".join(j["resume_fit"]["shallow_hits"]))
        band = parse_experience_band(d, cfg)
        j["experience_band"] = band
        j["flags"] = [f for f in j.get("flags", []) if f != "UNDER_BANDED"]
        if band.get("tag"):
            j["flags"].append(band["tag"])
        if band["verdict"] == "DROP":
            j["flags"].append(f"BELOW-MY-BAND ({band['stated']}) — drop")
    triage["jobs"] = [j for j in triage["jobs"]
                      if j["experience_band"]["verdict"] != "DROP"]
    triage["jobs"].sort(key=lambda j: (j["stack_score"],
                                       TIER_ORDER[j["company_tier"]]), reverse=True)
    return triage


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    """Profile-agnostic: runs against the fictional examples/demo profile."""
    install_profile(jsprofile.load_profile(jsprofile.DEMO))
    cfg = load_config()
    fails: list[str] = []

    def ck(cond, msg):
        if not cond:
            fails.append(msg)

    # the exclusion that must never over-match
    ck(blocked_company({"company": "Example Motors India Private Limited"}, cfg),
       "demo current employer should be blocked")
    ck(not blocked_company({"company": "Example Motors Group Trucks"}, cfg),
       "a different company sharing a prefix must stay in scope")
    ck(normalize_company("Endava Pvt Ltd") == "endava", "legal suffix strip")
    ck(normalize_company("Volvo Cars India Private Limited") == "volvo cars",
       "geo + legal suffix strip must land on volvo cars")
    ck(normalize_company("Volvo Cars") != normalize_company("Volvo Group"),
       "normalization must not collapse different companies")
    ck(company_key("Nutanix Technologies India Pvt Ltd") == company_key("Nutanix"),
       "generic descriptor strip should unify Nutanix entities")
    ck(company_key("Volvo Group Trucks Technology") != company_key("Volvo Cars"),
       "descriptor strip must never collapse Volvo Group into Volvo Cars")
    ck(titles_match_exact("Senior Engineer - DevSecOps", "Sr. DevSecOps Engineer"),
       "title match must be order-insensitive")

    # title matching, exactly the v2 examples
    ck(titles_match_exact("Senior DevSecOps Engineer", "Sr. DevSecOps Engineer"),
       "Sr. == Senior should match")
    ck(not titles_match_exact("Senior DevSecOps Engineer", "Senior Platform Engineer"),
       "different domain titles must not match")
    ck(title_similarity("Senior DevSecOps Engineer", "Senior Platform Engineer") < 0.75,
       "different-domain similarity should stay under review threshold")

    # applied filter three-way outcome
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.json")
        json.dump([{"company": "Omnissa", "title": "Senior Engineer - DevSecOps",
                    "jobId": "x1"},
                   {"company": "Acme Labs"}], open(p, "w"))
        af = AppliedFilter(p, cfg)
        ck(af.check("Omnissa", "Sr Engineer DevSecOps")["verdict"] == "EXCLUDED",
           "same company+title should be EXCLUDED")
        ck(af.check("Omnissa", "Staff Platform Engineer")["verdict"] == "PASS",
           "different title at applied company must surface")
        ck(af.check("Acme Labs", "Anything")["verdict"] == "EXCLUDED",
           "titleless entry = company block")

    # experience band, including the v2 contradiction resolution
    ck(parse_experience_band("5-8 years of experience", cfg)["verdict"] == "DROP",
       "5-8 should drop")
    ck(parse_experience_band("8+ years required", cfg)["verdict"] == "KEEP",
       "floor with no ceiling should keep")
    ck(parse_experience_band("6-12 years", cfg)["verdict"] == "KEEP",
       "ceiling 12 should keep")
    ck(parse_experience_band("7-10 years", cfg).get("tag") == "UNDER_BANDED",
       "ceiling 10 should be UNDER_BANDED, not dropped")
    ck(parse_experience_band("Senior engineer wanted", cfg)["ceiling"] is None,
       "must never infer a ceiling")

    # dates
    global NOW
    real_now, fixed = NOW, datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    NOW = lambda: fixed  # noqa: E731
    ck(parse_posted("6 hours ago")[0] == fixed - timedelta(hours=6), "relative hours")
    ck(parse_posted("2026-08-17T09:00:00Z")[0].day == 17, "iso parse")
    ck(parse_posted(1755432000)[1] == "epoch", "epoch parse")
    ck(parse_posted("sometime last sprint")[0] is None, "unparseable -> None, not old")
    NOW = real_now

    # keyword groups (demo profile)
    g = matched_groups("Platform Engineer", ["Kubernetes", "ArgoCD", "Terraform"], "")
    ck(len(g) == 4 and "PlatformEng" in g, f"expected 4 groups incl PlatformEng, got {g}")
    ck("Go" not in matched_groups("go-to-market strategy ongoing"),
       "'Go' must not match go-to-market/ongoing")
    ck("Go" in matched_groups("Strong Go and Python skills"), "'Go' should match")
    ck(mechanical_drop({"title": "Engineering Manager, Platform", "source": "linkedin",
                        "location": "Bengaluru"}, cfg) == TITLE_DROP_LABEL,
       "profile title_drop regex should drop")
    ck(mechanical_drop({"title": "SAP Basis Consultant", "source": "linkedin",
                        "location": "Bengaluru"}, cfg) == OUT_OF_DOMAIN_LABEL,
       "profile out_of_domain regex should drop")
    ck(mechanical_drop({"title": "Senior Platform Engineer", "source": "linkedin",
                        "location": "Pune, India"}, cfg) is None,
       "in-scope title in India stays")
    ck(mechanical_drop({"title": "Senior Platform Engineer", "source": "linkedin",
                        "location": "Berlin, Germany"}, cfg) is not None,
       "location outside allowed_locations drops")
    ck(not jsprofile.validate(_PROFILE), f"demo profile invalid: {jsprofile.validate(_PROFILE)}")

    # dedupe keeps the most direct apply link
    kept, removed = dedupe([
        {"source": "linkedin", "company": "Acme", "title": "Platform Engineer",
         "apply_type": "EASY_APPLY"},
        {"source": "naukri", "company": "Acme Pvt Ltd", "title": "Platform Engineer",
         "apply_type": "EXTERNAL"}])
    ck(removed == 1 and kept[0]["source"] == "naukri", "dedupe should keep EXTERNAL")

    # tier is a tiebreak, never a filter
    ck(company_tier({"li_industry": "IT Services and IT Consulting"}) == "SERVICES",
       "services tier tag")
    ck(TIER_ORDER["FOREIGN-MNC"] > TIER_ORDER["SERVICES"], "tier order")

    # resume gap analysis
    sk = {"have": ["kubernetes", "go", "terraform"],
          "shallow": {"aws": "breadth gap"},
          "vocabulary": ["kubernetes", "go", "terraform", "java", "aws", "kafka"]}
    r = analyze_gaps("Must have strong experience in Java and Kafka. "
                     "Kubernetes and Terraform knowledge helpful. AWS exposure.", sk)
    ck(set(r["gaps"]) == {"java", "kafka"}, f"gaps should be java+kafka, got {r['gaps']}")
    ck("kubernetes" in r["covered"], "kubernetes should be covered")
    ck({h["term"] for h in r["hard_requirement_gaps"]} == {"java", "kafka"},
       f"hard-req gaps wrong: {r['hard_requirement_gaps']}")
    ck(r["shallow_hits"] == ["aws"], "aws should flag as shallow, not gap")
    ck(analyze_gaps("Kubernetes and Go role", sk)["hard_requirement_gaps"] == [],
       "no hard requirement -> no cap signal")

    # indeed adapter must read kaix/indeed-scraper's NESTED shape, not just the
    # old flat one. Getting this wrong makes Indeed contribute zero survivors
    # silently, which is exactly what happened on the 2026-08-18 run.
    nested = from_indeed({
        "id": "abc123",
        "title": {"text": "Platform Engineer"},
        "company": {"name": "Acme"},
        "location": {"formatted": "Bengaluru, Karnataka"},
        "urls": {"indeed": "https://in.indeed.com/viewjob?jk=abc123",
                 "external": "https://acme.com/careers/1"},
        "dates": {"posted": "2026-09-02T10:00:00Z"},
        "description": {"text": "Build Kubernetes operators."},
    })
    ck(nested["job_id"] == "abc123" and nested["title"] == "Platform Engineer"
       and nested["company"] == "Acme" and nested["location"].startswith("Bengaluru")
       and nested["apply_type"] == "EXTERNAL"
       and nested["apply_url"] == "https://acme.com/careers/1"
       and nested["posted_raw"] == "2026-09-02T10:00:00Z"
       and nested["description"] == "Build Kubernetes operators.",
       f"indeed nested adapter wrong: {nested}")
    flat = from_indeed({"jobkey": "z9", "positionName": "SRE", "company": "Beta",
                        "location": "Remote", "url": "https://x/y",
                        "postingDateParsed": "2026-09-01", "description": "text"})
    ck(flat["job_id"] == "z9" and flat["title"] == "SRE" and flat["company"] == "Beta"
       and flat["apply_type"] == "INDEED", f"indeed flat fallback broke: {flat}")

    # every term the candidate HAS must be in vocabulary, or it can never be
    # detected and therefore can never reach resume_fit.covered. Checked for the
    # demo profile and, when one is selected, the active profile too.
    _active = jsprofile.profile_dir(required=False)
    if _active:
        _ap = jsprofile.load_profile(_active)
        for e in jsprofile.validate(_ap):
            fails.append(f"active profile {_active.name}: {e}")
        install_profile(_ap)
    _sk = load_skills()
    if _sk.get("have"):
        _missing = [t for t in _sk["have"]
                    if t.lower() not in {v.lower() for v in _sk["vocabulary"]}]
        ck(not _missing, f"skills.json: have-terms absent from vocabulary: {_missing}")

    if fails:
        print("FAIL\n  - " + "\n  - ".join(fails))
        return 1
    print("all self-tests passed")
    return 0


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("stage-a", help="scrape exports -> triage.json")
    a.add_argument("--linkedin"), a.add_argument("--naukri"), a.add_argument("--indeed")
    a.add_argument("--out", default="triage.json")
    a.add_argument("--no-network", action="store_true", help="skip liveness checks")
    a.add_argument("--config")

    b = sub.add_parser("stage-b", help="triage.json + descriptions -> scored.json")
    b.add_argument("--triage", required=True)
    b.add_argument("--details", required=True, help='{"jobId": "description", ...}')
    b.add_argument("--out", default="scored.json")
    b.add_argument("--config")

    sub.add_parser("self-test")
    ap.add_argument("--profile", help="profile name or path (default: JOBSEARCH_PROFILE)")
    args = ap.parse_args()
    jsprofile.select(args.profile)

    if args.cmd == "self-test":
        return self_test()

    install_profile(jsprofile.load_profile(jsprofile.profile_dir()))
    cfg = load_config(getattr(args, "config", None))

    if args.cmd == "stage-a":
        res = stage_a({"linkedin": args.linkedin, "naukri": args.naukri,
                       "indeed": args.indeed}, cfg, do_network=not args.no_network)
        with open(args.out, "w") as fh:
            json.dump(res, fh, indent=2)
        c = res["coverage"]
        print(f"raw={c['raw']} survivors={c['survivors']} "
              f"(primary={c['primary']}, secondary={c['secondary']}) "
              f"applied-filtered={c['applied_companies_removed']} "
              f"deduped={c['dedupe_removed']} biggest-noise={c['biggest_noise_source']!r}")
        print(f"wrote {args.out}")
        return 0

    with open(args.triage) as fh:
        triage = json.load(fh)
    with open(args.details) as fh:
        details = json.load(fh)
    out = stage_b(triage, details, cfg)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote {args.out} ({len(out['jobs'])} jobs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
