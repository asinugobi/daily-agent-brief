"""Runtime settings for the source desk pipeline.

Stdlib only, Python 3.9+. Override any value with an env var of the same name
prefixed SD_ (e.g. SD_CONTACT_EMAIL, SD_JACCARD).
"""
import os

def _env(name, default, cast=str):
    v = os.environ.get("SD_" + name.upper())
    if v is None:
        return default
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


# --- identity -------------------------------------------------------------
# SEC EDGAR returns 403 to every www.sec.gov path without a contact address in
# the User-Agent, and it is simply good manners everywhere else. Set this before
# running against EDGAR or those feeds will fail while data.sec.gov succeeds.
CONTACT_EMAIL = _env("contact_email", "set-me@example.com")
USER_AGENT = _env("user_agent", f"SourceDesk/1.0 (daily AI+econ brief; {CONTACT_EMAIL})")

# --- fetching -------------------------------------------------------------
TIMEOUT = _env("timeout", 20, int)             # seconds per request
WORKERS = _env("workers", 12, int)             # global thread pool size
PER_HOST = _env("per_host", 2, int)            # max concurrent requests per host
RETRIES = _env("retries", 2, int)              # retries after the first attempt
BACKOFF = _env("backoff", 1.5, float)          # seconds, doubled each retry
MAX_BYTES = _env("max_bytes", 8 * 1024 * 1024, int)

# Access models the feed fetcher can actually poll. Everything else is routed to
# the manual queue rather than silently failing at 6am.
FETCHABLE = {"open", "paywall_headlines"}
MANUAL = {"scrape", "license_required"}
NEEDS_KEY = {"key_required"}

# --- volume control -------------------------------------------------------
# arXiv alone publishes ~270 cs.AI papers a day. Without a cap one feed drowns
# the brief; without a keyword filter the cap just truncates arbitrarily.
MAX_ITEMS_PER_FEED = _env("max_items_per_feed", 60, int)
FEED_FILTERS = {
    # feed id -> keywords; an item must match at least one (title or summary)
    "arxiv_cs_ai": ["agent", "reasoning", "benchmark", "scaling", "safety",
                    "alignment", "evaluation", "economic", "labor", "labour"],
    "arxiv_cs_lg": ["scaling", "efficien", "training", "inference", "benchmark"],
    "arxiv_cs_cl": ["evaluation", "benchmark", "reasoning", "agent"],
    "arxiv_cs_ro": ["manipulation", "humanoid", "embodied", "foundation model",
                    "sim-to-real", "locomotion", "dexter"],
    "arxiv_eess_sy": ["power grid", "energy", "datacenter", "data center",
                      "manufactur", "supply chain"],
    "arxiv_cs_ma": ["coordination", "fleet", "market", "economic"],
}

# --- deduplication --------------------------------------------------------
# Merge rule: jaccard >= JACCARD  OR  (overlap >= OVERLAP AND shared >= MIN_SHARED).
# Tuned on labelled headline pairs including hard negatives - same entity, different
# event ("Fed holds rates" vs "Fed minutes show divide"), which is where naive
# thresholds actually fail. This setting gave 5/6 recall with 0/8 false merges.
# MIN_SHARED=3 is load-bearing: at 2 it merged "Figure unveils humanoid" with
# "Agility deploys humanoid". Under-merging is the safer error for a brief - you
# show a story twice rather than assert two events were one.
JACCARD = _env("jaccard", 0.45, float)
OVERLAP = _env("overlap", 0.50, float)
MIN_SHARED = _env("min_shared", 3, int)
CLUSTER_WINDOW_H = _env("cluster_window_h", 72, int)  # only merge within N hours
MIN_TOKENS = _env("min_tokens", 3, int)        # below this, require exact match

# --- digest ---------------------------------------------------------------
DIGEST_HOURS = _env("digest_hours", 24, int)   # lookback for the daily brief
TOP_CLUSTERS = _env("top_clusters", 25, int)

# Tier weights drive story ranking. A corroborated primary record outranks a
# widely-repeated piece of commentary - the same doctrine the source list uses.
TIER_WEIGHT = {1: 100, 2: 62, 3: 40, 4: 34}

# Tier and newsworthiness are different axes. A preprint is a genuine primary
# document (tier 1 for citation) but it is a research signal, not an event - and
# arXiv ships hundreds a day. Left at tier-1 weight it buries stories that four
# newsrooms independently carried. Ranked here between reported news and
# commentary; the tier shown in the brief is unchanged.
PREPRINT_OUTLETS = {"arxiv.org"}
PREPRINT_WEIGHT = _env("preprint_weight", 55, int)

# The lede section is reserved for multi-publisher stories, so a flood of
# single-source items cannot crowd it out on a busy research day.
TOP_CORROBORATED = _env("top_corroborated", 12, int)
BREADTH_POINTS = 9      # per additional distinct source in a cluster
DOMAIN_SPREAD_POINTS = 6  # per additional distinct domain
AGE_PENALTY_PER_H = 0.8
