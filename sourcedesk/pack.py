"""Write per-beat candidate packs for the brief agent's researchers.

One file per beat in the coverage map, holding the deduplicated sourcedesk
candidates routed to that beat. A researcher reads its own file first and then
searches for what the pack does not cover, which turns a blind sweep into a
verified starting point.

The pack lists leads, not findings. Every URL still has to be opened before it
is cited - that rule lives in the agent's system prompt and this does not
weaken it.
"""
import re
from datetime import datetime, timezone

from . import beats

UTC = timezone.utc


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _stamp(row):
    raw = row["published"] or row["first_seen"]
    try:
        d = datetime.fromisoformat(raw)
        d = d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)
        return d.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "undated"


def build(store, outdir, hours, limit=400, per_beat=25, max_research=5):
    """Write one markdown pack per beat. Returns {beat: count}."""
    outdir.mkdir(parents=True, exist_ok=True)
    rows = store.clusters_for_pack(limit=limit)

    buckets = {name: [] for name in beats.BEATS}
    research_used = {name: 0 for name in beats.BEATS}
    leftovers = []
    for r in rows:
        assigned = beats.assign(r)
        if not assigned:
            leftovers.append(r)
            continue
        for name in assigned:
            if len(buckets[name]) >= per_beat:
                continue
            # Keep literature from crowding news out of a topical beat.
            if (name != beats.RESEARCH_BEAT and beats.is_research(r)):
                if research_used[name] >= max_research:
                    continue
                research_used[name] += 1
            buckets[name].append(r)

    unrouted = len(leftovers)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    counts = {}
    for name, items in buckets.items():
        path = outdir / ("%s.md" % _slug(name))
        counts[name] = len(items)
        lines = [
            "# Sourcedesk pack: %s" % name,
            "",
            "_%d candidates, %d-hour window, generated %s._" % (len(items), hours, now),
            "",
            "These come from the curated source list: already deduplicated across",
            "feeds, tier-tagged, and ranked. Corroboration counts independent",
            "publishers, not feeds.",
            "",
            "**Use this as your starting point, not your answer.** Open anything you",
            "intend to cite - a pack line is a lead, exactly like a search snippet.",
            "Then run your own searches for what is missing here.",
            "",
        ]
        if not items:
            lines += ["_Nothing routed to this beat in the window. Search from scratch._", ""]
        for it in items:
            corr = ""
            if it["outlets"] > 1:
                others = store.cluster_members(it["cluster_id"], it["id"])
                names, seen = [], {it["outlet"]}
                for o in others:
                    if o["outlet"] in seen:
                        continue
                    seen.add(o["outlet"])
                    names.append("%s (T%d)" % (o["feed_name"], o["tier"]))
                if names:
                    corr = "  \n  corroborated by: %s" % ", ".join(names)
            summary = (it["summary"] or "").strip().replace("\n", " ")
            if len(summary) > 300:
                summary = summary[:300].rsplit(" ", 1)[0] + "..."
            lines.append(
                "- **%s**  \n  %s - tier %d - %s - %d publisher(s)  \n  %s%s%s"
                % (it["title"], it["feed_name"], it["tier"], _stamp(it),
                   it["outlets"], it["url"] or "(no url)",
                   ("  \n  " + summary) if summary else "", corr))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Nothing is dropped silently: what matched no beat still gets written, for
    # the synthesiser to scan. An item that fits no beat can still be the story.
    leftover_path = outdir / "unrouted.md"
    ll = ["# Sourcedesk pack: unrouted", "",
          "_%d candidates that matched no beat, %d-hour window, generated %s._"
          % (len(leftovers), hours, now), "",
          "Beat routing is keyword-driven and imperfect. These are real items it",
          "could not place. Scan them for anything the beats missed.", ""]
    for it in leftovers[:60]:
        ll.append("- **%s**  \n  %s - tier %d - %s  \n  %s"
                  % (it["title"], it["feed_name"], it["tier"], _stamp(it),
                     it["url"] or "(no url)"))
    leftover_path.write_text("\n".join(ll) + "\n", encoding="utf-8")

    index = outdir / "index.md"
    idx = ["# Sourcedesk packs", "",
           "_Generated %s from a %d-hour window._" % (now, hours), "",
           "| Beat | Candidates | File |", "|---|---|---|"]
    for name in beats.PRIORITY:
        idx.append("| %s | %d | `%s.md` |" % (name, counts.get(name, 0), _slug(name)))
    idx += ["", "| unrouted | %d | `unrouted.md` |" % unrouted, ""]
    index.write_text("\n".join(idx) + "\n", encoding="utf-8")
    counts["_unrouted"] = unrouted
    return counts
