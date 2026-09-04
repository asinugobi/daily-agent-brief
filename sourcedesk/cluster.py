"""Event clustering: collapse many articles about one event into one story.

Three merge signals, strongest first:
  1. identical canonical URL   - the same document syndicated to several feeds
  2. identical normalised title - the same headline, different URL
  3. title/summary token overlap above a threshold, within a time window

Within a cluster the *canonical* item is chosen by tier, not by who is loudest:
a company's own announcement outranks the wire report of it, which outranks the
newsletter analysing it. That is the whole point of the tier system - dedupe is
where it pays off, because the brief then cites the primary record and lists the
rest as corroboration.
"""
from datetime import datetime, timedelta, timezone

from . import canon, config

UTC = timezone.utc


def _similar(ta, tb, thr, shared):
    """Jaccard OR containment. See config for how these were tuned."""
    if canon.jaccard(ta, tb) >= thr:
        return True
    return shared >= config.MIN_SHARED and canon.overlap(ta, tb) >= config.OVERLAP


class _UF:
    def __init__(self, n):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.r[ra] < self.r[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        if self.r[ra] == self.r[rb]:
            self.r[ra] += 1
        return True


def _when(row):
    raw = row["published"] or row["first_seen"]
    if not raw:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return datetime.now(UTC)
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def build(rows, jaccard_threshold=None, window_hours=None):
    """rows: sqlite3.Row list. Returns list of cluster dicts."""
    thr = config.JACCARD if jaccard_threshold is None else jaccard_threshold
    win = timedelta(hours=window_hours if window_hours is not None
                    else config.CLUSTER_WINDOW_H)
    n = len(rows)
    if n == 0:
        return []

    uf = _UF(n)
    toks = [canon.tokens(r["title"], r["summary"] or "") for r in rows]
    times = [_when(r) for r in rows]

    # --- signal 1: identical canonical URL --------------------------------
    by_url = {}
    for i, r in enumerate(rows):
        cu = r["canon_url"]
        if not cu:
            continue
        if cu in by_url:
            uf.union(by_url[cu], i)
        else:
            by_url[cu] = i

    # --- signal 2: identical normalised title -----------------------------
    by_title = {}
    for i, r in enumerate(rows):
        nt = r["norm_title"]
        if not nt:
            continue
        if nt in by_title:
            uf.union(by_title[nt], i)
        else:
            by_title[nt] = i

    # --- signal 3: token overlap, time-bounded ----------------------------
    # Inverted index keeps this near-linear instead of comparing all pairs.
    postings = {}
    for i, ts in enumerate(toks):
        for t in ts:
            postings.setdefault(t, []).append(i)
    # A token present in almost everything carries no signal and explodes the
    # candidate set, so ignore the most common ones.
    ceiling = max(12, int(n * 0.18))

    for i in range(n):
        if len(toks[i]) < config.MIN_TOKENS:
            continue
        counts = {}
        for t in toks[i]:
            plist = postings.get(t, ())
            if len(plist) > ceiling:
                continue
            for j in plist:
                if j > i:
                    counts[j] = counts.get(j, 0) + 1
        for j, shared in counts.items():
            if shared < 2:
                continue
            if uf.find(i) == uf.find(j):
                continue
            if abs(times[i] - times[j]) > win:
                continue
            if _similar(toks[i], toks[j], thr, shared):
                uf.union(i, j)

    # --- assemble ---------------------------------------------------------
    groups = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    now = datetime.now(UTC)
    clusters = []
    for members in groups.values():
        canonical = _pick_canonical(rows, times, members)
        clusters.append({
            "canonical": rows[canonical]["id"],
            "members": [rows[i]["id"] for i in members],
            "outlets": len({rows[i]["outlet"] for i in members}),
            "score": _score(rows, times, members, canonical, now),
        })
    clusters.sort(key=lambda c: -c["score"])
    return clusters


def _pick_canonical(rows, times, members):
    """Lowest tier wins; then earliest; then the more informative headline."""
    def key(i):
        return (rows[i]["tier"], times[i], -len(rows[i]["title"] or ""))
    return min(members, key=key)


def _score(rows, times, members, canonical, now):
    """Breadth counts distinct OUTLETS, never feeds.

    One arXiv paper cross-listed to cs.AI, cs.LG and cs.MA arrives on three
    feeds and is still one document from one publisher - awarding it breadth
    would rank a routine preprint above a Fed decision carried by four
    independent newsrooms. Repetition is not corroboration.
    """
    tier = rows[canonical]["tier"]
    base = config.TIER_WEIGHT.get(tier, 30)
    if rows[canonical]["outlet"] in config.PREPRINT_OUTLETS:
        base = min(base, config.PREPRINT_WEIGHT)
    outlets = {rows[i]["outlet"] for i in members}
    domains = {rows[i]["domain"] for i in members}
    age_h = max(0.0, (now - times[canonical]).total_seconds() / 3600.0)
    return (base
            + config.BREADTH_POINTS * (len(outlets) - 1)
            + config.DOMAIN_SPREAD_POINTS * (len(domains) - 1)
            - config.AGE_PENALTY_PER_H * age_h)
