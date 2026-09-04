# Source Desk

Fetch, deduplicate and brief across 120 verified sources covering AI, physical AI
and automation economics.

**Stdlib only.** No pip install, no virtualenv, Python 3.9+. A daily job that
needs a dependency tree is a daily job that breaks.

## Layout

| File | Role |
|---|---|
| `build_sources.py` | **Single source of truth.** Edit this, never the outputs. |
| `patch_page.py` | Syncs `source-desk.html` to the data. Idempotent. |
| `sources.yaml` | Human-readable source list (generated) |
| `sources.json` | Pipeline input, deduplicated by URL (generated) |
| `sourcedesk/` | The fetch + dedupe + digest pipeline |

## Quick start

```bash
export SD_CONTACT_EMAIL="you@yourdomain.com"   # required: SEC 403s without it
python3 build_sources.py
python3 -m sourcedesk run --hours 24 > brief.md
```

## Commands

```bash
python3 -m sourcedesk sync                    # load sources.json into the store
python3 -m sourcedesk fetch                   # conditional-GET every pollable feed
python3 -m sourcedesk cluster --hours 72      # deduplicate into events
python3 -m sourcedesk digest --limit 25       # print the brief
python3 -m sourcedesk health                  # feed status + manual queue
python3 -m sourcedesk run --hours 24          # all three
```

Tune anything in `config.py` with an `SD_`-prefixed env var:
`SD_JACCARD=0.5`, `SD_WORKERS=20`, `SD_CLUSTER_WINDOW_H=48`.

## How the source list splits

118 unique endpoints from 120 listed rows (two are deliberately cross-listed).
They are not all feeds, and pretending otherwise produces phantom failures:

| kind | n | handling |
|---|---|---|
| `feed` | 83 | polled with conditional GET |
| `api` | 9 | query endpoints (XBRL frames, Federal Register, BLS) — need bespoke handlers |
| `bulk` | 1 | quarterly ZIP archive |
| `page` | 25 | no feed exists, or the publisher blocks bots — needs a scraper |

`health` lists each group. The 25 pages are not errors; they are the known cost
of including sources like IEA and Stanford HAI that publish no feed at all.

## Dedupe: three signals, then one rule

1. **Identical canonical URL** — the same document syndicated to several feeds.
   URLs are normalised: tracking params dropped, AMP suffixes stripped, host
   lowercased, `www.` removed.
2. **Identical normalised title** — same headline, different URL. Publisher
   suffixes (`| The Verge`) are stripped first.
3. **Token overlap** — `jaccard >= 0.45` OR `(overlap >= 0.50 AND >= 3 shared
   tokens)`, within a 72-hour window.

Thresholds were tuned on labelled headline pairs including **hard negatives** —
same entity, different event ("Fed holds rates" vs "Fed minutes show divide"),
which is where naive thresholds actually fail. The setting gives 5/6 recall with
0/8 false merges. `MIN_SHARED=3` is load-bearing: at 2 it merged "Figure unveils
humanoid" with "Agility deploys humanoid".

Publishers disagree about how to write the same entity, which silently splits
clusters, so phrases are aliased before tokenising: `federal reserve`→`fed`,
`data center`→`datacenter`, `driverless`→`autonomous`. This lifted the Fed pair
from 0.40 to 0.62.

**Under-merging is the safer failure.** You show a story twice rather than
assert two events were one.

## Two rules that make the brief trustworthy

**The canonical source is chosen by tier, not by who published first.** A
company's own announcement outranks the wire report of it, which outranks the
newsletter analysing it. The brief cites the primary record and lists the rest
as corroboration.

**Corroboration counts publishers, not feeds.** One arXiv paper cross-listed to
`cs.AI`, `cs.LG` and `cs.MA` arrives on three feeds and is still one document
from one publisher. Counting feeds ranked routine preprints above stories four
newsrooms carried independently. Breadth uses the registrable domain, and the
brief says explicitly when extra feeds came from the same publisher.

Related: tier and newsworthiness are **different axes**. A preprint is a genuine
primary document (tier 1 for citation) but a research signal rather than an
event, and arXiv ships hundreds a day — so preprints are ranked between reported
news and commentary. The tier shown in the brief is unchanged.

## Volume control

arXiv alone publishes ~270 cs.AI papers a day. `MAX_ITEMS_PER_FEED` caps intake
and `FEED_FILTERS` in `config.py` keyword-filters the high-volume feeds so the
cap is not just arbitrary truncation.

## Politeness and caching

- Conditional GET via stored `ETag` / `Last-Modified`. In testing the second run
  returned **57 of 83 feeds as 304 Not Modified, cutting the run from 63s to 17s**.
- Max 2 concurrent requests per host.
- Retries with doubling backoff on 5xx and network errors; **never on 4xx**,
  which retrying cannot fix.
- One User-Agent carrying a contact address, as SEC requires.

## Known gaps

- `page` sources need a scraper per site; none is included.
- `api` sources need bespoke handlers — an XBRL frame is not an item stream.
- The BLS API needs POST; polling it with GET returns 405 by design.
- Clustering is lexical. Two headlines describing one event with no shared
  vocabulary will not merge.
