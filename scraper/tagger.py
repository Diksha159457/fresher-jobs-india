"""LLM tagging: role (fixed taxonomy) + city (canonical Indian city / Remote).

* Batched: LLM_BATCH_SIZE posts per request with compact inputs -> few tokens per post.
* Cached by content hash: the daily refresh only pays for posts it has not seen.
* Validated: role must be in the taxonomy, city is normalised via CITY_ALIASES.
* Usage tracked: input/output tokens from the API response -> exact cost.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

from . import config

log = logging.getLogger(__name__)

SYSTEM = f"""You tag Indian fresher job and internship posts.
For each post return:
- "role": exactly one of {json.dumps(config.ROLES)}
- "city": the primary Indian city where the work happens, in standard English spelling
  (e.g. Bengaluru, Mumbai, Delhi, Gurugram, Noida, Pune, Hyderabad, Chennai, Kolkata).
  Use "Remote" for work-from-home/remote posts, "Multiple" if several cities are listed
  with none primary, "India" if only the country is known.
Decide the role from what the person will actually do (title, skills, snippet), not the company.
Reply with JSON only: {{"tags":[{{"id":"...","role":"...","city":"..."}}, ...]}} in the same order."""


def post_key(p: dict) -> str:
    raw = f"{p['title']}|{p['company']}|{p['location_raw']}|{','.join(p.get('skills') or [])}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _compact(p: dict, i: int) -> dict:
    d = {"id": str(i), "title": p["title"], "company": p["company"], "location": p["location_raw"]}
    if p.get("skills"):
        d["skills"] = ", ".join(p["skills"][:6])
    if p.get("snippet"):
        d["snippet"] = p["snippet"][:160]
    return d


def normalise_city(city: str, location_raw: str = "") -> str:
    c = (city or "").strip()
    low = c.lower()
    if low in config.CITY_ALIASES:
        return config.CITY_ALIASES[low]
    if low in ("multiple", "india", "remote"):
        return c.title()
    return c.title() if c else "India"


def normalise_role(role: str) -> str:
    if role in config.ROLES:
        return role
    low = (role or "").lower()
    for r in config.ROLES:
        if low and (low in r.lower() or r.lower().split(" ")[0] in low):
            return r
    return "Other"


class Usage:
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.requests = 0
        self.posts_tagged = 0

    def cost_usd(self, model: str) -> float:
        pin, pout = config.PRICES_PER_M.get(model, (0.0, 0.0))
        return (self.prompt_tokens * pin + self.completion_tokens * pout) / 1e6

    def as_dict(self, model: str) -> dict:
        c = self.cost_usd(model)
        return {
            "model": model, "requests": self.requests, "posts_tagged": self.posts_tagged,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "price_per_m_in_out_usd": config.PRICES_PER_M.get(model),
            "cost_usd": round(c, 6),
            "cost_per_1000_posts_usd": round(c / self.posts_tagged * 1000, 4) if self.posts_tagged else None,
        }


def _call(client, batch: list[dict], usage: Usage) -> list[dict]:
    payload = [_compact(p, i) for i, p in enumerate(batch)]
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=config.LLM_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": json.dumps({"posts": payload}, ensure_ascii=False)}],
            )
            usage.requests += 1
            if resp.usage:
                usage.prompt_tokens += resp.usage.prompt_tokens or 0
                usage.completion_tokens += resp.usage.completion_tokens or 0
            text = resp.choices[0].message.content or "{}"
            text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
            tags = {str(t.get("id")): t for t in json.loads(text).get("tags", [])}
            return [tags.get(str(i), {}) for i in range(len(batch))]
        except Exception as e:  # rate limits, bad JSON ...
            wait = 5 * (attempt + 1)
            log.warning("LLM batch failed (%s); retry in %ss", e, wait)
            time.sleep(wait)
    return [{} for _ in batch]


def tag_posts(posts: list[dict], cache: dict) -> Usage:
    """Fills p['role'], p['city'], p['tagged_by'] in place. Mutates cache."""
    usage = Usage()
    todo = []
    for p in posts:
        k = post_key(p)
        p["_key"] = k
        if k in cache:
            p.update(role=cache[k]["role"], city=cache[k]["city"], tagged_by=cache[k].get("by", "llm"))
        else:
            todo.append(p)
    log.info("tagging: %d cached, %d new", len(posts) - len(todo), len(todo))
    if not todo:
        return usage

    if not config.LLM_API_KEY:
        log.warning("LLM_API_KEY not set: using keyword fallback tagger (not cached)")
        for p in todo:
            p.update(rule_tag(p), tagged_by="rules")
        return usage

    from openai import OpenAI
    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    for s in range(0, len(todo), config.LLM_BATCH_SIZE):
        batch = todo[s:s + config.LLM_BATCH_SIZE]
        results = _call(client, batch, usage)
        for p, t in zip(batch, results):
            if t.get("role"):
                p.update(role=normalise_role(t["role"]), city=normalise_city(t.get("city"), p["location_raw"]), tagged_by="llm")
                cache[p["_key"]] = {"role": p["role"], "city": p["city"], "by": "llm", "model": config.LLM_MODEL}
                usage.posts_tagged += 1
            else:
                p.update(rule_tag(p), tagged_by="rules")
        time.sleep(1)  # stay well inside free-tier RPM limits
    return usage


# --------------------------------------------- fallback (also a baseline to compare)
_RULES = [
    ("QA & Testing", r"\b(qa|quality assurance|test(ing|er)?|sdet)\b"),
    ("Data Science & AI/ML", r"\b(data|machine learning|ml|ai|artificial intelligence|analytics?|analyst|nlp|deep learning|genai|llm)\b"),
    ("DevOps, Cloud & IT", r"\b(devops|cloud|sre|site reliability|it support|network|security|cyber|system admin)\b"),
    ("Software Engineering", r"\b(software|developer|development|engineer|sde|frontend|front end|backend|back end|full ?stack|web|android|ios|flutter|react|java|python)\b"),
    ("Product & Design", r"\b(product|design|designer|ui|ux|graphic)\b"),
    ("Sales & Business Development", r"\b(sales|business development|bde|bdm|inside sales|telecaller)\b"),
    ("Marketing & Content", r"\b(marketing|seo|content|social media|digital|brand|copywrit|writer|video edit)\b"),
    ("Finance & Accounting", r"\b(finance|account|ca|audit|tax|financial)\b"),
    ("HR & Recruiting", r"\b(hr|human resource|recruit|talent acquisition)\b"),
    ("Operations & Customer Support", r"\b(operations|customer|support|admin|office|executive|coordinator)\b"),
    ("Core Engineering", r"\b(mechanical|electrical|civil|embedded|hardware|vlsi|electronics)\b"),
]


def rule_tag(p: dict) -> dict:
    t = p["title"].lower()
    role = next((r for r, rx in _RULES if re.search(rx, t)), "Other")
    loc = p["location_raw"].lower()
    cities = {normalise_city(m.group(0)) for m in re.finditer(config.INDIA_RE, loc, re.I)
              if m.group(0).lower() not in ("india", "ncr", "remote - india", "india remote")}
    if not loc or "work from home" in loc or "remote" in loc:
        city = "Remote"
    elif len(cities) == 1:
        city = cities.pop()
    elif len(cities) > 1:
        city = "Multiple"
    elif "india" in loc:
        city = "India"
    else:
        city = normalise_city(loc.split(",")[0].split("(")[0].strip())
    return {"role": role, "city": city}
