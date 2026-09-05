"""Hacker News "Ask HN: Who is hiring?" -- the best free feed of startups that
never post to a job board. ~238 companies in the current month's thread.

Comments are freeform prose, so this extracts a company name and apply link per
comment, then emits one Posting per role line that looks relevant. The
classifier makes the final call.
"""
from __future__ import annotations

import html as html_mod
import re
from datetime import datetime, timezone

from .base import Posting, SourceResult, html_to_text, http

SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&query=Ask+HN+Who+is+hiring"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"

# "Company | Role | Location | REMOTE | full-time | https://..."
_SPLIT = re.compile(r"\s*[|•·]\s*|\s+[-–—]\s+")
_URL = re.compile(r"https?://[^\s<>\"')]+")
_ROLE_HINT = re.compile(
    r"(?<![a-z])(product manager|product management|product designer|product design|"
    r"ux designer|ui designer|ux researcher|design(?:er)? intern|pm intern|apm|"
    r"associate product manager|intern(?:ship)?|new grad|entry.level|founding designer|"
    r"chief of staff|founder'?s associate|business operations)(?![a-z])", re.I)
_MONTH = re.compile(r"\(([A-Za-z]+)\s+(\d{4})\)")


def _latest_thread() -> tuple[str, str]:
    data = http(SEARCH, expect_json=True)
    for hit in data.get("hits", []):
        title = hit.get("title") or ""
        if "who is hiring" in title.lower() and _MONTH.search(title):
            return hit["objectID"], title
    raise ValueError("no Who-is-hiring thread found")


def _company_of(first_line: str) -> str:
    parts = [p.strip() for p in _SPLIT.split(first_line) if p.strip()]
    if not parts:
        return ""
    name = _URL.sub("", parts[0]).strip(" :|-–—")
    name = re.sub(r"\s+", " ", name)
    return name[:60]


def _apply_url(text: str) -> str:
    urls = _URL.findall(text)
    for u in urls:  # prefer a real careers/ATS link over a marketing homepage
        if re.search(r"(greenhouse|lever|ashby|workable|jobs|career|apply|breezy|rippling)", u, re.I):
            return u.rstrip(").,")
    return urls[0].rstrip(").,") if urls else ""


def fetch() -> SourceResult:
    res = SourceResult(name="hn_hiring")
    try:
        thread_id, thread_title = _latest_thread()
        data = http(ITEM.format(id=thread_id), expect_json=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        res.ok, res.error = False, f"{type(exc).__name__}: {exc}"
        return res

    posted = (data.get("created_at") or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    kept = 0
    for child in data.get("children", []) or []:
        raw = child.get("text") or ""
        if not raw:
            continue
        text = html_to_text(html_mod.unescape(raw))
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        company = _company_of(lines[0])
        if not company or len(company) < 2:
            continue
        url = _apply_url(text) or f"https://news.ycombinator.com/item?id={child.get('id')}"
        # One posting per distinct role hint, so a single comment advertising
        # both a PM and a designer produces two rows.
        roles = {m.group(0).strip().title() for m in _ROLE_HINT.finditer(text)}
        for role in sorted(roles)[:4]:
            res.postings.append(Posting(
                title=role,
                company=company,
                url=url,
                location="Remote" if re.search(r"\bremote\b", text, re.I) else "",
                description=text,
                source="hn_hiring",
                company_tags=["startup"],
                posted_at=posted,
                remote=bool(re.search(r"\bremote\b", text, re.I)),
            ))
            kept += 1
    res.detail = f"{thread_title}: {len(data.get('children') or [])} comments -> {kept} role rows"
    return res
