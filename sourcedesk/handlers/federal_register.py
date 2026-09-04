"""Federal Register handler.

The authoritative record of US rulemaking, and unusually well-behaved: its JSON
API returns documents that map directly onto items, no key required.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .. import canon, config

UTC = timezone.utc
API = "https://www.federalregister.gov/api/v1/documents.json"

TERMS = ["artificial intelligence", "robotics", "semiconductor export"]
# NOTICE is by far the highest-volume type and mostly procedural.
TYPES = ["RULE", "PRORULE", "PRESDOCU"]


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": config.USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def run(cfg):
    days = max(7, int(cfg.get("lookback_hours") or 24) // 24)
    since = (datetime.now(UTC) - timedelta(days=days)).date()
    items, seen = [], set()
    for term in TERMS:
        params = [
            ("conditions[term]", term),
            ("conditions[publication_date][gte]", str(since)),
            ("per_page", "20"),
            ("order", "newest"),
            ("fields[]", "title"), ("fields[]", "html_url"),
            ("fields[]", "publication_date"), ("fields[]", "type"),
            ("fields[]", "agencies"), ("fields[]", "abstract"),
            ("fields[]", "document_number"),
        ]
        for t in TYPES:
            params.append(("conditions[type][]", t))
        doc = _get(API + "?" + urllib.parse.urlencode(params))
        if not doc:
            continue
        for d in doc.get("results") or []:
            num = d.get("document_number") or d.get("html_url", "")
            if num in seen:
                continue
            seen.add(num)
            try:
                pub = datetime.strptime(d.get("publication_date", ""), "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                pub = datetime.now(UTC)
            agencies = ", ".join(a.get("name", "") for a in (d.get("agencies") or [])[:2])
            title = canon.clean_text(d.get("title") or "")
            url = d.get("html_url") or ""
            summary = canon.clean_text(
                "%s. %s %s" % (d.get("type", "Document"), agencies,
                               (d.get("abstract") or "")))
            items.append({
                "guid": "fedreg:%s" % num, "url": url,
                "canon_url": canon.canon_url(url), "title": title,
                "norm_title": canon.norm_title(title), "summary": summary,
                "published": pub,
            })
    return {"federal_register_artificial_intelligence": items}
