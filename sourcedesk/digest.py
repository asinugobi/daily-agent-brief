"""Render the daily brief from clustered items."""
from datetime import datetime, timezone

from . import config

UTC = timezone.utc

DOMAIN_LABEL = {
    "ai": "AI", "physical": "Physical AI",
    "econ": "Economics", "cross": "Cross-cutting",
}


def _dt(row):
    raw = row["published"] or row["first_seen"]
    try:
        d = datetime.fromisoformat(raw)
        return d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _stamp(row):
    d = _dt(row)
    return d.strftime("%d %b %H:%M") if d else "undated"


def _snippet(row, n=190):
    s = (row["summary"] or "").strip()
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0] + "..."


def render(store, limit=None, hours=None):
    limit = limit or config.TOP_CLUSTERS
    hours = hours or config.DIGEST_HOURS
    corroborated = store.top_clusters(config.TOP_CORROBORATED, min_outlets=2)
    singles = store.top_clusters(limit, max_outlets=1)
    rows = corroborated or singles
    now = datetime.now(UTC)

    out = []
    out.append("# Daily brief - AI, physical AI and automation economics")
    out.append("")
    out.append("_%s UTC - %d-hour window_" % (now.strftime("%A %d %B %Y, %H:%M"), hours))
    out.append("")

    if not rows:
        out.append("No items in the window. Run `fetch` first, or widen `--hours`.")
        return "\n".join(out)

    # "Corroborated" means independent publishers agreed - not that one document
    # reached us on several feeds. Cross-listings are collapsed, never counted.
    if corroborated:
        out.append("## Corroborated stories")
        out.append("")
        out.append("_Independent publishers on one event. The cited source is the "
                   "most authoritative in the cluster, not the first to publish. "
                   "Syndication and cross-listing are collapsed, not counted._")
        out.append("")
        for i, r in enumerate(corroborated, 1):
            out.extend(_render_cluster(store, r, i))

    if singles:
        out.append("## Single-source items")
        out.append("")
        out.append("_A single publisher. Treat as a lead to verify, not an "
                   "established fact - especially at tier 2 and below. Items here "
                   "may still have arrived on several feeds from that one publisher._")
        out.append("")
        for r in singles:
            dom = DOMAIN_LABEL.get(r["domain"], r["domain"])
            link = r["url"] or ""
            title = r["title"].replace("[", "(").replace("]", ")")
            head = "- **T%d** %s" % (r["tier"], ("[%s](%s)" % (title, link)) if link else title)
            out.append("%s  \n  %s - %s - %s" % (head, r["feed_name"], dom, _stamp(r)))
        out.append("")

    return "\n".join(out)


def _render_cluster(store, r, idx):
    lines = []
    title = r["title"].replace("[", "(").replace("]", ")")
    link = r["url"] or ""
    lines.append("### %d. %s" % (idx, ("[%s](%s)" % (title, link)) if link else title))
    dom = DOMAIN_LABEL.get(r["domain"], r["domain"])
    lines.append("**%s** - tier %d - %s - %s - %d publishers - score %.0f"
                 % (r["feed_name"], r["tier"], dom, _stamp(r), r["outlets"], r["score"]))
    snip = _snippet(r)
    if snip:
        lines.append("")
        lines.append("> " + snip)
    others = store.cluster_members(r["id"], r["id"])
    if others:
        seen, parts, same = {r["outlet"]}, [], 0
        for o in others:
            if o["outlet"] in seen:
                same += 1
                continue
            seen.add(o["outlet"])
            parts.append("%s (T%d)" % (o["feed_name"], o["tier"]))
        if parts:
            lines.append("")
            lines.append("_Also covered by:_ " + ", ".join(parts))
        if same:
            lines.append("")
            lines.append("_(+%d more feed(s) from the same publisher - not counted "
                         "as corroboration.)_" % same)
    lines.append("")
    return lines


def render_health(store):
    rows = store.health()
    out = ["# Feed health", ""]
    pollable = [r for r in rows if r["kind"] == "feed"]
    bad = [r for r in pollable if r["consecutive_errors"] > 0]
    ok = [r for r in pollable if r["consecutive_errors"] == 0 and r["last_status"]]
    never = [r for r in pollable if not r["last_status"]]

    out.append("Pollable feeds: %d healthy - %d failing - %d never fetched"
               % (len(ok), len(bad), len(never)))
    out.append("")
    if bad:
        out.append("## Failing")
        out.append("")
        for r in bad:
            out.append("- **%s** (T%d, %s) - %s x%d - %s"
                       % (r["name"], r["tier"], r["access"], r["last_status"],
                          r["consecutive_errors"], (r["last_error"] or "")[:110]))
        out.append("")
    for kind, title, blurb in (
        ("api", "Query APIs",
         "Not item streams - they answer questions. Each needs its own handler "
         "(XBRL frames, Federal Register search, BLS timeseries)."),
        ("bulk", "Bulk archives", "Periodic downloads, not polled."),
        ("page", "Needs a scraper",
         "No feed exists, or the publisher blocks automated clients. Not failures."),
    ):
        group = [r for r in rows if r["kind"] == kind]
        if not group:
            continue
        out.append("## %s (%d)" % (title, len(group)))
        out.append("")
        out.append("_%s_" % blurb)
        out.append("")
        for r in group:
            out.append("- %s (T%d, %s)" % (r["name"], r["tier"], r["access"]))
        out.append("")
    return "\n".join(out)
