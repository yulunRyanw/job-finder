"""venturecapitalcareers.com -- the single highest-yield VC source (~535 roles).

Server-rendered, so a plain HTTP fetch works; no browser needed. Listing pages
carry title/company/location/date; full descriptions live on the detail page and
are fetched later, only for postings that survive classification.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import Posting, SourceResult, http

BASE = "https://venturecapitalcareers.com"
LIST_URL = BASE + "/jobs?page={page}"
JOB_HREF = re.compile(r"^/companies/([^/]+)/jobs/([^/?#]+)$")
MAX_PAGES = 30

_LOC = re.compile(
    r"·\s*(.+?)\s*(?:\((On-site|Hybrid|Remote)\))?\s*(?:\d+\s*(?:min|hour|hr|day|wk|week|mo|month|yr|year)s?\.?\s*ago|$)",
    re.I)


def _pretty(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).title()


def _clean_location(raw: str, company_slug: str) -> str:
    """Cards repeat the company name and location after the location itself.
    Cut at whichever repetition appears first."""
    loc = raw.strip(" ·-,")
    # 1. the company name (as slug words) restarts the repeated block
    words = [w for w in re.split(r"[-_]+", company_slug) if len(w) > 2]
    if words:
        pat = re.compile(r"\s+" + r"[\s-]*".join(re.escape(w) for w in words), re.I)
        loc = pat.split(loc)[0].strip(" ·-,")
    # 2. otherwise the location string simply repeats itself
    half = len(loc) // 2
    if half > 8 and loc[:half].strip(" ·-,").lower() == loc[half:].strip(" ·-,").lower():
        loc = loc[:half].strip(" ·-,")
    return re.split(r"\s*\((?:On-site|Hybrid|Remote)\)", loc, flags=re.I)[0].strip(" ·-,")


def _parse_page(html: str) -> list[Posting]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Posting] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = JOB_HREF.match(a["href"])
        if not m or a["href"] in seen:
            continue
        seen.add(a["href"])
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        if not title or len(title) < 3:
            continue
        # Walk up only as far as the card that still wraps exactly this one job,
        # otherwise sibling cards bleed their locations into this posting.
        card = a
        for _ in range(6):
            parent = card.parent
            if parent is None:
                break
            if sum(1 for x in parent.find_all("a", href=True) if JOB_HREF.match(x["href"])) > 1:
                break
            card = parent
        text = re.sub(r"\s+", " ", card.get_text(" ", strip=True))
        location = ""
        lm = _LOC.search(text)
        if lm:
            location = _clean_location(lm.group(1), m.group(1))
            if lm.group(2):
                location = f"{location} ({lm.group(2)})"
        out.append(Posting(
            title=title,
            company=_pretty(m.group(1)),
            url=f"{BASE}{a['href']}",
            location=location,
            source="vc_careers",
            company_tags=["vc"],
            remote=bool(re.search(r"\bremote\b", text, re.I)),
        ))
    return out


def fetch() -> SourceResult:
    res = SourceResult(name="vc_careers")
    pages = 0
    try:
        for page in range(1, MAX_PAGES + 1):
            html = http(LIST_URL.format(page=page), timeout=35)
            batch = _parse_page(html)
            pages = page
            if not batch:
                break
            res.postings.extend(batch)
    except Exception as exc:  # noqa: BLE001
        if not res.postings:
            res.ok, res.error = False, f"{type(exc).__name__}: {exc}"
            return res
        res.detail = f"stopped early at page {pages}: {type(exc).__name__}"
    res.detail = (res.detail + " " if res.detail else "") + f"{pages} page(s)"
    return res


def fetch_description(url: str) -> str:
    """Detail-page description. Called lazily for postings that pass the filter."""
    from .base import html_to_text
    soup = BeautifulSoup(http(url, timeout=30), "lxml")
    for sel in ("article", "main", '[class*="description"]', '[class*="prose"]'):
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 250:
            return html_to_text(str(node))
    return html_to_text(str(soup.body)) if soup.body else ""
