"""CLI: python3 -m sourcedesk <command>

  sync      load sources.json into the store
  fetch     conditional-GET every pollable feed, store new items
  pull      run the api handlers (EDGAR filings, XBRL frames, Federal Register)
  pack      write per-beat candidate packs into state/sourcedesk/
  cluster   deduplicate and group the recent window into events
  digest    print the daily brief
  run       fetch + pull + cluster + digest
  health    per-feed status, failures, and the manual queue

Typical daily job:   python3 -m sourcedesk run --hours 24 > brief.md
"""
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

from . import cluster as clusterer
from . import config, digest, fetch, handlers, miniyaml, pack
from .store import Store

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DEFAULT_SOURCES = HERE / "sources.json"
DEFAULT_DB = PROJECT / "state" / "sourcedesk.db"

UTC = timezone.utc


def _load_sources(path):
    if not os.path.exists(path):
        sys.exit("sources.json not found at %s - run sourcedesk/build_sources.py first." % path)
    with open(path) as fh:
        return json.load(fh)["feeds"]


def cmd_sync(args, store):
    feeds = _load_sources(args.sources)
    store.sync_feeds(feeds)
    from collections import Counter
    kinds = Counter(f.get("kind", "feed") for f in feeds)
    pollable = [f for f in feeds
                if f["access"] in config.FETCHABLE and f.get("kind") == "feed"]
    print("synced %d sources: %d pollable feeds | %d query APIs | %d bulk | "
          "%d pages needing a scraper"
          % (len(feeds), len(pollable), kinds["api"], kinds["bulk"], kinds["page"]),
          file=sys.stderr)


def cmd_fetch(args, store):
    cmd_sync(args, store)
    feeds = store.feeds(access=tuple(config.FETCHABLE), kind=("feed",))
    if args.only:
        wanted = set(args.only.split(","))
        feeds = [f for f in feeds if f["id"] in wanted]
    if not feeds:
        sys.exit("no pollable feeds selected")

    run_id = store.start_run()
    counts = {"ok": 0, "not_modified": 0, "failed": 0, "new": 0, "not_a_feed": 0}

    def progress(r, done, total):
        if args.quiet:
            return
        mark = {"ok": "+", "not_modified": "=", "not_a_feed": "?"}.get(r["status"], "!")
        print("  [%3d/%d] %s %-42s %s" % (done, total, mark, r["id"][:42],
                                          r["status"]), file=sys.stderr)

    print("fetching %d feeds with %d workers (%d per host)..."
          % (len(feeds), config.WORKERS, config.PER_HOST), file=sys.stderr)
    results = fetch.fetch_all(feeds, progress=progress)

    for r in results:
        if r["status"] == "ok":
            n = store.add_items(r["id"], r["items"])
            counts["ok"] += 1
            counts["new"] += n
            store.update_feed_state(r["id"], "ok", r["etag"], r["last_modified"],
                                    new_items=n)
        elif r["status"] == "not_modified":
            counts["not_modified"] += 1
            store.update_feed_state(r["id"], "not_modified")
        elif r["status"] == "not_a_feed":
            counts["not_a_feed"] += 1
            store.update_feed_state(r["id"], "not_a_feed", error=r["error"])
        else:
            counts["failed"] += 1
            store.update_feed_state(r["id"], r["status"], error=r["error"])

    store.finish_run(run_id, fetched=counts["ok"], not_modified=counts["not_modified"],
                     failed=counts["failed"], new_items=counts["new"])
    print("fetched %d - not modified %d - not a feed %d - failed %d - new items %d"
          % (counts["ok"], counts["not_modified"], counts["not_a_feed"],
             counts["failed"], counts["new"]), file=sys.stderr)


def load_agent_config():
    """Share the agent's own watchlists rather than keeping a second copy."""
    path = PROJECT / "config.yaml"
    if not path.exists():
        return {}
    try:
        return miniyaml.load(path)
    except Exception as e:
        print("  could not read config.yaml (%s); using handler defaults" % e,
              file=sys.stderr)
        return {}


def cmd_pull(args, store):
    """Run the api-kind handlers - EDGAR, Federal Register - and store items."""
    cfg = load_agent_config()
    only = set(args.only.split(",")) if getattr(args, "only", None) else None
    print("pulling api sources (EDGAR, Federal Register)...", file=sys.stderr)
    produced = handlers.run_all(cfg, only=only)
    total = 0
    for fid, items in produced.items():
        n = store.add_items(fid, items)
        total += n
        store.update_feed_state(fid, "ok", new_items=n)
        if not args.quiet:
            print("  %-44s %3d returned, %3d new" % (fid, len(items), n),
                  file=sys.stderr)
    print("api pull: %d new items across %d endpoints" % (total, len(produced)),
          file=sys.stderr)


def cmd_cluster(args, store):
    cutoff = (datetime.now(UTC) - timedelta(hours=args.hours)).isoformat()
    rows = store.items_since(cutoff)
    if not rows:
        print("no items in the last %dh" % args.hours, file=sys.stderr)
        store.replace_clusters([])
        return
    clusters = clusterer.build(rows, args.jaccard, args.window)
    store.replace_clusters(clusters)
    multi = sum(1 for c in clusters if c["outlets"] > 1)
    collapsed = len(rows) - len(clusters)
    print("clustered %d items into %d events (%d multi-publisher, %d duplicates collapsed)"
          % (len(rows), len(clusters), multi, collapsed), file=sys.stderr)


def cmd_digest(args, store):
    print(digest.render(store, limit=args.limit, hours=args.hours))


def cmd_health(args, store):
    print(digest.render_health(store))


def cmd_pack(args, store):
    """Write per-beat candidate packs the brief agent's researchers read."""
    outdir = PROJECT / "state" / "sourcedesk"
    counts = pack.build(store, outdir, hours=args.hours, per_beat=args.per_beat)
    unrouted = counts.pop("_unrouted", 0)
    total = sum(counts.values())
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("  %-34s %3d" % (name, n), file=sys.stderr)
    print("packed %d beat slots (%d clustered items unrouted) -> %s"
          % (total, unrouted, outdir), file=sys.stderr)


def cmd_run(args, store):
    cmd_fetch(args, store)
    if not getattr(args, "no_apis", False):
        cmd_pull(args, store)
    cmd_cluster(args, store)
    if getattr(args, "pack", False):
        cmd_pack(args, store)
    cmd_digest(args, store)


def main(argv=None):
    p = argparse.ArgumentParser(prog="sourcedesk", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--sources", default=str(DEFAULT_SOURCES))
    sub = p.add_subparsers(dest="cmd")

    def add(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(fn=fn)
        return sp

    add("sync", cmd_sync, help="load sources.json into the store")

    sp = add("pack", cmd_pack, help="write per-beat candidate packs for the agent")
    sp.add_argument("--hours", type=int, default=config.DIGEST_HOURS)
    sp.add_argument("--per-beat", type=int, default=25)

    sp = add("pull", cmd_pull, help="run api handlers (EDGAR, Federal Register)")
    sp.add_argument("--only", help="comma-separated feed ids")
    sp.add_argument("--quiet", action="store_true")

    for name, fn in (("fetch", cmd_fetch), ("run", cmd_run)):
        sp = add(name, fn, help="poll feeds" if name == "fetch" else "fetch + cluster + digest")
        sp.add_argument("--only", help="comma-separated feed ids")
        sp.add_argument("--quiet", action="store_true")
        if name == "run":
            sp.add_argument("--hours", type=int, default=config.DIGEST_HOURS)
            sp.add_argument("--jaccard", type=float, default=None)
            sp.add_argument("--window", type=int, default=None)
            sp.add_argument("--limit", type=int, default=config.TOP_CLUSTERS)
            sp.add_argument("--no-apis", action="store_true",
                            help="skip the EDGAR / Federal Register handlers")
            sp.add_argument("--pack", action="store_true",
                            help="also write per-beat packs for the brief agent")
            sp.add_argument("--per-beat", type=int, default=25)

    sp = add("cluster", cmd_cluster, help="deduplicate and group into events")
    sp.add_argument("--hours", type=int, default=config.DIGEST_HOURS)
    sp.add_argument("--jaccard", type=float, default=None)
    sp.add_argument("--window", type=int, default=None)

    sp = add("digest", cmd_digest, help="print the daily brief")
    sp.add_argument("--hours", type=int, default=config.DIGEST_HOURS)
    sp.add_argument("--limit", type=int, default=config.TOP_CLUSTERS)

    add("health", cmd_health, help="per-feed status and the manual queue")

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 0

    if config.CONTACT_EMAIL == "set-me@example.com":
        print("warning: SD_CONTACT_EMAIL is unset - SEC EDGAR www paths will 403.",
              file=sys.stderr)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    try:
        args.fn(args, store)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
