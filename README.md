# Fresher Jobs India

[![Daily refresh](https://github.com/Diksha159457/fresher-jobs-india/actions/workflows/refresh.yml/badge.svg)](https://github.com/Diksha159457/fresher-jobs-india/actions/workflows/refresh.yml)

A live web tool that collects **fresher job and internship posts in India** from public sources, **removes duplicates**, **tags each post by role and city with an LLM**, and **refreshes every day**.

* **Live site:** https://diksha159457.github.io/fresher-jobs-india/
* **Blind accuracy check:** https://diksha159457.github.io/fresher-jobs-india/review.html
* **Latest run stats:** [`docs/data/stats.json`](docs/data/stats.json)

## Results

| | |
|---|---|
| Live posts (latest run) | **422** from 451 collected; 29 duplicates removed |
| LLM cost per 1,000 posts | **$0.059** at paid list price (Gemini 3.1 Flash-Lite), from logged token usage; **$0** on the free tier actually used |

| n = 50 random posts, labeled blind | LLM (Gemini 3.1 Flash-Lite) | Keyword baseline |
|---|---|---|
| Role accuracy | **96%** (48/50, 95% CI 87–99%) | 56% (28/50, 95% CI 42–69%) |
| City accuracy | **96%** (48/50, 95% CI 87–99%) | 92% (46/50, 95% CI 81–97%) |
| Both correct | **92%** (46/50, 95% CI 81–97%) | 50% (25/50, 95% CI 37–63%) |

- **Role is where the LLM earns its cost.** It was right and the baseline wrong on 21 posts; the reverse happened once.
- **City is close**, because Internshala's location field is already clean.
- **LLM errors (4):**
  - Two posts got a real category where "Other" was correct: Robotics Trainer → Core Engineering (it's a teaching job), and cybercrime-law research → Marketing & Content.
  - Two multi-city listings ("Mumbai, Navi Mumbai", "Thane, Mumbai") were collapsed to "Mumbai" instead of "Multiple", even though the prompt asks for "Multiple".

**Disclosure:**
- Labels were first made blind. In a second pass, each label was checked against the written rules using the full listing. This pass was **not blind**: both systems' tags were visible.
- 15 labels were corrected (9 city-rule misapplications, 6 roles), and 1 spelling typo was fixed. Of the 6 role corrections, 5 moved toward the LLM's answer, so the LLM's role accuracy may be somewhat overstated. Every correction is backed by the listing's responsibilities; all labels are in `eval/hand_check.json`.
- The keyword baseline is a deliberately simple matcher written before evaluation. It sets a floor, not a strong competitor. Its main failure is word overlap, e.g. "Business Development" matching the software rule.
- A truly independent check would need a second labeler who never sees system output. That's the "Evaluation size" item under Limitations and next steps.

**How accuracy was measured**
* **Sample:** 50 posts drawn at random (seed 42) from the LLM-tagged set.
* **Rules first:** the [labeling rules](#labeling-rules) were written *before* labeling.
* **Blind labels:** each post was labeled by opening the original listing, without seeing either system's tags. This avoids anchoring on the LLM's answer.
* **Scoring:** both the LLM and a deterministic keyword baseline are scored against those labels. `python eval/score.py` prints accuracy with 95% Wilson intervals, a per-error breakdown, and the posts where the LLM beats the baseline or loses to it.
* **Caveats:** with only 50 posts the intervals are about ±10 points wide. There is one labeler, so there is no inter-annotator agreement figure.

**How cost was measured**
* Every API response's token usage is logged and priced at list price (`state/llm_totals.json`).
* Posts go 20 to a request as compact JSON (title, company, location, ≤6 skills, a 160-character snippet), with `temperature=0` and JSON-mode output.
* Results are cached by content hash, so the **daily** cost covers only new posts: a few dozen a day, which is a fraction of a cent.

## Architecture

```
 ┌───────────── GitHub Actions cron, 08:00 IST daily ─────────────┐
 │ collect ─► filter ─► dedupe ─► LLM tag ─► publish ─► health    │──► GitHub Pages (docs/)
 │ 4 sources  India +   3 passes  batched,   docs/data   check:    │    static site: search,
 │ (health    fresher             cached,    *.json      <200 posts│    filters, source status
 │  tracked)                      validated              = red run │
 └────────────────────────────────────────────────────────────────┘
```
I chose **GitHub Actions + Pages** because it needs no server and costs nothing, every refresh is a git commit (an auditable data history), and a failed run emails the owner.

## Sources (public, no login)

| Source | What | How |
|---|---|---|
| Internshala | Fresher jobs + CS / data-science / marketing internships | Server-rendered listing pages, allowed by its robots.txt. The bot sends an honest User-Agent, waits 1.5 s between requests and reads about 9 pages a day. |
| Greenhouse | ~28 company boards with India offices (Stripe, Razorpay, Groww, InMobi, Rubrik, MongoDB, …) | Public Job Board API |
| Lever | Stable Money, Mindtickle, 100ms, Sprinto, Fampay, … | Public Postings API |
| Ashby | Kong, Sarvam, Atlan, Rocketlane | Public Posting API |

* **Filtering:** posts from company job boards are kept only if the location is in India **and** the title signals a fresher role (intern, trainee, graduate, junior, associate, SDE-1, …) and not a senior one.
* **Source health:** each source's health is shown on the site, and a source returning 0 posts gets a red banner.
* **Failures:** a broken board is logged and skipped, so one bad source never fails the run.

## Deduplication (`scraper/dedupe.py`)

1. **Same ID.** The same Internshala post appears under several category listings.
2. **Same normalised key.** Same company + title + first city. The company name is stripped of Pvt/Ltd/Technologies and similar words, and title abbreviations are expanded (SDE, ML, AI, Jr).
3. **Fuzzy match.** Same company and city, with `token_sort_ratio ≥ 92` on the title.
   * *Why token_sort and not token_set?* token_set scores 100 whenever one title's words are a subset of the other's, so "Sales Intern" and "Sales and Marketing Intern" would be merged by mistake. token_sort scores them 63.
   * *Why 92?* The threshold catches re-posts ("…Intern" vs "…Intern 2027" scores 93.5) but keeps "Front End" and "Back End Development" apart (80).

Real examples of removed duplicates are written to `stats.json → dedupe.examples` on every run.

## LLM tagging (`scraper/tagger.py`)

* **Role:** one of 12 fixed categories.
* **City:** the canonical Indian city name, or `Remote`, `Multiple` or `India`.
* **Validation:** the output is checked. A role outside the taxonomy is mapped or set to `Other`, and city spellings are normalised (Bangalore → Bengaluru, Gurgaon → Gurugram).
* **Provider-agnostic:** it works with any OpenAI-compatible endpoint (Gemini, Groq, OpenAI) through `LLM_BASE_URL` and `LLM_MODEL`.
* **Fallback:** if no key is set, a keyword tagger takes over (`tagged_by: "rules"`), so the site never goes empty. The same keyword tagger is the evaluation baseline.

## Labeling rules

* **Role:** what the person will spend most of their time doing, judged from the responsibilities rather than the title or the company's industry.
* **Sales vs Marketing:** selling (lead gen, calling, closing, relationship manager, pre-sales) is **Sales & BD**, even when the title says "Marketing". Campaigns, SEO, social media, content and brand work is **Marketing & Content**.
* **Operations & Customer Support:** coordination, admin, back office, customer service, onboarding, PA/EA roles.
* **Other:** only when no category fits.
* **City:** the primary work location.
  * Work from home means **Remote**.
  * Several cities with no main one means **Multiple**.
  * Only the country given means **India**.

## Reliability

* **Failure alerts:** the workflow **fails, and GitHub emails the owner**, if fewer than 200 posts are live or Internshala returns nothing. The badge at the top shows the latest run status.
* **Tests:** offline tests cover the parsers, fresher filter, dedupe, the tagger (with a mocked LLM), the baseline and date parsing. They run before every refresh.

## Limitations and next steps

* **Source mix:** most volume comes from Internshala, so the role mix leans toward sales and ops. Next I would add more company boards and Indian job boards that offer APIs.
* **Dates:** Internshala shows relative dates ("7 days ago"), so `posted_date` is approximate.
* **Evaluation size:** 50 posts with one labeler. Next I would label 200 posts with two labelers and measure agreement (Cohen's κ).
* **Prompt iteration:** use the error table from `score.py` to add role definitions to the prompt (v2), then re-measure on the same 50 posts.

## Run it yourself

```bash
pip install -r requirements.txt
export LLM_API_KEY=...            # Gemini / Groq / OpenAI key
python -m scraper.run             # writes docs/data/*.json
python -m http.server -d docs     # http://localhost:8000
python -m pytest -q
python eval/score.py --md         # after the hand-check
```

**Deploy:**
1. Add the repo secret `LLM_API_KEY`. You can also set the variables `LLM_BASE_URL` and `LLM_MODEL`.
2. Turn on Pages: Settings → Pages → `main` / `/docs`.
3. Go to Actions → *Daily refresh* → Run workflow.

## Layout

```
scraper/   config.py · sources.py · dedupe.py · tagger.py · run.py
docs/      index.html · review.html · data/{jobs,stats,eval_sample}.json
state/     tag_cache.json · first_seen.json · llm_totals.json · runs.jsonl
eval/      score.py · hand_check.json
tests/     test_pipeline.py
```
