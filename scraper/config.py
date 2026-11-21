"""Central configuration: sources, taxonomy, LLM settings and prices."""
import os

USER_AGENT = (
    "FresherJobsIndiaBot/1.0 (+https://github.com/Diksha159457/fresher-jobs-india; "
    "daily, low-volume, listing pages only)"
)
REQUEST_DELAY_SEC = 1.5  # politeness delay between requests to the same host
TIMEOUT_SEC = 25

# ---------------------------------------------------------------- Internshala
# Server-rendered listing pages (allowed by internshala.com/robots.txt).
INTERNSHALA_PAGES = {
    "https://internshala.com/fresher-jobs/": 4,                              # ~50/page
    "https://internshala.com/internships/computer-science-internship/": 3,   # ~40/page
    "https://internshala.com/internships/data-science-internship/": 1,
    "https://internshala.com/internships/marketing-internship/": 1,
}

# ---------------------------------------------------------- Company ATS boards
# Public, unauthenticated job-board APIs that companies publish for embedding.
# Boards that 404 or time out are skipped and logged; they never fail the run.
GREENHOUSE_BOARDS = [
    "stripe", "rubrik", "inmobi", "karya", "sumologic", "mongodb", "razorpaysoftwareprivatelimited",
    "okta", "hackerrank", "groww", "enterpret", "twilio", "highradius", "elastic", "toast",
    "workato", "datadog", "databricks", "zscaler", "purestorage", "zenoti", "druva", "devrev",
    "observeai", "gitlab", "airbnb", "coinbase", "newrelic",
]
LEVER_BOARDS = [
    "stable-money1", "mindtickle", "100ms", "Sprinto", "fampay", "paytm", "cred", "zeta", "upstox",
]
ASHBY_BOARDS = ["kong", "sarvam", "atlan", "rocketlane"]

INDIA_RE = (
    r"\b(india|bengaluru|bangalore|mumbai|navi mumbai|delhi|new delhi|ncr|gurgaon|gurugram|noida|"
    r"pune|hyderabad|chennai|kolkata|ahmedabad|jaipur|kochi|cochin|chandigarh|indore|coimbatore|"
    r"thiruvananthapuram|trivandrum|mysuru|mysore|lucknow|bhubaneswar|nagpur|vadodara|surat|"
    r"visakhapatnam|vizag|mohali|gandhinagar|goa|remote - india|india remote)\b"
)
# Fresher signal in the TITLE for ATS boards (Internshala pages are fresher-only already).
FRESHER_RE = (
    r"\b(intern|internship|trainee|graduate|grad|fresher|freshers|entry[- ]level|junior|jr\.?|"
    r"apprentice|campus|new grad|associate software|associate engineer|associate consultant|"
    r"associate data|associate product|analyst i|engineer i|developer i|sde[- ]?1|sde i|"
    r"software engineer 1|engineer 1|early career|university)\b"
)
SENIOR_RE = (
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|architect|vp|chief|"
    r"ii|iii|iv|2|3|4|expert|specialist ii)\b"
)

# ------------------------------------------------------------- Tag taxonomy
ROLES = [
    "Software Engineering",      # backend / frontend / full-stack / mobile / SDE
    "Data Science & AI/ML",      # DS, ML, AI, data analyst, analytics, BI
    "DevOps, Cloud & IT",        # devops, SRE, cloud, IT support, networking, security
    "QA & Testing",
    "Product & Design",          # PM, UI/UX, graphic design
    "Sales & Business Development",
    "Marketing & Content",       # digital marketing, SEO, social media, content writing
    "Operations & Customer Support",
    "Finance & Accounting",
    "HR & Recruiting",
    "Core Engineering",          # mechanical, electrical, civil, hardware, embedded
    "Other",
]
# Canonical city spellings the LLM must use; anything else is mapped/validated after.
CITY_ALIASES = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "new delhi": "Delhi", "delhi": "Delhi", "delhi ncr": "Delhi NCR", "ncr": "Delhi NCR",
    "bombay": "Mumbai", "navi mumbai": "Navi Mumbai", "cochin": "Kochi", "trivandrum": "Thiruvananthapuram",
    "mysore": "Mysuru", "vizag": "Visakhapatnam", "calcutta": "Kolkata", "madras": "Chennai",
    "work from home": "Remote", "wfh": "Remote", "remote": "Remote",
}

# --------------------------------------------------------------------- LLM
# Any OpenAI-compatible chat-completions endpoint works (Gemini, Groq, OpenAI ...).
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "20"))

# USD per 1M tokens (paid-tier list prices, checked 20 Nov 2026).
# Cost is computed from the token usage the API actually returns.
PRICES_PER_M = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "gpt-4o-mini": (0.15, 0.60),
}
