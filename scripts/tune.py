import sys, json, importlib; sys.path.insert(0,'.')
from collections import Counter
from sources.base import Posting, dedupe_postings
import pipeline.classify as C
importlib.reload(C)
raw = json.load(open("data/_raw_cache.json"))["posts"]
posts = []
for d in raw:
    d = {k: v for k, v in d.items() if k in Posting.__dataclass_fields__}
    for k in ("id","category","level","region","first_seen","last_seen","active"): d.pop(k, None)
    posts.append(Posting(**d))
kept, drops = [], Counter()
for p in posts:
    ok, why = C.classify(p)
    (kept.append(p) if ok else drops.update([why]))
kept = dedupe_postings(kept)
print(f"kept {len(kept)} of {len(posts)}")
for w,n in drops.most_common(6): print(f"   drop {n:6}  {w}")
for key in ("category","level","region"):
    print(f"  {key:9}", dict(Counter(getattr(p,key) for p in kept).most_common()))
print("  top companies:", Counter(p.company for p in kept).most_common(6))
