"""Grow config/companies.yml from ATS links seen in aggregator sources.

Without this the company list decays: startups appear, get funded, and are never
added by hand. HN comments and VC boards are full of greenhouse/ashby/lever URLs,
so every run harvests them and verifies the board is real before adding it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sources.base import http  # noqa: E402

CONFIG = ROOT / "config" / "companies.yml"

PATTERNS = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([a-z0-9][a-z0-9_-]{1,40})", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([a-z0-9][a-z0-9_.-]{1,40})", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([a-z0-9][a-z0-9_-]{1,40})", re.I),
}
PROBE = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
}
SKIP = {"embed", "boards", "job-boards", "www", "api", "static", "assets"}
MAX_NEW_PER_RUN = 25


def _count(ats: str, slug: str) -> int:
    try:
        d = http(PROBE[ats].format(s=slug), expect_json=True, retries=1, timeout=20)
    except Exception:  # noqa: BLE001
        return -1
    if ats == "lever":
        return len(d) if isinstance(d, list) else -1
    return len(d.get("jobs") or [])


def discover(postings: list) -> list[dict]:
    cfg = yaml.safe_load(CONFIG.read_text())
    known = {(c["ats"], c["slug"].lower()) for c in cfg["companies"]}
    blob = "\n".join(f"{p.url}\n{p.description[:2000]}" for p in postings)

    found: list[tuple[str, str]] = []
    for ats, pat in PATTERNS.items():
        for m in pat.finditer(blob):
            slug = m.group(1).lower().rstrip(".")
            if slug in SKIP or (ats, slug) in known or (ats, slug) in found:
                continue
            found.append((ats, slug))

    added = []
    for ats, slug in found:
        if len(added) >= MAX_NEW_PER_RUN:
            break
        if _count(ats, slug) > 0:
            entry = {"name": re.sub(r"[-_]+", " ", slug).title(),
                     "ats": ats, "slug": slug, "tags": ["startup"]}
            cfg["companies"].append(entry)
            added.append(entry)

    if added:
        lines = CONFIG.read_text().rstrip("\n").splitlines()
        for e in added:
            lines.append(f'  - {{name: "{e["name"]}", ats: {e["ats"]}, slug: "{e["slug"]}", tags: [startup]}}  # auto-discovered')
        CONFIG.write_text("\n".join(lines) + "\n")
    return added
