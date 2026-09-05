"""Persistent job store. `first_seen` is the whole point: it never changes once
set, which is what makes "what's new" real rather than a re-read of the list.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "jobs.json"

# How long a vanished posting stays in the file before being dropped.
KEEP_INACTIVE_DAYS = 45


def _today() -> datetime:
    return datetime.now(timezone.utc)


def load() -> dict[str, dict]:
    if not STORE.exists():
        return {}
    try:
        raw = json.loads(STORE.read_text())
    except json.JSONDecodeError:
        return {}
    return {j["id"]: j for j in raw.get("jobs", [])}


def merge(existing: dict[str, dict], postings: list) -> tuple[dict[str, dict], dict]:
    """Fold this run's postings into the store. Returns (store, stats)."""
    now = _today().strftime("%Y-%m-%dT%H:%M:%SZ")
    seen: set[str] = set()
    added = 0

    for p in postings:
        rec = p.to_dict()
        seen.add(rec["id"])
        prior = existing.get(rec["id"])
        if prior:
            rec["first_seen"] = prior.get("first_seen") or now
            # Never regress to an empty description if we already had one.
            if not rec.get("description") and prior.get("description"):
                rec["description"] = prior["description"]
        else:
            rec["first_seen"] = now
            added += 1
        rec["last_seen"] = now
        rec["active"] = True
        existing[rec["id"]] = rec

    # Anything not seen this run has come down off its board.
    closed = 0
    cutoff = (_today() - timedelta(days=KEEP_INACTIVE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for jid, rec in list(existing.items()):
        if jid in seen:
            continue
        if rec.get("active", True):
            rec["active"] = False
            closed += 1
        if (rec.get("last_seen") or "") < cutoff:
            del existing[jid]

    return existing, {"added": added, "closed": closed, "total": len(existing),
                      "active": sum(1 for r in existing.values() if r.get("active"))}


def save(store: dict[str, dict], health: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": _today().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "health": health,
        "jobs": sorted(store.values(), key=lambda r: (r.get("first_seen") or "", r.get("company") or ""), reverse=True),
    }
    STORE.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
