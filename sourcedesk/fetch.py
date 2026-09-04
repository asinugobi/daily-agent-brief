"""Conditional, polite, parallel feed fetching.

Three things make this safe to run every morning against 100+ hosts:
conditional GETs so unchanged feeds cost a 304, a per-host concurrency cap so no
single publisher sees a burst, and backoff that distinguishes a server problem
(retry) from a client one (do not).
"""
import gzip
import io
import socket
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

from . import canon, config, parse

_host_locks = defaultdict(lambda: threading.Semaphore(config.PER_HOST))
_lock_guard = threading.Lock()


def _sem(url):
    host = (urlsplit(url).hostname or "").lower()
    with _lock_guard:
        return _host_locks[host]


def _decompress(raw, encoding):
    if not raw:
        return raw
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        return raw
    return raw


def http_get(url, etag=None, last_modified=None):
    """Returns (status, body, etag, last_modified).

    status is 'ok' | 'not_modified' | 'http_<code>' | 'error'.
    """
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, "
                  "text/xml, application/json;q=0.9, */*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    req = urllib.request.Request(url, headers=headers, method="GET")
    delay = config.BACKOFF
    last_exc = None

    for attempt in range(config.RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=config.TIMEOUT) as resp:
                raw = resp.read(config.MAX_BYTES + 1)
                if len(raw) > config.MAX_BYTES:
                    return "error", None, None, None
                body = _decompress(raw, resp.headers.get("Content-Encoding"))
                return ("ok", body,
                        resp.headers.get("ETag"),
                        resp.headers.get("Last-Modified"))
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return "not_modified", None, etag, last_modified
            # 4xx is a client problem: retrying will not fix it.
            if 400 <= e.code < 500 and e.code not in (408, 429):
                return "http_%d" % e.code, None, None, None
            last_exc = e
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            last_exc = e
        if attempt < config.RETRIES:
            time.sleep(delay)
            delay *= 2
    return "error", None, None, str(last_exc or "unknown")[:200]


def _passes_filter(feed_id, item):
    keywords = config.FEED_FILTERS.get(feed_id)
    if not keywords:
        return True
    hay = (item["title"] + " " + (item["summary"] or "")).lower()
    return any(k in hay for k in keywords)


def fetch_one(feed):
    """Fetch + parse one feed row. Returns a result dict (never raises)."""
    fid, url = feed["id"], feed["url"]
    res = {"id": fid, "name": feed["name"], "status": "error",
           "items": [], "etag": None, "last_modified": None, "error": None,
           "kept": 0, "seen": 0}
    sem = _sem(url)
    sem.acquire()
    try:
        status, body, etag, lm = http_get(url, feed["etag"], feed["last_modified"])
        res["status"], res["etag"], res["last_modified"] = status, etag, lm
        if status == "not_modified":
            return res
        if status != "ok":
            res["error"] = lm if status == "error" else status
            return res
        try:
            items = parse.parse(body, url)
        except parse.NotAFeed as e:
            res["status"] = "not_a_feed"
            res["error"] = str(e)
            return res

        res["seen"] = len(items)
        kept = []
        for it in items:
            if not it["title"] and not it["url"]:
                continue
            if not _passes_filter(fid, it):
                continue
            it["canon_url"] = canon.canon_url(it["url"])
            it["norm_title"] = canon.norm_title(it["title"])
            if not it["norm_title"]:
                continue
            kept.append(it)
            if len(kept) >= config.MAX_ITEMS_PER_FEED:
                break
        res["items"] = kept
        res["kept"] = len(kept)
        return res
    except Exception as e:  # a bad feed must not kill the run
        res["error"] = "%s: %s" % (type(e).__name__, e)
        return res
    finally:
        sem.release()


def fetch_all(feeds, workers=None, progress=None):
    """Fetch many feeds concurrently. Returns list of result dicts."""
    workers = workers or config.WORKERS
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, f): f for f in feeds}
        for done in as_completed(futures):
            r = done.result()
            results.append(r)
            if progress:
                progress(r, len(results), len(feeds))
    return results
