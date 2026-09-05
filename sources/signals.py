"""Tier 2: postings harvested by Claude from X, LinkedIn, blogs and newsletters.

Those sources need judgement and cannot run unattended in CI, so Claude writes
normalised JSON into signals/inbox/*.json and this adapter folds it into the
same pipeline as everything else.
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import Posting, SourceResult

INBOX = Path(__file__).resolve().parent.parent / "signals" / "inbox"

REQUIRED = ("title", "company", "url")


def fetch() -> SourceResult:
    res = SourceResult(name="signals")
    if not INBOX.exists():
        res.detail = "no inbox yet"
        return res
    files = sorted(INBOX.glob("*.json"))
    bad = 0
    for f in files:
        try:
            payload = json.loads(f.read_text())
        except json.JSONDecodeError:
            bad += 1
            continue
        for row in payload if isinstance(payload, list) else payload.get("postings", []):
            if not all(row.get(k) for k in REQUIRED):
                bad += 1
                continue
            res.postings.append(Posting(
                title=row["title"],
                company=row["company"],
                url=row["url"],
                location=row.get("location", ""),
                description=row.get("description", ""),
                source="signal:" + row.get("via", "web"),
                company_tags=row.get("tags") or ["startup"],
                posted_at=row.get("posted_at", ""),
                remote=bool(row.get("remote")),
            ))
    res.detail = f"{len(files)} file(s), {res.count} postings" + (f", {bad} skipped" if bad else "")
    return res
