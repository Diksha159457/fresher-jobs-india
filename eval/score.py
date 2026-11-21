"""Score the blind hand-check: LLM vs keyword baseline, plus error analysis and cost.

    python eval/score.py            # reads eval/hand_check.json and state/llm_totals.json
    python eval/score.py --md       # also prints a Markdown block to paste into the README
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALIAS = {"bangalore": "bengaluru", "gurgaon": "gurugram", "new delhi": "delhi", "bombay": "mumbai",
         "cochin": "kochi", "work from home": "remote", "wfh": "remote", "calcutta": "kolkata", "madras": "chennai"}


def nc(c):
    c = (c or "").strip().lower()
    return ALIAS.get(c, c)


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p, d = k / n, 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d, (c + m) / d


hc = json.loads((ROOT / "eval/hand_check.json").read_text())
rows = [r for r in hc["rows"] if r.get("label_role") and r.get("label_city")]
n = len(rows)
if n == 0:
    sys.exit("No labeled rows in eval/hand_check.json")


def score(prefix):
    role = sum(r[f"{prefix}_role"] == r["label_role"] for r in rows)
    city = sum(nc(r[f"{prefix}_city"]) == nc(r["label_city"]) for r in rows)
    both = sum(r[f"{prefix}_role"] == r["label_role"] and nc(r[f"{prefix}_city"]) == nc(r["label_city"]) for r in rows)
    return {"role": role, "city": city, "both": both}


llm, base = score("llm"), score("rule")
fmt = lambda k: f"{100*k/n:5.1f}% ({k}/{n}, 95% CI {100*wilson(k, n)[0]:.0f}-{100*wilson(k, n)[1]:.0f}%)"
print(f"Blind hand-check of {n} random posts  (LLM: {hc.get('model')})\n")
print(f"{'':18}{'LLM':38}Keyword baseline")
for m in ("role", "city", "both"):
    print(f"  {m:<16}{fmt(llm[m]):38}{fmt(base[m])}")

# Posts the LLM got right and the baseline got wrong (and vice versa): where the LLM adds value.
lw = sum(r["llm_role"] == r["label_role"] and r["rule_role"] != r["label_role"] for r in rows)
bw = sum(r["rule_role"] == r["label_role"] and r["llm_role"] != r["label_role"] for r in rows)
print(f"\nRole: LLM right & baseline wrong on {lw} posts; baseline right & LLM wrong on {bw}.")

print("\nLLM role errors (LLM -> correct):")
for (a, b), c in Counter((r["llm_role"], r["label_role"]) for r in rows if r["llm_role"] != r["label_role"]).most_common():
    print(f"  {c}x  {a}  ->  {b}")
for r in rows:
    if r["llm_role"] != r["label_role"]:
        print(f"     e.g. '{r['title']}' @ {r['company']}" + (f"  [{r['note']}]" if r.get("note") else ""))
print("\nLLM city errors:")
for r in rows:
    if nc(r["llm_city"]) != nc(r["label_city"]):
        print(f"  listing '{r['location_raw']}': LLM {r['llm_city']}, correct {r['label_city']}")

t = json.loads((ROOT / "state/llm_totals.json").read_text())
per_post_in = t["prompt_tokens"] / max(t["posts_tagged"], 1)
per_post_out = t["completion_tokens"] / max(t["posts_tagged"], 1)
print(f"\nLLM usage: {t['posts_tagged']} posts tagged, {t['prompt_tokens']:,} in + {t['completion_tokens']:,} out tokens "
      f"(~{per_post_in:.0f} in / {per_post_out:.0f} out per post)")
print(f"Cost: ${t['cost_usd']:.4f} total -> ${t['cost_per_1000_posts_usd']} per 1,000 posts "
      f"({t['model']} paid-tier list price; $0 on the free tier). Cached posts cost nothing on later runs.")

if "--md" in sys.argv:
    p = lambda k: f"{100*k/n:.0f}% ({k}/{n})"
    print("\n--- README block ---")
    print(f"| Blind hand-check, {n} random posts | LLM ({t['model']}) | Keyword baseline |\n|---|---|---|")
    for m, lab in (("role", "Role accuracy"), ("city", "City accuracy"), ("both", "Both correct")):
        print(f"| {lab} | {p(llm[m])} | {p(base[m])} |")
    print(f"\nLLM cost: **${t['cost_per_1000_posts_usd']} per 1,000 posts** at paid list price "
          f"(~{per_post_in:.0f} input + {per_post_out:.0f} output tokens per post, 20 posts per request).")
