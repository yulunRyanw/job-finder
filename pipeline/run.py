"""Entry point. Fetch every source, classify, persist, render.

Designed to fail soft: any single source can die without taking down the run,
and its failure shows up in the health report on the page.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import store as store_mod  # noqa: E402
from pipeline.classify import classify  # noqa: E402
from sources import hn_hiring, signals, vc_careers  # noqa: E402
from sources.ats import fetch_ats  # noqa: E402
from sources.base import dedupe_postings, http  # noqa: E402

CONFIG = ROOT / "config" / "companies.yml"
# Detail-page fetches are rate-limited, so cap how many we backfill per run.
MAX_BACKFILL = 60


def gather(only: str | None = None) -> tuple[list, list[dict]]:
    companies = yaml.safe_load(CONFIG.read_text())["companies"]
    results = []
    jobs_for = {
        "greenhouse": lambda: fetch_ats(companies, "greenhouse"),
        "ashby": lambda: fetch_ats(companies, "ashby"),
        "lever": lambda: fetch_ats(companies, "lever"),
        "hn_hiring": hn_hiring.fetch,
        "vc_careers": vc_careers.fetch,
        "signals": signals.fetch,
    }
    for name, fn in jobs_for.items():
        if only and name != only:
            continue
        print(f"  fetching {name} ...", flush=True)
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001
            from sources.base import SourceResult
            res = SourceResult(name=name, ok=False, error=f"{type(exc).__name__}: {exc}")
        results.append(res)
        flag = "ok " if res.ok else "FAIL"
        print(f"    {flag} {res.count:5} raw  {res.error or res.detail}", flush=True)
    postings = [p for r in results for p in r.postings]
    health = [{"source": r.name, "ok": r.ok, "raw": r.count,
               "error": r.error, "detail": r.detail} for r in results]
    return postings, health


def backfill_descriptions(kept: list, existing: dict) -> int:
    """Listing pages carry no description; fetch detail pages for new keepers only."""
    todo = [p for p in kept
            if p.source == "vc_careers" and not p.description
            and not (existing.get(p.id) or {}).get("description")]
    done = 0
    for p in todo[:MAX_BACKFILL]:
        try:
            p.description = vc_careers.fetch_description(p.url)
            done += 1
        except Exception:  # noqa: BLE001
            continue
    return done


LABELS = {"pm": "Product Management", "design": "Product Design",
          "vc": "Venture Capital", "generalist": "Startup Generalist"}
# Only these levels are worth an email; "open" roles are too numerous.
NOTIFY_LEVELS = {"intern", "fellowship", "part_time", "entry"}


def write_digest(store: dict, prior_ids: set) -> int:
    """Markdown summary of new early-career postings, for the Issue digest."""
    fresh = [r for r in store.values()
             if r["id"] not in prior_ids and r.get("level") in NOTIFY_LEVELS]
    out = ROOT / "data" / "digest.md"
    if not fresh:
        out.write_text("")
        return 0
    fresh.sort(key=lambda r: (r.get("category", ""), r.get("company", "")))
    lines = [f"{len(fresh)} new early-career posting(s).", ""]
    for cat in ("vc", "pm", "design", "generalist"):
        rows = [r for r in fresh if r.get("category") == cat]
        if not rows:
            continue
        lines.append(f"### {LABELS[cat]}")
        for r in rows:
            loc = f" — {r['location']}" if r.get("location") else ""
            lines.append(f"- [{r['title']}]({r['url']}) · **{r['company']}**{loc} · _{r.get('level')}_")
        lines.append("")
    out.write_text("\n".join(lines))
    print(f"  digest: {len(fresh)} new early-career posting(s)")
    return len(fresh)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="do not write the store")
    ap.add_argument("--source", help="run a single source")
    ap.add_argument("--no-backfill", action="store_true")
    args = ap.parse_args()

    print("== fetching ==")
    postings, health = gather(args.source)
    print(f"\n== classifying {len(postings)} raw postings ==")

    kept, drops = [], Counter()
    for p in postings:
        ok, why = classify(p)
        (kept.append(p) if ok else drops.update([why]))
    print(f"  kept {len(kept)}")
    for why, n in drops.most_common(8):
        print(f"    dropped {n:6}  {why}")

    kept = dedupe_postings(kept)
    print(f"  after dedupe: {len(kept)}")

    if not args.dry_run:
        from pipeline.discover import discover
        new_cos = discover(postings)
        if new_cos:
            print(f"  discovered {len(new_cos)} new board(s): "
                  + ", ".join(f"{c['ats']}/{c['slug']}" for c in new_cos[:8]))

    existing = store_mod.load()
    if not args.no_backfill:
        n = backfill_descriptions(kept, existing)
        if n:
            print(f"  backfilled {n} description(s)")

    print("\n== breakdown ==")
    for key in ("category", "level", "region", "source"):
        c = Counter(getattr(p, key) for p in kept)
        print(f"  {key:9} {dict(c.most_common())}")

    if args.dry_run:
        print("\n(dry run - store not written)")
        return 0

    prior_ids = set(existing)
    merged, stats = store_mod.merge(existing, kept)
    store_mod.save(merged, health)
    print(f"\n== stored ==\n  {stats}")
    write_digest(merged, prior_ids)

    from pipeline.render import render
    render()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
