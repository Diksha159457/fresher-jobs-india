"""Daily pipeline: collect -> dedupe -> tag -> publish JSON for the static site.

    python -m scraper.run                # full run
    python -m scraper.run --sample-eval  # also (re)draw the 50-post hand-check sample
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .dedupe import dedupe
from . import sources
from .sources import ALL_SOURCES
from .tagger import rule_tag, tag_posts

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "data"
STATE = ROOT / "state"
log = logging.getLogger("run")


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))


_UNITS = {"hour": 0, "day": 1, "week": 7, "month": 30}
GENDER_RE = re.compile(r"\b(female|male)( candidates?)?( only)?\b|\b(girls|boys|women|men) only\b", re.I)


def posted_date(posted: str | None, today: date) -> str | None:
    """'Today' / '3 days ago' / '2 weeks ago' / ISO date -> ISO date (approximate for relative)."""
    if not posted:
        return None
    s = posted.strip().lower()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    if s in ("today", "just now") or "hour" in s or "few" in s:
        return today.isoformat()
    m = re.match(r"(\d+|a|an)\s+(hour|day|week|month)s?\s+ago", s)
    if m:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        return (today - timedelta(days=n * _UNITS[m.group(2)])).isoformat()
    return None


def main(sample_eval: bool = False, min_posts: int = 200):
    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now = now_dt.isoformat()
    today_ist = (now_dt + timedelta(hours=5, minutes=30)).date()
    first_run = not (STATE / "runs.jsonl").exists()
    sources.HEALTH.clear()

    raw, per_source = [], {}
    for name, fn in ALL_SOURCES.items():
        try:
            got = fn()
        except Exception as e:  # one broken source must not kill the run
            log.exception("source %s crashed: %s", name, e)
            sources._fail(name, "crash", repr(e)[:200])
            got = []
        per_source[name] = len(got)
        raw += got
    log.info("collected %d raw posts: %s", len(raw), per_source)

    posts, dd = dedupe(raw)

    cache = _load(STATE / "tag_cache.json", {})
    usage = tag_posts(posts, cache)
    _dump(STATE / "tag_cache.json", cache)

    first_seen = _load(STATE / "first_seen.json", {})
    out = []
    for p in posts:
        uid = f"{p['source']}:{p['source_id']}"
        first_seen.setdefault(uid, now)
        rt = rule_tag(p)  # keyword baseline, kept alongside the LLM tags for evaluation
        out.append({
            "id": uid, "title": p["title"], "company": p["company"], "location_raw": p["location_raw"],
            "role": p["role"], "city": p["city"], "tagged_by": p["tagged_by"],
            "rule_role": rt["role"], "rule_city": rt["city"],
            "type": p["employment_type"], "source": p["source"], "url": p["url"],
            "posted": p.get("posted"), "posted_date": posted_date(p.get("posted"), today_ist),
            "first_seen": first_seen[uid], "skills": p.get("skills", []),
            "gender_restricted": bool(GENDER_RE.search(p["title"])),
        })
    out.sort(key=lambda x: x["first_seen"], reverse=True)
    _dump(STATE / "first_seen.json", first_seen)

    health = {}
    for name in ALL_SOURCES:
        h = sources.HEALTH.get(name, {"ok": {}, "failed": {}})
        health[name] = {"posts": per_source.get(name, 0), "endpoints_ok": len(h["ok"]),
                        "endpoints_failed": len(h["failed"]), "failed": h["failed"],
                        "status": "ok" if per_source.get(name, 0) > 0 else "down"}
    run = {
        "run_at": now, "first_run": first_run, "raw_by_source": per_source, "source_health": health, "dedupe": dd,
        "live_posts": len(out), "new_today": sum(1 for x in out if x["first_seen"] == now),
        "by_role": Counter(x["role"] for x in out).most_common(),
        "by_city": Counter(x["city"] for x in out).most_common(15),
        "llm": usage.as_dict(config.LLM_MODEL),
    }
    # Running totals of LLM spend across every daily run -> long-run cost per 1,000 posts.
    totals = _load(STATE / "llm_totals.json", {"prompt_tokens": 0, "completion_tokens": 0, "posts_tagged": 0, "cost_usd": 0.0})
    totals["prompt_tokens"] += usage.prompt_tokens
    totals["completion_tokens"] += usage.completion_tokens
    totals["posts_tagged"] += usage.posts_tagged
    totals["cost_usd"] = round(totals["cost_usd"] + usage.cost_usd(config.LLM_MODEL), 6)
    totals["cost_per_1000_posts_usd"] = (
        round(totals["cost_usd"] / totals["posts_tagged"] * 1000, 4) if totals["posts_tagged"] else None)
    totals["model"] = config.LLM_MODEL
    _dump(STATE / "llm_totals.json", totals)
    run["llm_totals"] = totals

    _dump(DOCS / "jobs.json", {"updated": now, "first_run": first_run, "count": len(out), "jobs": out})
    _dump(DOCS / "stats.json", run)
    with (STATE / "runs.jsonl").open("a") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")

    sample_path = DOCS / "eval_sample.json"
    if sample_eval or not sample_path.exists():
        llm_tagged = [x for x in out if x["tagged_by"] == "llm"] or out
        rnd = random.Random(42)
        sample = rnd.sample(llm_tagged, min(50, len(llm_tagged)))
        _dump(sample_path, {"drawn": now, "seed": 42, "model": config.LLM_MODEL, "posts": sample})
        log.info("drew %d-post evaluation sample", len(sample))
    else:  # keep the frozen sample, but (re)compute the deterministic keyword baseline on it
        smp = json.loads(sample_path.read_text())
        for x in smp["posts"]:
            rt = rule_tag({"title": x["title"], "location_raw": x["location_raw"]})
            x["rule_role"], x["rule_city"] = rt["role"], rt["city"]
        _dump(sample_path, smp)

    log.info("done: %d live posts (%d new), dedupe removed %d, LLM cost $%.5f",
             len(out), run["new_today"], dd["removed"], usage.cost_usd(config.LLM_MODEL))
    if len(out) < min_posts:
        log.warning("only %d posts (< %d) - check source logs above", len(out), min_posts)
    return run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-eval", action="store_true")
    a = ap.parse_args()
    main(sample_eval=a.sample_eval)
