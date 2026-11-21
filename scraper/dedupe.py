"""Duplicate removal in three passes.

1. Exact id:     same (source, source_id). The same Internshala post shows up under
                 several category listings.
2. Exact key:    same normalised (company, title, first city) across sources.
3. Fuzzy:        same company + same first city + title similarity >= 92
                 (token_sort_ratio). Catches "SDE Intern" vs "Software Development
                 Engineer - Intern" style re-posts.
The earliest-seen / richest record is kept; the others are counted as duplicates.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

ABBREV = {
    r"\bsde\b": "software development engineer",
    r"\bswe\b": "software engineer",
    r"\bml\b": "machine learning",
    r"\bai\b": "artificial intelligence",
    r"\bjr\b": "junior",
    r"\bui/ux\b": "ui ux",
}
COMPANY_NOISE = r"\b(pvt|private|ltd|limited|llp|inc|technologies|technology|tech|solutions|india|software|labs)\b"


def norm_title(t: str) -> str:
    t = t.lower()
    for k, v in ABBREV.items():
        t = re.sub(k, v, t)
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\b(internship|intern)\b", "intern", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_company(c: str) -> str:
    c = re.sub(r"[^a-z0-9 ]+", " ", c.lower())
    c = re.sub(COMPANY_NOISE, " ", c)
    return re.sub(r"\s+", "", c)


def first_city(loc: str) -> str:
    return re.sub(r"[^a-z]", "", (loc or "").lower().split(",")[0])


def richness(p: dict) -> int:
    return len(p.get("snippet") or "") + 20 * len(p.get("skills") or [])


def dedupe(posts: list[dict]) -> tuple[list[dict], dict]:
    stats = {"input": len(posts), "dup_same_id": 0, "dup_exact_key": 0, "dup_fuzzy": 0}
    examples: dict[str, list] = {"exact_key": [], "fuzzy": []}

    def ex(kind, a, b, score=None):
        if len(examples[kind]) < 8:
            e = {"kept": f"{a['title']} | {a['company']} | {a['location_raw']} ({a['source']})",
                 "dropped": f"{b['title']} | {b['company']} | {b['location_raw']} ({b['source']})"}
            if score is not None:
                e["similarity"] = round(score, 1)
            examples[kind].append(e)

    by_id: dict[str, dict] = {}
    for p in posts:
        k = f"{p['source']}:{p['source_id']}"
        if k in by_id:
            stats["dup_same_id"] += 1
            if richness(p) > richness(by_id[k]):
                by_id[k] = p
        else:
            by_id[k] = p

    by_key: dict[str, dict] = {}
    for p in by_id.values():
        k = f"{norm_company(p['company'])}|{norm_title(p['title'])}|{first_city(p['location_raw'])}"
        if k in by_key:
            stats["dup_exact_key"] += 1
            ex("exact_key", by_key[k], p)
            if richness(p) > richness(by_key[k]):
                by_key[k] = p
        else:
            by_key[k] = p

    kept: list[dict] = []
    buckets: dict[str, list[dict]] = {}
    for p in by_key.values():
        b = f"{norm_company(p['company'])}|{first_city(p['location_raw'])}"
        nt = norm_title(p["title"])
        match = next(((q, sc) for q in buckets.get(b, [])
                      if (sc := fuzz.token_sort_ratio(nt, norm_title(q["title"]))) >= 92), None)
        if match:
            stats["dup_fuzzy"] += 1
            ex("fuzzy", match[0], p, match[1])
            continue
        buckets.setdefault(b, []).append(p)
        kept.append(p)

    stats["output"] = len(kept)
    stats["removed"] = stats["input"] - stats["output"]
    stats["examples"] = examples
    return kept, stats
