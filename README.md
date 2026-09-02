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

## Setup

Once:

    brew install python@3.12
    ./setup.sh
    export ANTHROPIC_API_KEY=sk-ant-...

Get the key at https://platform.claude.com. The SDK reads it from the shell
environment and does not load `.env` files on its own.

## Run

    ./.venv/bin/python agent.py

Flags:

- `--mode weekly|monthly|earnings-week` overrides `run_mode` in config for one run
- `--budget 8` raises the spend cap, default 5 USD
- `--cron` runs once and exits without the follow-up prompt

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

    30 6 * * 1-5 cd ~/daily-brief-agent && ANTHROPIC_API_KEY=sk-ant-... ./.venv/bin/python agent.py --cron >> state/cron.log 2>&1

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
