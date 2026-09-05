# Job Finder

A self-updating board for **product management**, **product design**,
**venture capital** and **startup** roles aimed at college students.
Runs free on GitHub Actions, publishes to GitHub Pages, and remembers what it
has already shown you so "what's new" actually means something.

## How it works

Two tiers, one store.

**Tier 1 — automated (GitHub Actions, every 6 hours).** Sources with a stable,
keyless API or server-rendered HTML:

| Source | What it covers |
|---|---|
| Greenhouse / Ashby / Lever | ~86 verified company boards, with full job descriptions |
| venturecapitalcareers.com | ~535 VC roles — the highest-yield single VC source |
| HN "Who is hiring?" | ~240 startups a month that never touch a job board |

**Tier 2 — Claude-run (on request).** X, LinkedIn, VC newsletters and blogs.
These need judgement and cannot run unattended, so Claude sweeps them and writes
JSON into `signals/inbox/`, which the same pipeline merges.
See [`signals/SWEEP.md`](signals/SWEEP.md).

Everything then goes through one filter: category (PM / design / VC /
generalist), level (internship / fellowship / part-time / new grad / open),
and region, with senior roles and non-US postings dropped.

## Layout

```
config/companies.yml   verified job boards      config/filters.yml   role taxonomy
config/programs.yml    student fellowships      sources/             one adapter per source
pipeline/classify.py   relevance filter         pipeline/store.py    first_seen tracking
pipeline/discover.py   grows the company list   docs/                the published site
```

## Running it locally

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m pipeline.run            # fetch, classify, store, render
./.venv/bin/python -m pipeline.run --dry-run  # inspect without writing
cd docs && python3 -m http.server 8777        # then open localhost:8777
```

Rebuild the verified company list (boards migrate constantly):

```bash
./.venv/bin/python scripts/probe_slugs.py data/candidates.json
```

## Design notes

**`first_seen` is never overwritten.** It is what makes NEW badges and the
digest real rather than a re-read of the whole list.

**Company slugs are verified, not guessed.** Probing alone is dangerous:
`wing` is Alphabet's drone unit, `sequoia` is a payroll company and `amplify`
is an education publisher — none of them the VC firms of the same name. Those
collisions were caught by `scripts/verify_vc.py` and excluded.

**Every source reports health.** Boards rot: companies migrate off Lever, APIs
change shape. Each run records per-source status and counts, and the page shows
a red banner when a source fails, so this degrades loudly instead of silently
returning nothing.

**Precision over recall.** One Ashby board alone returns 778 roles. A permissive
filter buries the interesting postings, so the classifier is deliberately strict
and roles with no seniority signal are kept only for startups and VC firms.

## Known limits

- **X coverage is partial.** There is no free X API; this relies on search
  indexing and the user's own browser session.
- **LinkedIn is never scraped** — ToS and account-ban risk. Read manually only.
- **Keyword classification misfires** on a small share of postings, VC most of
  all, because VC job titles are idiosyncratic (a16z prefixes every listing
  with `Partner NN,`).
- **GitHub disables scheduled workflows after ~60 days of repo inactivity.**
  If the cron stops, re-enable it from the Actions tab.
