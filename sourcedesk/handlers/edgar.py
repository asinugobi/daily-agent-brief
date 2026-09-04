"""SEC EDGAR handler.

EDGAR is not an item stream, which is why polling it like a feed produced
"failures" for endpoints that were working perfectly. It answers questions. This
turns three of those questions into brief-ready items:

  1. Which watched companies filed something material in the window?
  2. Where is tracked language ("physical AI", "humanoid") appearing in 10-K
     risk factors? Disclosure moves before revenue does.
  3. What does the latest capex / R&D frame look like across all filers?

Every www.sec.gov path 403s without a contact address in the User-Agent, and the
published rate limit is 10 requests/second. Both are handled here.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .. import canon, config

UTC = timezone.utc

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK%010d.json"
FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/%s/USD/CY%dQ%d.json"
FULLTEXT = "https://efts.sec.gov/LATEST/search-index"
FILING_DOC = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s"
FILING_INDEX = "https://www.sec.gov/Archives/edgar/data/%d/%s/"

# Forms worth waking someone up for. 8-K is the material-event form; the rest
# are periodic reports and offerings.
FORMS_OF_INTEREST = {
    "8-K": "material event",
    "10-Q": "quarterly report",
    "10-K": "annual report",
    "20-F": "annual report (foreign issuer)",
    "S-1": "IPO registration",
    "S-4": "merger registration",
    "424B4": "prospectus",
    "SC 13D": "activist stake",
}

# Phrases whose spread through filings is itself the signal.
TRACKED_PHRASES = ["physical AI", "humanoid robot", "agentic AI", "AI data center"]

# Cross-filer league tables. Each entry is (label, [candidate us-gaap tags]).
#
# Capex needs several tags merged. Filers report the same economics under
# different concepts, and a single-tag capex frame is badly unrepresentative:
# CY2026Q2 on PaymentsToAcquirePropertyPlantAndEquipment alone returned 174
# filers led by T-Mobile and McDonald's, with every hyperscaler missing, while
# R&D on one tag returned 1,577 led by Meta and Alphabet. Publishing the first
# as "US filer capex" would be actively misleading.
FRAME_CONCEPTS = [
    ("capital expenditure", [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
    ]),
    ("R&D expense", ["ResearchAndDevelopmentExpense"]),
]

# A frame for the quarter just ended is thinly populated because filers report
# over weeks. Below this, walk back rather than publish an unrepresentative
# ranking - the period is always named in the item so the staleness is visible.
MIN_FILERS = 500

_RATE_DELAY = 0.13   # ~7.7 req/s, comfortably under the published 10/s
_last_call = [0.0]


def _get(url, data=None):
    """Rate-limited GET/POST returning parsed JSON, or None."""
    wait = _RATE_DELAY - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()

    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=config.TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def _item(guid, url, title, summary, when):
    return {
        "guid": guid[:500],
        "url": url[:1000],
        "canon_url": canon.canon_url(url),
        "title": title[:600],
        "norm_title": canon.norm_title(title),
        "summary": summary[:1200],
        "published": when,
    }


# --------------------------------------------------------------------------- tickers

_ticker_cache = {}


def ticker_map():
    """{TICKER: (cik, name)}. Fetched once per process.

    Resolving tickers through SEC's own map beats hardcoding CIKs, which is
    exactly the kind of detail that is easy to get subtly wrong.
    """
    if _ticker_cache:
        return _ticker_cache
    doc = _get(TICKERS_URL)
    if not doc:
        return _ticker_cache
    for row in doc.values():
        if isinstance(row, dict) and row.get("ticker"):
            _ticker_cache[row["ticker"].upper()] = (int(row["cik_str"]),
                                                    row.get("title", ""))
    return _ticker_cache


# --------------------------------------------------------------------------- filings

def watchlist_filings(tickers, hours=72):
    """Recent filings of interest for the watched companies."""
    tmap = ticker_map()
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).date()
    items = []
    for t in tickers:
        entry = tmap.get(t.upper())
        if not entry:
            continue
        cik, name = entry
        doc = _get(SUBMISSIONS % cik)
        if not doc:
            continue
        recent = (doc.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accs = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        descs = recent.get("primaryDocDescription") or []
        for i, form in enumerate(forms):
            if form not in FORMS_OF_INTEREST:
                continue
            try:
                filed = datetime.strptime(dates[i], "%Y-%m-%d").date()
            except (ValueError, IndexError):
                continue
            if filed < cutoff:
                continue
            acc = accs[i] if i < len(accs) else ""
            nodash = acc.replace("-", "")
            primary = docs[i] if i < len(docs) else ""
            url = (FILING_DOC % (cik, nodash, primary)) if primary \
                else (FILING_INDEX % (cik, nodash))
            desc = descs[i] if i < len(descs) else ""
            title = "%s (%s) filed %s - %s" % (
                name or t.upper(), t.upper(), form, FORMS_OF_INTEREST[form])
            summary = ("Accession %s filed %s. %s Primary document: %s. "
                       "Figures cited from this filing are citable to the accession number."
                       % (acc, dates[i], (desc + ".") if desc else "", primary or "index"))
            items.append(_item("edgar:%s:%s" % (cik, acc), url, title, summary,
                               datetime.combine(filed, datetime.min.time()).replace(tzinfo=UTC)))
    return items


# --------------------------------------------------------------------------- full text

def fulltext_hits(phrases=None, forms=("10-K", "10-Q", "8-K"), days=7, limit=6):
    """Filings whose text contains a tracked phrase."""
    phrases = phrases or TRACKED_PHRASES
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    items = []
    for phrase in phrases:
        q = ('%s?q=%%22%s%%22&forms=%s&dateRange=custom&startdt=%s&enddt=%s'
             % (FULLTEXT, urllib.parse.quote(phrase), ",".join(forms), start, end))
        doc = _get(q)
        if not doc:
            continue
        hits = ((doc.get("hits") or {}).get("hits") or [])[:limit]
        for h in hits:
            src = h.get("_source") or {}
            names = src.get("display_names") or []
            who = names[0] if names else "unknown filer"
            form = src.get("file_type") or src.get("root_form") or "filing"
            when = src.get("file_date") or ""
            adsh = (src.get("adsh") or h.get("_id", "")).split(":")[0]
            cik = (src.get("ciks") or [None])[0]
            url = ("https://www.sec.gov/Archives/edgar/data/%s/%s/"
                   % (str(cik).lstrip("0") if cik else "", adsh.replace("-", "")))
            try:
                pub = datetime.strptime(when, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                pub = datetime.now(UTC)
            title = '"%s" appears in a %s filed by %s' % (phrase, form, who)
            summary = ("EDGAR full-text search matched the phrase %r in %s (%s, filed %s). "
                       "Disclosure language is a leading indicator: it moves before revenue does."
                       % (phrase, form, who, when))
            items.append(_item("edgarft:%s:%s" % (phrase, adsh), url, title, summary, pub))
    return items


# --------------------------------------------------------------------------- frames

def _merged_frame(tags, y, q):
    """Union several us-gaap tags for one quarter, keyed by filer.

    Where a filer reports under more than one tag the largest value wins: the
    tags overlap in meaning, and taking the max avoids double counting while
    still capturing whichever concept that filer actually used.
    """
    merged, used = {}, []
    for tag in tags:
        doc = _get(FRAMES % (tag, y, q))
        if not doc or not doc.get("data"):
            continue
        used.append(tag)
        for e in doc["data"]:
            key = e.get("cik") or e.get("entityName")
            val = e.get("val") or 0
            if key is None or val <= 0:
                continue
            prev = merged.get(key)
            if prev is None or val > prev["val"]:
                merged[key] = {"entityName": e.get("entityName", ""),
                               "cik": e.get("cik"), "val": val}
    return list(merged.values()), used


def _latest_frame(tags, max_back=8):
    """Newest quarter whose merged frame is populated enough to rank."""
    now = datetime.now(UTC)
    y, q = now.year, (now.month - 1) // 3 + 1
    best = None
    for _ in range(max_back):
        rows, used = _merged_frame(tags, y, q)
        if rows:
            if len(rows) >= MIN_FILERS:
                return rows, used, y, q
            if best is None:
                best = (rows, used, y, q)   # remember the thin one as a fallback
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return best if best else (None, None, None, None)


def frames_league_table(watch_tickers=(), top=8):
    """One item per concept summarising the latest cross-filer frame."""
    items = []
    for label, tags in FRAME_CONCEPTS:
        data, used, y, q = _latest_frame(tags)
        if not data:
            continue
        thin = len(data) < MIN_FILERS
        rows = sorted(data, key=lambda e: -e.get("val", 0))
        lead = ", ".join("%s $%.1fB" % (r["entityName"].title(), r["val"] / 1e9)
                         for r in rows[:top] if r.get("val"))
        # Match the watchlist by CIK, not by name substring: "meta" as a
        # substring pulls in Metagenomi, Metavia and Liquidmetal.
        tmap = ticker_map()
        wanted = {tmap[t.upper()][0] for t in watch_tickers if t.upper() in tmap}
        watched = []
        for r in rows:
            if r.get("cik") in wanted:
                watched.append("%s $%.1fB" % (r["entityName"].title(), r["val"] / 1e9))
            if len(watched) >= top:
                break
        title = ("US filer %s for CY%dQ%d: %d companies reported%s"
                 % (label, y, q, len(rows), " (partial)" if thin else ""))
        summary = ("XBRL frames %s, CY%dQ%d, %d filers. Largest: %s.%s "
                   "Tags are merged because filers report the same economics under "
                   "different us-gaap concepts; the largest value per filer is kept. "
                   "%s"
                   % (" + ".join(used), y, q, len(rows), lead,
                      (" Watchlist: " + ", ".join(watched) + ".") if watched else "",
                      ("WARNING: only %d filers have reported this period, below the %d "
                       "needed to be representative - treat the ranking as incomplete."
                       % (len(rows), MIN_FILERS)) if thin else
                      "Still a starting population, not a census."))
        url = FRAMES % (used[0], y, q)
        items.append(_item("edgarframe:%s:%d-%d" % (label.replace(" ", "_"), y, q),
                           url, title, summary, datetime.now(UTC)))
    return items


# --------------------------------------------------------------------------- entry

def run(cfg):
    """Returns {feed_id: [items]} for the EDGAR api-kind sources."""
    tickers = list(cfg.get("watchlist") or []) + list(cfg.get("shadow_list") or [])
    hours = int(cfg.get("lookback_hours") or 24)
    # Filings land on business days; a 24h window on a Monday misses Friday.
    window = max(hours, 72)
    out = {}
    try:
        out["edgar_submissions_api"] = watchlist_filings(tickers, hours=window)
    except Exception as e:
        out["edgar_submissions_api"] = []
        print("  edgar submissions failed: %s" % e)
    try:
        out["edgar_full_text_search"] = fulltext_hits(days=max(7, window // 24))
    except Exception as e:
        out["edgar_full_text_search"] = []
        print("  edgar full-text failed: %s" % e)
    try:
        out["edgar_xbrl_frames_api"] = frames_league_table(tickers)
    except Exception as e:
        out["edgar_xbrl_frames_api"] = []
        print("  edgar frames failed: %s" % e)
    return out
