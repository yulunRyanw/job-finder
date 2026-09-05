"""Adapters for the three applicant-tracking systems that publish free, keyless
JSON job boards: Greenhouse, Ashby and Lever.

Together these cover the overwhelming majority of startups and VC firms.
Each company is configured in config/companies.yml as {name, ats, slug, tags}.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .base import Posting, SourceResult, html_to_text, http

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

MAX_WORKERS = 8


def _is_remote(*values: str) -> bool:
    blob = " ".join(v for v in values if v).lower()
    return "remote" in blob or "anywhere" in blob


# --------------------------------------------------------------------------
# per-company fetchers -- each returns list[Posting] or raises
# --------------------------------------------------------------------------

def _fetch_greenhouse(co: dict) -> list[Posting]:
    data = http(GREENHOUSE_URL.format(slug=co["slug"]), expect_json=True)
    out = []
    for j in data.get("jobs", []) or []:
        loc = ((j.get("location") or {}).get("name") or "").strip()
        out.append(Posting(
            title=j.get("title", ""),
            company=co["name"],
            url=j.get("absolute_url", ""),
            location=loc,
            description=html_to_text(j.get("content")),
            source="greenhouse",
            company_tags=list(co.get("tags") or []),
            posted_at=(j.get("updated_at") or j.get("first_published") or "")[:10],
            remote=_is_remote(loc, j.get("title", "")),
        ))
    return out


def _fetch_ashby(co: dict) -> list[Posting]:
    data = http(ASHBY_URL.format(slug=co["slug"]), expect_json=True)
    out = []
    for j in data.get("jobs", []) or []:
        if j.get("isListed") is False:
            continue
        loc = (j.get("location") or "").strip()
        extra = j.get("secondaryLocations") or []
        if extra:
            names = [e.get("location", "") if isinstance(e, dict) else str(e) for e in extra]
            names = [n for n in names if n]
            if names:
                loc = "; ".join([loc] + names) if loc else "; ".join(names)
        out.append(Posting(
            title=j.get("title", ""),
            company=co["name"],
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            location=loc,
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            source="ashby",
            company_tags=list(co.get("tags") or []),
            posted_at=(j.get("publishedAt") or "")[:10],
            remote=bool(j.get("isRemote")) or _is_remote(loc, j.get("workplaceType") or ""),
        ))
    return out


def _fetch_lever(co: dict) -> list[Posting]:
    data = http(LEVER_URL.format(slug=co["slug"]), expect_json=True)
    if not isinstance(data, list):
        raise ValueError(f"lever returned {type(data).__name__}, not a list")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or ([cats.get("location")] if cats.get("location") else [])
        loc = "; ".join([l for l in locs if l])
        desc = j.get("descriptionPlain") or ""
        if j.get("additionalPlain"):
            desc = f"{desc}\n\n{j['additionalPlain']}"
        posted = ""
        if isinstance(j.get("createdAt"), (int, float)):
            posted = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).strftime("%Y-%m-%d")
        out.append(Posting(
            title=j.get("text", ""),
            company=co["name"],
            url=j.get("hostedUrl") or j.get("applyUrl") or "",
            location=loc,
            description=desc.strip(),
            source="lever",
            company_tags=list(co.get("tags") or []),
            posted_at=posted,
            remote=_is_remote(loc, j.get("workplaceType") or "", cats.get("commitment") or ""),
        ))
    return out


FETCHERS = {"greenhouse": _fetch_greenhouse, "ashby": _fetch_ashby, "lever": _fetch_lever}


def fetch_ats(companies: list[dict], ats: str) -> SourceResult:
    """Fetch every company on one ATS in parallel.

    A company that 404s has almost always migrated to a different ATS; that is
    normal churn and is reported as a dead slug rather than a source failure.
    """
    targets = [c for c in companies if c.get("ats") == ats and c.get("slug")]
    result = SourceResult(name=ats)
    if not targets:
        result.detail = "no companies configured"
        return result

    fetcher = FETCHERS[ats]
    dead: list[str] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetcher, co): co for co in targets}
        for fut in as_completed(futures):
            co = futures[fut]
            try:
                result.postings.extend(fut.result())
            except FileNotFoundError:
                dead.append(co["slug"])
            except Exception as exc:  # noqa: BLE001 - never let one company kill the run
                errors.append(f"{co['slug']}: {type(exc).__name__}")

    live = len(targets) - len(dead) - len(errors)
    result.detail = f"{live}/{len(targets)} boards live"
    if dead:
        result.detail += f", {len(dead)} dead slug(s): {', '.join(sorted(dead)[:6])}"
    if errors:
        result.detail += f", {len(errors)} error(s): {', '.join(sorted(errors)[:4])}"
    # Unhealthy only if essentially everything failed.
    if targets and live == 0:
        result.ok = False
        result.error = "all boards failed"
    return result
