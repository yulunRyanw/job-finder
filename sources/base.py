"""Shared primitives for every job source adapter.

A source is a callable that returns a SourceResult. Sources must never raise:
a broken source degrades to ok=False with an error string so the pipeline can
render a health banner instead of silently dropping to zero jobs.
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Full descriptions are capped so the committed repo does not grow without bound.
MAX_DESC_CHARS = 12_000
SNIPPET_CHARS = 400

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

def html_to_text(raw: str | None) -> str:
    """Flatten an HTML job description to readable plain text."""
    if not raw:
        return ""
    text = html.unescape(raw)
    # Preserve block structure before stripping tags.
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "• ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def norm_key(value: str | None) -> str:
    """Aggressive normalisation used only for building dedupe keys."""
    if not value:
        return ""
    value = html.unescape(value).lower()
    value = _PUNCT_RE.sub(" ", value)
    return _WS_RE.sub(" ", value).strip()


def canonical_url(url: str | None) -> str:
    """Strip tracking params and fragments so the same job matches across sources."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    # Greenhouse/Lever job ids live in the query string, so keep only known-good keys.
    keep = []
    for chunk in parts.query.split("&"):
        if not chunk or "=" not in chunk:
            continue
        key = chunk.split("=", 1)[0].lower()
        if key in {"gh_jid", "jid", "id", "job_id", "lever_id", "ashby_jid"}:
            keep.append(chunk)
    return urlunsplit(("https", host, path, "&".join(sorted(keep)), ""))


# --------------------------------------------------------------------------
# posting model
# --------------------------------------------------------------------------

@dataclass
class Posting:
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""          # full plain text, capped
    source: str = ""               # adapter name, e.g. "greenhouse"
    company_tags: list[str] = field(default_factory=list)  # vc | startup | bigco
    posted_at: str = ""            # source-reported date, if any
    remote: bool = False
    # filled in by the pipeline
    id: str = ""
    category: str = ""
    level: str = ""
    region: str = ""
    first_seen: str = ""
    last_seen: str = ""
    active: bool = True

    def __post_init__(self) -> None:
        self.title = _WS_RE.sub(" ", html.unescape(self.title or "")).strip()
        self.company = _WS_RE.sub(" ", html.unescape(self.company or "")).strip()
        self.location = _WS_RE.sub(" ", html.unescape(self.location or "")).strip()
        self.url = (self.url or "").strip()
        if len(self.description) > MAX_DESC_CHARS:
            self.description = self.description[:MAX_DESC_CHARS].rstrip() + "\n\n[truncated - see full posting]"
        if not self.id:
            self.id = self.make_id()

    def make_id(self) -> str:
        """Stable across runs. Location is included because many companies post
        the same role in several cities as genuinely separate openings."""
        basis = f"{norm_key(self.company)}|{norm_key(self.title)}|{norm_key(self.location)}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    @property
    def snippet(self) -> str:
        text = _WS_RE.sub(" ", self.description).strip()
        return text[:SNIPPET_CHARS]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceResult:
    name: str
    postings: list[Posting] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    detail: str = ""

    @property
    def count(self) -> int:
        return len(self.postings)


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

_session = requests.Session()
_session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
})

_host_lock = threading.Lock()
_last_hit: dict[str, float] = {}
MIN_HOST_INTERVAL = 0.34  # be a polite citizen: ~3 req/sec per host


def _throttle(url: str) -> None:
    host = urlsplit(url).netloc
    with _host_lock:
        wait = _last_hit.get(host, 0.0) + MIN_HOST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def http(url: str, *, method: str = "GET", timeout: int = 30, retries: int = 3,
         expect_json: bool = False, **kwargs) -> Any:
    """HTTP with backoff. Returns parsed JSON when expect_json else response text.

    Raises the final exception if every attempt fails; callers catch it and
    convert to an unhealthy SourceResult.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            _throttle(url)
            resp = _session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code == 404:
                raise FileNotFoundError(f"404 {url}")
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code} {url}")
            resp.raise_for_status()
            if expect_json:
                return resp.json()
            return resp.text
        except FileNotFoundError:
            raise
        except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep((2 ** attempt) + random.uniform(0, 0.4))
    raise last_exc  # type: ignore[misc]


def dedupe_postings(postings: Iterable[Posting]) -> list[Posting]:
    """Collapse duplicates, preferring the entry with the richest description."""
    best: dict[str, Posting] = {}
    by_url: dict[str, str] = {}
    for p in postings:
        if not p.title or not p.company or not p.url:
            continue
        key = p.id
        cu = canonical_url(p.url)
        # A different id but identical canonical URL is the same job.
        if cu and cu in by_url and by_url[cu] != key:
            key = by_url[cu]
        elif cu:
            by_url[cu] = key
        incumbent = best.get(key)
        if incumbent is None or len(p.description) > len(incumbent.description):
            if incumbent is not None:
                p.id = incumbent.id
            best[key] = p
    return list(best.values())
