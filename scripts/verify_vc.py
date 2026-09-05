"""Decide what a candidate VC board actually is.

Slug probing alone is dangerous: `wing` is Alphabet's drone unit, `sequoia` is a
payroll company, `amplify` is an education publisher. A board can be one of:
  vc_firm        - the firm's own board (investment/platform roles)
  portfolio      - the firm's board, but listing portfolio-company jobs
  collision      - an unrelated company that happens to own the slug
Only vc_firm boards may carry the `vc` tag, because that tag grants postings the
early-career benefit of the doubt.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sources.base import http  # noqa: E402

VC_TITLE = re.compile(
    r"(?<![a-z])(investment|investor|venture|portfolio|deal team|fund |limited partner|"
    r"platform|principal, invest|analyst, invest|associate, invest)(?![a-z])", re.I)
# Roles that only an operating company posts.
OPERATING = re.compile(
    r"(?<![a-z])(payroll|tutor|nurse|driver|warehouse|barista|cashier|technician|"
    r"flight|aviation|construction|manufacturing|retail associate|teacher)(?![a-z])", re.I)


def sample(ats: str, slug: str) -> tuple[list[str], str]:
    if ats == "greenhouse":
        d = http(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", expect_json=True)
        jobs = d.get("jobs", [])
        titles = [j.get("title", "") for j in jobs]
        dom = ""
        for j in jobs:
            m = re.match(r"https?://([^/]+)/", j.get("absolute_url", "") or "")
            if m and "greenhouse.io" not in m.group(1):
                dom = m.group(1).removeprefix("www."); break
        return titles, dom
    if ats == "ashby":
        d = http(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", expect_json=True)
        return [j.get("title", "") for j in d.get("jobs", []) or []], ""
    d = http(f"https://api.lever.co/v0/postings/{slug}?mode=json", expect_json=True)
    return [j.get("text", "") for j in d], ""


def verdict(titles: list[str], domain: str, expect_domain: str) -> tuple[str, str]:
    if not titles:
        return "collision", "no jobs"
    if expect_domain and domain and expect_domain in domain:
        return "vc_firm", f"domain match {domain}"
    if domain and expect_domain and expect_domain not in domain:
        return "collision", f"domain {domain} != {expect_domain}"
    vc_hits = sum(1 for t in titles if VC_TITLE.search(t))
    op_hits = sum(1 for t in titles if OPERATING.search(t))
    ratio = vc_hits / len(titles)
    if op_hits and not vc_hits:
        return "collision", f"operating roles ({op_hits})"
    if ratio >= 0.30:
        return "vc_firm", f"{vc_hits}/{len(titles)} investment titles"
    if vc_hits:
        return "portfolio", f"{vc_hits}/{len(titles)} investment titles (mostly portfolio)"
    return "portfolio", f"0/{len(titles)} investment titles"


def main() -> None:
    cands = json.loads(Path(sys.argv[1]).read_text())
    rows = []
    for c in cands:
        try:
            titles, dom = sample(c["ats"], c["slug"])
            v, why = verdict(titles, dom, c.get("domain", ""))
        except Exception as exc:  # noqa: BLE001
            v, why, titles = "collision", f"{type(exc).__name__}", []
        rows.append({**c, "verdict": v, "why": why, "sample": titles[:3]})
        print(f"  {v:10} {c['name']:26} {c['ats']:10} {c['slug']:24} | {why}")
        if v != "collision" and titles:
            print(f"             e.g. {titles[0][:62]}")
    Path("data/vc_verdicts.json").write_text(json.dumps(rows, indent=1))
    from collections import Counter
    print("\n", Counter(r["verdict"] for r in rows))


if __name__ == "__main__":
    main()
