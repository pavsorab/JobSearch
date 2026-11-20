#!/usr/bin/env python3
"""jsprofile: shared .env + profile loader for jobpipe, jptools and apify_fetch.

A profile is a folder holding one person's data:

    profiles/<name>/profile.json      search, filters, keyword groups, tunables
    profiles/<name>/candidate.md      who the agent scores for
    profiles/<name>/skills.json       machine-readable skills + vocabulary
    profiles/<name>/master-cv.tex     Master CV (see templates/master-cv-template.tex)
    profiles/<name>/state/            seen-jobs, applied-companies, overleaf mapping
    profiles/<name>/runs/ results/ output/

The active profile comes from --profile, else JOBSEARCH_PROFILE (environment or
.env). A value with a slash is a path relative to the repo (e.g. examples/demo).

Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEMO = ROOT / "examples" / "demo"


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env. Real environment variables win."""
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if k and v:
            os.environ.setdefault(k, v)


def select(name: str | None) -> None:
    """Apply a --profile flag before anything reads the environment."""
    if name:
        os.environ["JOBSEARCH_PROFILE"] = name


def profile_dir(required: bool = True) -> Path | None:
    load_env()
    name = os.environ.get("JOBSEARCH_PROFILE", "").strip()
    if not name:
        if required:
            raise SystemExit("No profile selected. Set JOBSEARCH_PROFILE=<name> in .env "
                             "(see README) or pass --profile <name>.")
        return None
    p = Path(name)
    if not p.is_absolute():
        p = ROOT / p if len(p.parts) > 1 else ROOT / "profiles" / name
    if not (p / "profile.json").exists():
        if required:
            raise SystemExit(f"Profile not found: {p}/profile.json. Create one with "
                             f"`python3 jptools.py new-profile {p.name}`.")
        return None
    return p


def load_profile(pdir: Path) -> dict[str, Any]:
    with open(pdir / "profile.json") as fh:
        prof = json.load(fh)
    prof["_dir"] = str(pdir)
    return prof


def display_name(prof: dict[str, Any]) -> str:
    return (prof.get("name") or Path(prof.get("_dir", "profile")).name).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "profile"


def validate(prof: dict[str, Any]) -> list[str]:
    """Problems that would make a run silently wrong. Empty list = fine."""
    errs: list[str] = []
    if not prof.get("name") or prof.get("name") == "Your Name":
        errs.append("profile.json: set `name` (must match the Master CV footer)")
    if "Your Current Employer" in ((prof.get("config") or {}).get("blocked_companies") or []):
        errs.append("profile.json: replace the `Your Current Employer` placeholder")
    groups = prof.get("keyword_groups") or {}
    if len(groups) < 3:
        errs.append("profile.json: need at least 3 keyword_groups")
    for g, terms in groups.items():
        if not terms:
            errs.append(f"keyword group {g!r} is empty")
        for t in terms:
            if not (isinstance(t, list) and len(t) == 2 and isinstance(t[0], str)
                    and isinstance(t[1], bool)):
                errs.append(f"keyword group {g!r}: entries must be [\"term\", true|false]")
                break
    for k in ("title_drop", "out_of_domain", "dev_hint"):
        pat = (prof.get("filters") or {}).get(k)
        if pat:
            try:
                re.compile(pat)
            except re.error as e:
                errs.append(f"filters.{k} is not a valid regex: {e}")
    s = prof.get("search") or {}
    if not (s.get("boolean_queries") or s.get("linkedin_keywords")):
        errs.append("profile.json: search has no queries")
    return errs
