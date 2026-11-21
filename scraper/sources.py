"""Collectors. Each returns a list of raw post dicts with a common shape:

{source, source_id, title, company, location_raw, url, posted, employment_type, snippet, skills}
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger(__name__)
_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})

INDIA = re.compile(config.INDIA_RE, re.I)
FRESHER = re.compile(config.FRESHER_RE, re.I)
SENIOR = re.compile(config.SENIOR_RE, re.I)
# Per-run source health: {"greenhouse": {"ok": {"stripe": 7}, "failed": {"postman": "404"}}}
HEALTH: dict[str, dict] = {}


def _ok(src: str, name: str, n: int):
    HEALTH.setdefault(src, {"ok": {}, "failed": {}})["ok"][name] = n


def _fail(src: str, name: str, why):
    HEALTH.setdefault(src, {"ok": {}, "failed": {}})["failed"][name] = str(why)


STRONG_FRESHER = re.compile(r"\b(intern|internship|trainee|fresher|freshers|apprentice|new grad|graduate)\b", re.I)


def _get(url: str, **kw):
    for attempt in range(3):
        try:
            r = _session.get(url, timeout=config.TIMEOUT_SEC, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code}")
            return r
        except requests.RequestException as e:
            wait = 2 ** attempt * 2
            log.warning("GET %s failed (%s), retry in %ss", url, e, wait)
            time.sleep(wait)
    return None


def is_fresher_title(title: str) -> bool:
    if STRONG_FRESHER.search(title):
        return True
    return bool(FRESHER.search(title)) and not SENIOR.search(title)


def _iso(ms_or_str) -> str | None:
    if ms_or_str is None:
        return None
    if isinstance(ms_or_str, (int, float)):
        return datetime.fromtimestamp(ms_or_str / 1000, tz=timezone.utc).date().isoformat()
    return str(ms_or_str)[:10]


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ------------------------------------------------------------------ Internshala
def parse_internshala(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select(".individual_internship[internshipid]"):
        title_a = card.select_one("a.job-title-href") or card.select_one(".job-internship-name a")
        if not title_a:
            continue
        href = card.get("data-href") or title_a.get("href") or ""
        etype = card.get("employment_type") or ("job" if "/job/" in href else "internship")
        locs = [_clean(a.get_text()) for a in card.select(".locations a")] or [
            _clean(x.get_text()) for x in card.select(".locations span")
        ]
        posted = card.select_one(".status-success span, .status-info span, .status-inactive span")
        out.append({
            "source": "internshala",
            "source_id": card["internshipid"],
            "title": _clean(title_a.get_text()),
            "company": _clean((card.select_one(".company-name") or card.select_one(".company_name")).get_text())
            if (card.select_one(".company-name") or card.select_one(".company_name")) else "",
            "location_raw": ", ".join(l for l in locs if l),
            "url": "https://internshala.com" + href if href.startswith("/") else href,
            "posted": _clean(posted.get_text()) if posted else None,
            "employment_type": "internship" if etype == "internship" else "job",
            "snippet": _clean((card.select_one(".about_job .text") or soup.new_tag("x")).get_text())[:300],
            "skills": [_clean(s.get_text()) for s in card.select(".job_skill")][:8],
        })
    return out


def fetch_internshala() -> list[dict]:
    posts = []
    for base, pages in config.INTERNSHALA_PAGES.items():
        for p in range(1, pages + 1):
            url = base if p == 1 else f"{base}page-{p}/"
            r = _get(url)
            if r is None or r.status_code != 200:
                log.warning("internshala %s -> %s", url, getattr(r, "status_code", "ERR"))
                _fail("internshala", url, getattr(r, "status_code", "ERR"))
                break
            got = parse_internshala(r.text)
            log.info("internshala %s -> %d", url, len(got))
            (_ok if got else _fail)("internshala", url, len(got) if got else "0 cards parsed")
            if not got:
                break
            posts += got
            time.sleep(config.REQUEST_DELAY_SEC)
    return posts


# ------------------------------------------------------------------- Greenhouse
def parse_greenhouse(board: str, data: dict) -> list[dict]:
    company = (data.get("meta") or {}).get("name") or board
    out = []
    for j in data.get("jobs", []):
        loc = _clean((j.get("location") or {}).get("name"))
        title = _clean(j.get("title"))
        if not INDIA.search(loc) or not is_fresher_title(title):
            continue
        out.append({
            "source": "greenhouse", "source_id": f"{board}:{j['id']}", "title": title,
            "company": company if company != board else board.replace("softwareprivatelimited", "").title(),
            "location_raw": loc, "url": j.get("absolute_url"), "posted": _iso(j.get("first_published") or j.get("updated_at")),
            "employment_type": "internship" if re.search(r"intern", title, re.I) else "job",
            "snippet": "", "skills": [],
        })
    return out


def fetch_greenhouse() -> list[dict]:
    posts = []
    for b in config.GREENHOUSE_BOARDS:
        r = _get(f"https://boards-api.greenhouse.io/v1/boards/{b}/jobs")
        if r is None or r.status_code != 200:
            log.warning("greenhouse %s -> %s", b, getattr(r, "status_code", "ERR"))
            _fail("greenhouse", b, getattr(r, "status_code", "ERR"))
            continue
        name_r = _get(f"https://boards-api.greenhouse.io/v1/boards/{b}")
        data = r.json()
        if name_r is not None and name_r.ok:
            data["meta"] = {"name": name_r.json().get("name")}
        got = parse_greenhouse(b, data)
        _ok("greenhouse", b, len(got))
        log.info("greenhouse %s -> %d fresher India posts", b, len(got))
        posts += got
        time.sleep(0.5)
    return posts


# ------------------------------------------------------------------------ Lever
def parse_lever(board: str, data: list) -> list[dict]:
    out = []
    for j in data:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location") or ""]
        loc = ", ".join(l for l in locs if l)
        title = _clean(j.get("text"))
        in_india = j.get("country") == "IN" or INDIA.search(loc)
        if not in_india or not is_fresher_title(title):
            continue
        out.append({
            "source": "lever", "source_id": f"{board}:{j['id']}", "title": title,
            "company": board.replace("-", " ").rstrip("1").strip().title(),
            "location_raw": loc or "India", "url": j.get("hostedUrl"), "posted": _iso(j.get("createdAt")),
            "employment_type": "internship" if re.search(r"intern", title + " " + (cat.get("commitment") or ""), re.I) else "job",
            "snippet": _clean(j.get("descriptionPlain"))[:300], "skills": [],
        })
    return out


def fetch_lever() -> list[dict]:
    posts = []
    for b in config.LEVER_BOARDS:
        r = _get(f"https://api.lever.co/v0/postings/{b}?mode=json")
        if r is None or r.status_code != 200 or not isinstance(r.json(), list):
            log.warning("lever %s -> %s", b, getattr(r, "status_code", "ERR"))
            _fail("lever", b, getattr(r, "status_code", "ERR"))
            continue
        got = parse_lever(b, r.json())
        _ok("lever", b, len(got))
        log.info("lever %s -> %d", b, len(got))
        posts += got
        time.sleep(0.5)
    return posts


# ------------------------------------------------------------------------ Ashby
def parse_ashby(board: str, data: dict) -> list[dict]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "")
        loc = ", ".join(l for l in locs if l)
        title = _clean(j.get("title"))
        if not (INDIA.search(loc) or INDIA.search(country)) or not is_fresher_title(title):
            continue
        out.append({
            "source": "ashby", "source_id": f"{board}:{j.get('id') or j.get('jobUrl')}", "title": title,
            "company": board.title(), "location_raw": loc, "url": j.get("jobUrl"), "posted": _iso(j.get("publishedAt")),
            "employment_type": "internship" if re.search(r"intern", title + " " + (j.get("employmentType") or ""), re.I) else "job",
            "snippet": _clean(j.get("descriptionPlain"))[:300], "skills": [],
        })
    return out


def fetch_ashby() -> list[dict]:
    posts = []
    for b in config.ASHBY_BOARDS:
        r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{b}")
        if r is None or r.status_code != 200:
            log.warning("ashby %s -> %s", b, getattr(r, "status_code", "ERR"))
            _fail("ashby", b, getattr(r, "status_code", "ERR"))
            continue
        got = parse_ashby(b, r.json())
        _ok("ashby", b, len(got))
        log.info("ashby %s -> %d", b, len(got))
        posts += got
    return posts


ALL_SOURCES = {
    "internshala": fetch_internshala,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}
