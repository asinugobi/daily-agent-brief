# Daily Brief Agent

An autonomous research analyst that sweeps nine beats in parallel, verifies every
source it cites, and writes a dated brief to `./briefs/`.

## Layout

| File | What it is |
|---|---|
| `system-prompt.md` | The agent's instructions. The main thing to edit. |
| `config.yaml` | Watchlists, mode, lookback, thresholds. Read fresh on every run. |
| `agent.py` | The runner: options, subagent definition, streaming loop. |
| `state/open-loops.md` | Running ledger of dated claims. Agent rewrites it each run. |
| `state/covered.jsonl` | Every item ever published, for de-duplication. |
| `state/runs.jsonl` | One line per run: candidates, published, failures, cost. |
| `briefs/` | Output, one file per day. |
| `sourcedesk/` | Curated 120-source list plus the fetch, dedupe and pack pipeline. |
| `state/sourcedesk/` | Per-beat candidate packs, rewritten each run. |

## Setup

Once:

    brew install python@3.12
    ./setup.sh
    export ANTHROPIC_API_KEY=sk-ant-...

Get the key at https://platform.claude.com. The SDK reads it from the shell
environment and does not load `.env` files on its own.

## Sourcedesk

Before the sweep, a pre-pass fetches a curated list of 120 endpoint-verified
sources, deduplicates them into events, and writes one candidate pack per beat
into `state/sourcedesk/`. Each researcher reads its own pack first, then
searches for what the pack does not cover. Blind search becomes a verified
starting point.

Set a contact address before running, because SEC EDGAR returns 403 to every
`www.sec.gov` request without one:

    export SD_CONTACT_EMAIL=you@yourdomain.com

The pipeline is stdlib-only, so it adds no dependencies. It can be driven on its
own:

    ./.venv/bin/python -m sourcedesk run --hours 24 --pack
    ./.venv/bin/python -m sourcedesk health     # feed status, API and scraper queues

`sourcedesk/build_sources.py` is the single source of truth for the list. Edit
it, then re-run it; never edit `sources.json` or `sources.yaml` directly.

Three things worth knowing about the packs:

- **A pack line is a lead, not a source.** The fetch-before-citing rule applies
  to it exactly as to a search snippet.
- **Corroboration counts publishers, not feeds.** One paper cross-listed to
  three arXiv categories is one source, not three.
- **Packs cover feeds only.** They systematically miss paywalled reporting and
  the 25 listed sources that publish no feed at all, so search and `./inbox/`
  still carry those.

Skip the pre-pass for one run with `--no-sourcedesk`.

## Run

    ./.venv/bin/python agent.py

Flags:

- `--mode weekly|monthly|earnings-week` overrides `run_mode` in config for one run
- `--budget 8` raises the spend cap, default 5 USD
- `--cron` runs once and exits without the follow-up prompt
- `--no-sourcedesk` skips the pre-pass; researchers work from search alone

After the brief is written, the session stays open for `/deeper`, `/thesis check`,
`/explain`, `/interview`, `/build`, and `/quiet`. Blank line quits.

## Cost control

Every run spawns nine researcher subagents, each doing several searches and fetches.
Three limits bound this:

- `max_budget_usd` in `agent.py`, a hard stop enforced across subagent spend
- `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=6`, so at most six run at once
- `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`, so researchers cannot spawn their own

Researchers run Sonnet; only the synthesizer runs Opus. Watch the first few runs,
then check `state/runs.jsonl` to see what a normal run costs before automating it.

## Scheduling

Once a few manual runs look right:

    crontab -e

Then, for 6:30am on weekdays:

    30 6 * * 1-5 cd ~/daily-brief-agent && ANTHROPIC_API_KEY=sk-ant-... SD_CONTACT_EMAIL=you@yourdomain.com ./.venv/bin/python agent.py --cron >> state/cron.log 2>&1

cron runs with a minimal environment, which is why the key and the absolute
interpreter path are spelled out.

## Tuning

- Brief too thin or too noisy: change `score_threshold` in `config.yaml`. The
  `Filtered out` line reports candidates gathered versus published, which is the
  signal for which way to move it.
- Too expensive: cut beats from the coverage map in `system-prompt.md`, or drop
  `max_items`.
- Citing things it did not read: that rule is in section 0 of the system prompt
  and overrides everything else. Strengthen it there, not in the runner.
