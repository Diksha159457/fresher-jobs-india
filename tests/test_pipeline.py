"""Offline tests: parsers on real-shaped fixtures, dedupe, tagger validation (LLM mocked)."""
import json
from types import SimpleNamespace

from scraper import tagger
from scraper.dedupe import dedupe
from scraper.sources import is_fresher_title, parse_greenhouse, parse_internshala, parse_lever

CARD = """<div class="container-fluid individual_internship" id="individual_internship_{id}" internshipid="{id}"
 employment_type="{et}" data-href="/{et}/detail/x-{id}"><h2 class="job-internship-name">
 <a class="job-title-href" href="/{et}/detail/x-{id}">{title}</a></h2>
 <p class="company-name"> {co} </p><div class="row-1-item locations"><span><a>{loc}</a></span></div>
 <div class="about_job"><div class="text"> We are looking for someone </div></div>
 <div class="job_skill">HTML</div><div class="job_skill">CSS</div>
 <div class="status-success"><span>1 day ago</span></div></div>"""


def page(*cards):
    return "<html><body>" + "".join(CARD.format(**c) for c in cards) + "</body></html>"


def test_internshala_parser():
    html = page(dict(id=1, et="internship", title="Front End Development", co="ViralChilly", loc="Jaipur"),
                dict(id=2, et="job", title="Fresher Marketing Associate", co="Echo Booom", loc="Pune"))
    got = parse_internshala(html)
    assert [g["title"] for g in got] == ["Front End Development", "Fresher Marketing Associate"]
    assert got[0]["url"] == "https://internshala.com/internship/detail/x-1"
    assert got[1]["employment_type"] == "job" and got[1]["location_raw"] == "Pune"
    assert got[0]["skills"] == ["HTML", "CSS"]


def test_fresher_filter():
    assert is_fresher_title("Software Engineer Intern")
    assert is_fresher_title("Associate Software Engineer")
    assert is_fresher_title("Lead Generation Intern")          # strong signal wins
    assert not is_fresher_title("Senior Software Engineer")
    assert not is_fresher_title("Software Engineer II")
    assert not is_fresher_title("Engineering Manager")


def test_ats_parsers():
    gh = {"jobs": [{"id": 1, "title": "Software Engineer Intern", "location": {"name": "Bengaluru, India"}, "absolute_url": "u"},
                   {"id": 2, "title": "Software Engineer Intern", "location": {"name": "Dublin"}, "absolute_url": "u"},
                   {"id": 3, "title": "Staff Engineer", "location": {"name": "Bengaluru"}, "absolute_url": "u"}],
          "meta": {"name": "Stripe"}}
    assert [p["source_id"] for p in parse_greenhouse("stripe", gh)] == ["stripe:1"]
    lv = [{"id": "a", "text": "Intern, Engineering", "country": "IN", "categories": {"location": "Bengaluru"}, "hostedUrl": "h"},
          {"id": "b", "text": "AI Product Builder", "country": "IN", "categories": {"location": "Bengaluru"}, "hostedUrl": "h"}]
    got = parse_lever("stable-money1", lv)
    assert len(got) == 1 and got[0]["company"] == "Stable Money"


def _p(i, title, co="Acme", loc="Bengaluru", src="internshala"):
    return {"source": src, "source_id": str(i), "title": title, "company": co, "location_raw": loc, "skills": [], "snippet": ""}


def test_dedupe():
    posts = [_p(1, "Web Development"), _p(1, "Web Development"),                 # same id
             _p(2, "Web Development", co="Acme Pvt Ltd"),                         # same key after norm
             _p(3, "SDE Intern", src="greenhouse"), _p(4, "Software Development Engineer Intern"),  # abbrev
             _p(5, "Front End Development"), _p(6, "Back End Development"),      # distinct
             _p(7, "Web Development", loc="Pune")]                                # other city
    kept, st = dedupe(posts)
    assert st["dup_same_id"] == 1 and st["dup_exact_key"] >= 1
    titles = sorted((k["title"], k["location_raw"]) for k in kept)
    assert ("Back End Development", "Bengaluru") in titles and ("Front End Development", "Bengaluru") in titles
    assert ("Web Development", "Pune") in titles
    assert len(kept) == 5


def test_tagger_with_mocked_llm(monkeypatch):
    posts = [_p(1, "Data Analyst Intern", loc="Bangalore"), _p(2, "Sales Intern", loc="Work From Home")]
    reply = {"tags": [{"id": "0", "role": "Data Science & AI/ML", "city": "Bangalore"},
                      {"id": "1", "role": "Sales & Business Development", "city": "Remote"}]}

    class FakeClient:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kw):
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=400, completion_tokens=60),
                                   choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(reply)))])

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setattr(tagger.config, "LLM_API_KEY", "x")
    monkeypatch.setattr(tagger.time, "sleep", lambda s: None)
    cache = {}
    u = tagger.tag_posts(posts, cache)
    assert posts[0]["city"] == "Bengaluru" and posts[1]["role"] == "Sales & Business Development"
    assert u.posts_tagged == 2 and len(cache) == 2
    assert tagger.tag_posts(posts, cache).requests == 0     # second run fully cached
    assert u.as_dict("gemini-3.1-flash-lite")["cost_usd"] > 0


def test_rule_baseline_city():
    rt = tagger.rule_tag
    assert rt({"title": "Data Analyst Intern", "location_raw": "Bengaluru, Karnataka, India"}) == {"role": "Data Science & AI/ML", "city": "Bengaluru"}
    assert rt({"title": "Sales Intern", "location_raw": "Delhi, Gurgaon"})["city"] == "Multiple"
    assert rt({"title": "Sales Intern", "location_raw": "Work From Home"})["city"] == "Remote"
    assert rt({"title": "HR Intern", "location_raw": "India"})["city"] == "India"


def test_posted_date():
    from datetime import date
    from scraper.run import posted_date
    d = date(2026, 11, 20)
    assert posted_date("2 weeks ago", d) == "2026-11-06"
    assert posted_date("Today", d) == "2026-11-20"
    assert posted_date(None, d) is None
