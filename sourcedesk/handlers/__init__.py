"""Handlers for api-kind sources.

A feed is polled; an API is asked a question. These modules turn the second into
the same item shape the feed path produces, so everything downstream -
deduplication, clustering, the brief - treats them identically.
"""
from . import edgar, federal_register

# feed id -> module providing run(cfg) -> {feed_id: [items]}
PROVIDERS = (edgar, federal_register)


def run_all(cfg, only=None):
    """Run every provider. Returns {feed_id: [items]}."""
    out = {}
    for mod in PROVIDERS:
        try:
            produced = mod.run(cfg) or {}
        except Exception as e:  # one bad handler must not kill the pull
            print("  handler %s failed: %s: %s" % (mod.__name__, type(e).__name__, e))
            continue
        for fid, items in produced.items():
            if only and fid not in only:
                continue
            out.setdefault(fid, []).extend(items)
    return out
