"""Turn raw postings into categorised, level-tagged, location-scoped results.

Keyword-driven on purpose: this has to run for free inside GitHub Actions with
no model API key. Precision is favoured over recall -- a single Ashby board can
return 778 roles, so a permissive filter would bury the interesting ones.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "filters.yml"

# Priority when several categories tie.
CATEGORY_PRIORITY = ["vc", "design", "pm", "generalist"]

# a16z (and a few peers) prefix every requisition with "Partner 18," which is a
# req-numbering convention, not a seniority level. Strip it before classifying.
_REQ_PREFIX = re.compile(r"^\s*partner\s+\d+\s*[,\-:]\s*", re.I)

# Only these phrases may set a level from description text.
_DESC_LEVEL = [
    (re.compile(r"(?<![a-z])(?:summer|winter|fall|spring)\s+internship(?![a-z])", re.I), "intern"),
    (re.compile(r"(?<![a-z])internship\s+program(?![a-z])", re.I), "intern"),
    (re.compile(r"(?<![a-z])this\s+internship(?![a-z])", re.I), "intern"),
    (re.compile(r"(?<![a-z])as\s+an?\s+intern(?![a-z])", re.I), "intern"),
    (re.compile(r"(?<![a-z])co-?op\s+(?:program|student|position)(?![a-z])", re.I), "intern"),
    (re.compile(r"(?<![a-z])fellowship\s+program(?![a-z])", re.I), "fellowship"),
    (re.compile(r"(?<![a-z])part[\s-]?time(?![a-z])", re.I), "part_time"),
    (re.compile(r"(?<![a-z])working\s+student(?![a-z])", re.I), "part_time"),
    (re.compile(r"(?<![a-z])new\s+grad(?:uate)?(?![a-z])", re.I), "entry"),
]

_YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|-\s*\d{1,2})?\s*(?:or more\s*)?year[s]?\b(?![^.]{0,40}\bold\b)",
    re.I,
)

_INTERNATIONAL = {
    "united kingdom", "london", "england", "scotland", "ireland", "dublin",
    "germany", "berlin", "munich", "hamburg", "france", "paris", "spain",
    "madrid", "barcelona", "portugal", "lisbon", "netherlands", "amsterdam",
    "belgium", "brussels", "sweden", "stockholm", "norway", "oslo", "denmark",
    "copenhagen", "finland", "helsinki", "poland", "warsaw", "krakow",
    "switzerland", "zurich", "geneva", "austria", "vienna", "italy", "milan",
    "rome", "czech", "prague", "romania", "bucharest", "greece", "athens",
    "israel", "tel aviv", "india", "bangalore", "bengaluru", "mumbai", "delhi",
    "hyderabad", "pune", "singapore", "japan", "tokyo", "china", "beijing",
    "shanghai", "shenzhen", "hong kong", "korea", "seoul", "taiwan", "taipei",
    "australia", "sydney", "melbourne", "new zealand", "auckland", "canada",
    "toronto", "vancouver", "montreal", "ottawa", "brazil", "sao paulo",
    "mexico", "mexico city", "argentina", "colombia", "bogota", "chile",
    "uae", "dubai", "abu dhabi", "saudi", "riyadh", "egypt", "cairo",
    "nigeria", "lagos", "kenya", "nairobi", "south africa", "cape town",
    "philippines", "manila", "vietnam", "hanoi", "thailand", "bangkok",
    "indonesia", "jakarta", "malaysia", "kuala lumpur", "turkey", "istanbul",
    "ukraine", "kyiv", "emea", "apac", "latam",
}

_SF_BAY = {"san francisco", "sf", "bay area", "palo alto", "menlo park",
           "mountain view", "sunnyvale", "san jose", "santa clara", "redwood city",
           "oakland", "berkeley", "cupertino", "burlingame", "san mateo"}
_NYC = {"new york", "nyc", "brooklyn", "manhattan"}

_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "united states", "usa", "u.s.", "us", "washington dc", "district of columbia",
}
_US_ABBR = re.compile(
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    r"MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b"
)


def _phrase_re(phrase: str) -> re.Pattern[str]:
    """Word-boundary matcher tolerant of spacing/hyphen/slash variation."""
    parts = [re.escape(tok) for tok in re.split(r"[\s/\-]+", phrase.strip()) if tok]
    if not parts:
        return re.compile(r"(?!x)x")
    body = r"[\s/\-]*".join(parts) if len(parts) > 1 else parts[0]
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.I)


@functools.lru_cache(maxsize=1)
def load_rules() -> dict:
    raw = yaml.safe_load(CONFIG.read_text())
    rules: dict = {"categories": {}, "levels": {}}
    for name, spec in raw["categories"].items():
        rules["categories"][name] = {
            "label": spec.get("label", name),
            "strong": [_phrase_re(p) for p in spec.get("strong") or []],
            "weak": [_phrase_re(p) for p in spec.get("weak") or []],
            "exclude": [_phrase_re(p) for p in spec.get("exclude") or []],
            "open_ok": [_phrase_re(p) for p in spec.get("open_ok") or []],
        }
    for name, spec in raw["levels"].items():
        rules["levels"][name] = {
            "label": spec.get("label", name),
            "patterns": [_phrase_re(p) for p in spec.get("patterns") or []],
        }
    rules["seniority_exclude"] = [_phrase_re(p) for p in raw.get("seniority_exclude") or []]
    rules["seniority_allow"] = [_phrase_re(p) for p in raw.get("seniority_allow") or []]
    rules["max_years"] = int(raw.get("max_years_for_unspecified", 3))
    rules["keep_levels"] = set(raw.get("keep_levels") or []) or None
    ind = raw.get("industry_exclude") or {}
    rules["industry"] = {
        "applies_to": set(ind.get("applies_to") or []),
        "name_or_title": [_phrase_re(p) for p in ind.get("name_or_title") or []],
        "companies": [_phrase_re(p) for p in ind.get("companies") or []],
        "description": [_phrase_re(p) for p in ind.get("description") or []],
    }
    return rules


def normalize_title(title: str) -> str:
    return _REQ_PREFIX.sub("", title or "").strip()


def _any(patterns, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def min_years_required(description: str) -> int | None:
    """Lowest years-of-experience bar mentioned. None when unstated."""
    hits = [int(m.group(1)) for m in _YEARS.finditer(description or "")]
    hits = [h for h in hits if 0 < h <= 25]
    return min(hits) if hits else None


def detect_level(title: str, description: str) -> str:
    rules = load_rules()
    for name in ("intern", "fellowship", "part_time", "entry"):
        if _any(rules["levels"][name]["patterns"], title):
            return name
    # Descriptions are only trusted for unambiguous multi-word phrases: single
    # words like "resident" or "fellow" appear in ordinary prose and were
    # promoting senior engineering roles into the internship list.
    head = (description or "")[:1200]
    for pat, name in _DESC_LEVEL:
        if pat.search(head):
            return name
    return "unspecified"


def is_too_senior(title: str) -> bool:
    rules = load_rules()
    if _any(rules["seniority_allow"], title):
        return False
    return _any(rules["seniority_exclude"], title)


def detect_category(title: str, description: str, company_tags: list[str]) -> tuple[str, int]:
    """Return (category, score). Empty category means 'not relevant'."""
    rules = load_rules()
    is_vc_firm = "vc" in (company_tags or [])
    scores: dict[str, int] = {}

    for name, spec in rules["categories"].items():
        if _any(spec["exclude"], title):
            continue
        score = 0
        if _any(spec["strong"], title):
            score = 10
        elif spec["weak"] and _any(spec["weak"], title):
            # Weak signals need corroboration: a VC employer, or an early-career title.
            if name == "vc":
                score = 6 if is_vc_firm else 0
            else:
                score = 4 if detect_level(title, "") != "unspecified" else 0
        # Deliberately no description fallback: a job description that merely
        # mentions "design" or "venture capital" is not evidence of the role.
        if score:
            scores[name] = score

    if not scores:
        return "", 0
    best = max(scores.values())
    winners = [n for n, s in scores.items() if s == best]
    winners.sort(key=lambda n: CATEGORY_PRIORITY.index(n) if n in CATEGORY_PRIORITY else 99)
    return winners[0], best


def derive_region(location: str, remote: bool) -> str:
    loc = (location or "").lower()
    if not loc:
        return "remote" if remote else "unknown"
    if any(c in loc for c in _SF_BAY):
        return "sf_bay"
    if any(c in loc for c in _NYC):
        return "nyc"
    if _US_ABBR.search(location or "") or any(s in loc for s in _US_STATES if len(s) > 3):
        return "other_us"
    if any(c in loc for c in _INTERNATIONAL):
        return "international"
    if remote or "remote" in loc or "anywhere" in loc:
        return "remote"
    return "unknown"


def classify(posting) -> tuple[bool, str]:
    """Annotate a Posting in place. Returns (keep, reason_if_dropped)."""
    title = normalize_title(posting.title)
    posting.title = title

    if is_too_senior(title):
        return False, "too senior"

    category, _ = detect_category(title, posting.description, posting.company_tags)
    if not category:
        return False, "category miss"

    level = detect_level(title, posting.description)
    if level == "unspecified":
        # No explicit early-career signal. These are kept only narrowly, and the
        # page hides them behind a toggle, because otherwise the entire startup
        # job market floods the list.
        tags = set(posting.company_tags or [])
        if not tags & {"startup", "vc"}:
            return False, "no level signal"
        if category == "generalist" and not _any(
                load_rules()["categories"]["generalist"]["open_ok"], title):
            return False, "generalist needs level signal"
        years = min_years_required(posting.description)
        if years is not None and years > load_rules()["max_years"]:
            return False, f"requires {years}y"
        level = "open"

    rules = load_rules()
    keep = rules["keep_levels"]
    if keep and level not in keep:
        return False, f"level {level} not wanted"

    ind = rules["industry"]
    if category in ind["applies_to"]:
        blob = f"{posting.company} {title}"
        if _any(ind["name_or_title"], blob) or _any(ind["companies"], posting.company):
            return False, "excluded industry"
        if _any(ind["description"], (posting.description or "")[:2500]):
            return False, "excluded industry (desc)"

    region = derive_region(posting.location, posting.remote)
    if region == "international":
        return False, "international"

    posting.category = category
    posting.level = level
    posting.region = region
    return True, ""
