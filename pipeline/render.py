"""Emit the static site: a light index for fast first paint, sharded
descriptions loaded on demand, and an RSS feed.
"""
from __future__ import annotations

import json
import re
import shutil

import yaml
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "jobs.json"
DOCS = ROOT / "docs"
SNIPPET = 300
FEED_MAX = 60

LABELS = {"pm": "Product Management", "design": "Product Design",
          "vc": "Venture Capital", "generalist": "Startup Generalist"}


def _snippet(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:SNIPPET]


def render() -> dict:
    payload = json.loads(STORE.read_text())
    jobs = [j for j in payload.get("jobs", []) if j.get("active")]
    DOCS.mkdir(parents=True, exist_ok=True)
    desc_dir = DOCS / "desc"
    if desc_dir.exists():
        shutil.rmtree(desc_dir)
    desc_dir.mkdir(parents=True)

    index, shards = [], {}
    for j in jobs:
        index.append({
            "id": j["id"], "t": j["title"], "c": j["company"], "l": j.get("location", ""),
            "u": j["url"], "cat": j.get("category", ""), "lvl": j.get("level", ""),
            "r": j.get("region", ""), "s": j.get("source", ""),
            "f": (j.get("first_seen") or "")[:10], "p": (j.get("posted_at") or "")[:10],
            "sn": _snippet(j.get("description", "")),
            "tags": j.get("company_tags") or [],
        })
        if j.get("description"):
            shards.setdefault(j["id"][:2], {})[j["id"]] = j["description"]

    for shard, data in shards.items():
        (desc_dir / f"{shard}.json").write_text(json.dumps(data, ensure_ascii=False))

    index.sort(key=lambda x: (x["f"], x["c"]), reverse=True)
    (DOCS / "jobs.json").write_text(json.dumps({
        "generated_at": payload.get("generated_at"),
        "health": payload.get("health", []),
        "count": len(index),
        "jobs": index,
    }, ensure_ascii=False))

    _feed(index, payload.get("generated_at", ""))
    n_prog = _programs()
    return {"jobs": len(index), "shards": len(shards), "programs": n_prog}


def _programs() -> int:
    """Recurring student fellowships. Hand-curated because they never appear on
    job boards -- which is exactly why they are worth tracking."""
    src = ROOT / "config" / "programs.yml"
    if not src.exists():
        return 0
    data = yaml.safe_load(src.read_text()) or {}
    progs = data.get("programs", [])
    (DOCS / "programs.json").write_text(json.dumps({"programs": progs}, ensure_ascii=False))
    return len(progs)


def _feed(index: list[dict], generated: str) -> None:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = []
    for j in index[:FEED_MAX]:
        title = escape(f"{j['t']} — {j['c']}")
        cat = LABELS.get(j["cat"], j["cat"])
        body = escape(f"[{cat} · {j['lvl']}] {j['l']}\n\n{j['sn']}")
        items.append(
            f"<item><title>{title}</title><link>{escape(j['u'])}</link>"
            f"<guid isPermaLink=\"false\">{j['id']}</guid>"
            f"<description>{body}</description></item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
           '<title>Job Finder — PM · Design · VC · Startups</title>'
           '<link>https://example.invalid/</link>'
           '<description>New early-career postings</description>'
           f'<lastBuildDate>{now}</lastBuildDate>' + "".join(items) +
           '</channel></rss>')
    (DOCS / "feed.xml").write_text(xml)


if __name__ == "__main__":
    print(render())
