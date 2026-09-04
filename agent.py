#!/usr/bin/env python3
"""Daily AI, Markets, and Geopolitics Brief.

Auth: uses ANTHROPIC_API_KEY if set. Otherwise falls back to the Claude Code
credentials already on this machine, via the CLI the SDK bundles.

Usage:
    ./.venv/bin/python agent.py                  run, show progress, then follow-ups
    ./.venv/bin/python agent.py --mode weekly    override run_mode for this run
    ./.venv/bin/python agent.py --cron           plain output, no bar, no follow-ups
    ./.venv/bin/python agent.py --budget 8       spend cap, only applies to API-key auth
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

PROJECT = Path(__file__).parent.resolve()
EXPECTED_BEATS = 9  # coverage map in system-prompt.md section 3
PACK_DIR = PROJECT / "state" / "sourcedesk"

BEAT_RESEARCHER = AgentDefinition(
    description=(
        "Researches one news beat over a lookback window and returns scored, "
        "fetch-verified candidate items. Use one per beat, all dispatched together."
    ),
    prompt="""You research a single news beat and return scored candidates. You do not write prose.

Your caller gives you: a beat name, what counts for that beat, a lookback window, watchlists,
a mute list, and headlines already published in past briefs.

Procedure:
1. Read your sourcedesk pack first, at the path your caller gives you. It holds candidates
   already pulled from a curated, endpoint-verified source list: deduplicated across feeds,
   tier-tagged, and marked with how many independent publishers carried each one. It is a
   head start, not an answer, and it is deliberately incomplete. If the file is missing or
   empty, say so in your return and work from search alone.
2. Run several distinct WebSearch queries for your beat, aimed at what the pack does not
   already cover. Vary the phrasing. One query is not a sweep.
3. Open every promising result with WebFetch, pack lines included. A pack line is a lead
   exactly like a search snippet: you have not read it until you fetch it.
4. Drop anything on the mute list, anything already covered without a material update, and
   anything outside the lookback window.
5. Score what survives and return at most six items. Pack provenance earns no free points:
   score a packed item on the same four axes as anything you found yourself.

Scoring, 1 to 5 each:
- materiality: does this change a number, a plan, or a constraint
- proximity: does it touch the listed beats, watchlists, or the AI power and electrification thesis
- novelty: genuinely new, or a rehash of something already circulating
- source_quality: tier 1 primary is 5, strong tier 2 is 4, tier 3 alone is 2

Return exactly this YAML and nothing else. No preamble, no summary, no commentary:

```yaml
beat: <beat name>
searches_run: <count>
candidates:
  - headline: <plain description in your own words>
    url: <the URL you actually opened>
    published: <YYYY-MM-DD or unknown>
    tier: 1 | 2 | 3
    fetched: yes | no
    facts: <2 to 4 sentences, numbers only, no interpretation>
    materiality: <1-5>
    proximity: <1-5>
    novelty: <1-5>
    source_quality: <1-5>
```

If a fetch failed, still list the item with `fetched: no` so the caller can decide.
If the beat is genuinely empty for this window, return `candidates: []`. Do not manufacture items.""",
    tools=["WebSearch", "WebFetch", "Read"],
    model="sonnet",
    # Searching, fetching and scoring is mechanical work. Low effort and a turn cap
    # keep nine parallel researchers from dominating the run's cost.
    effort="low",
    maxTurns=16,
)

TRIGGER = """Run the brief.

Follow the run procedure in your system prompt exactly, starting with the date check.

A sourcedesk pre-pass has already run. It fetched the curated source list, deduplicated it
into events, and wrote one candidate pack per beat to ./state/sourcedesk/. Read
./state/sourcedesk/index.md first to see how many candidates each beat got, then read
./state/sourcedesk/unrouted.md yourself: it holds real items that matched no beat, and an
item that fits no beat can still be the story.

Dispatch the beat-researcher subagent once per beat in your coverage map, all in a single
batch so they run in parallel. Give each one everything it needs in the Agent prompt, since
subagents inherit none of this conversation - including the path to its own pack file,
which is ./state/sourcedesk/<beat name slugified>.md. A beat whose pack is empty is not a
quiet beat; it means the curated list had nothing and search matters more there.

Then rank, verify, write ./briefs/<today>.md, and update the state files."""


# ------------------------------------------------------------------ sourcedesk


def sourcedesk_prepass(quiet: bool, hours: int | None = None) -> str:
    """Fetch, deduplicate and pack the curated source list before the sweep.

    Gives every researcher a verified starting point instead of a blind search.
    Deliberately non-fatal: if the network is down or a feed misbehaves, the
    agent still runs on search alone, because a stale pack is worth less than a
    brief that never gets written.
    """
    if hours is None:
        try:
            from sourcedesk import miniyaml
            cfg = miniyaml.load(PROJECT / "config.yaml")
            monday = date.today().weekday() == 0
            hours = int(cfg.get("monday_lookback_hours" if monday
                                else "lookback_hours") or 24)
        except Exception:
            hours = 24

    cmd = [sys.executable, "-m", "sourcedesk", "run",
           "--hours", str(hours), "--pack", "--quiet", "--limit", "1"]
    try:
        out = subprocess.run(cmd, cwd=str(PROJECT), capture_output=True,
                             text=True, timeout=420)
    except subprocess.SubprocessError as e:
        return f"sourcedesk skipped ({type(e).__name__}); researchers will search only"

    lines = [ln for ln in (out.stderr or "").splitlines() if ln.strip()]
    summary = next((ln for ln in lines if ln.startswith("packed ")), "")
    fetched = next((ln for ln in lines if ln.startswith("fetched ")), "")
    pulled = next((ln for ln in lines if ln.startswith("api pull")), "")
    if out.returncode != 0 and not summary:
        tail = lines[-1] if lines else "no output"
        return f"sourcedesk failed ({tail}); researchers will search only"
    return "  ·  ".join(x for x in (fetched, pulled, summary) if x)


# ----------------------------------------------------------------------------- auth


def auth_mode() -> tuple[str, bool]:
    """Return (human description, usable).

    Asks the Claude CLI directly, which is authoritative across every auth method
    (subscription, Console, SSO, API key). Falls back to reading the credentials
    file only if the CLI is not on PATH.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY", True

    cli = shutil.which("claude")
    if cli:
        try:
            out = subprocess.run(
                [cli, "auth", "status"], capture_output=True, text=True, timeout=20
            )
            status = json.loads(out.stdout)
            if status.get("loggedIn"):
                method = status.get("authMethod", "claude.ai")
                return f"Claude Code login ({method})", True
            return "logged out", False
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            pass  # fall through to the file check

    creds = Path.home() / ".claude" / ".credentials.json"
    if creds.exists():
        try:
            oauth = json.loads(creds.read_text()).get("claudeAiOauth") or {}
            exp_ms = oauth.get("expiresAt")
            if exp_ms and exp_ms / 1000 < time.time():
                when = time.strftime("%Y-%m-%d", time.localtime(exp_ms / 1000))
                return f"Claude Code login EXPIRED {when}", False
            return "Claude Code login", True
        except (json.JSONDecodeError, OSError):
            pass

    return "none found", False


# ------------------------------------------------------------------------- progress


class Bar:
    """Live single-line progress display. Falls back to plain lines when not a TTY."""

    WIDTH = 22

    def __init__(self, total_beats: int, live: bool) -> None:
        self.total = total_beats
        self.live = live
        self.start = time.monotonic()
        self.phase = "orienting"
        self.dispatched = 0
        self.done = 0
        self.searches = 0
        self.fetches = 0
        self._shown = False
        self._last_draw = 0.0

    # -- helpers

    def elapsed(self) -> str:
        s = int(time.monotonic() - self.start)
        return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"

    def _render(self) -> str:
        total = max(self.total, self.dispatched)
        frac = (self.done / total) if total else 0.0
        if self.phase in ("synthesizing", "writing", "done"):
            frac = 1.0
        filled = int(frac * self.WIDTH)
        bar = "█" * filled + "░" * (self.WIDTH - filled)
        bits = [f"[{self.phase:<13}]", bar]
        if self.dispatched:
            bits.append(f"{self.done}/{total} beats")
        if self.searches or self.fetches:
            bits.append(f"{self.searches} searches, {self.fetches} fetches")
        bits.append(self.elapsed())
        return "  " + "  ".join(bits)

    # -- public

    def draw(self, force: bool = False) -> None:
        if not self.live:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < 0.4:
            return
        self._last_draw = now
        sys.stdout.write("\r\033[K" + self._render())
        sys.stdout.flush()
        self._shown = True

    def clear(self) -> None:
        if self.live and self._shown:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            self._shown = False

    def log(self, line: str) -> None:
        """Print a line above the bar."""
        self.clear()
        print(f"  {self.elapsed():>6}  {line}", flush=True)
        self.draw(force=True)

    async def ticker(self) -> None:
        while True:
            await asyncio.sleep(1)
            self.draw()


# ------------------------------------------------------------------------- options


def build_options(budget: float) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=(PROJECT / "system-prompt.md").read_text(),
        cwd=str(PROJECT),
        model="claude-opus-5",
        agents={"beat-researcher": BEAT_RESEARCHER},
        # Listing a tool pre-approves it, which is what makes unattended runs possible.
        # "Agent" must be present or the subagents can never be spawned.
        allowed_tools=[
            "Read", "Write", "Edit", "Glob", "Grep",
            "Bash", "WebSearch", "WebFetch", "Agent",
        ],
        permission_mode="acceptEdits",
        # Only meaningful under API-key auth; inert on subscription auth.
        max_budget_usd=budget,
        # Python merges env into the inherited environment, so PATH survives.
        env={
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "9",
        },
    )


def preflight() -> None:
    problems = []
    for f in ("system-prompt.md", "config.yaml"):
        if not (PROJECT / f).exists():
            problems.append(f"missing {f}")
    for d in ("state", "briefs"):
        (PROJECT / d).mkdir(exist_ok=True)
    for f in ("state/covered.jsonl", "state/runs.jsonl"):
        (PROJECT / f).touch(exist_ok=True)

    # EDGAR 403s every www.sec.gov path without a contact address in the
    # User-Agent. Not fatal: feeds and the other handlers still work, so warn
    # rather than block the run.
    if not os.environ.get("SD_CONTACT_EMAIL"):
        print("  note: SD_CONTACT_EMAIL is unset, so the SEC EDGAR pulls will be")
        print("        skipped. Set it to a contact address SEC can reach:")
        print("        export SD_CONTACT_EMAIL=you@yourdomain.com\n")

    mode, ok = auth_mode()
    if not ok:
        if "EXPIRED" in mode or mode == "logged out":
            problems.append(
                f"Claude Code is not authenticated ({mode}).\n"
                "      Log in again:  claude auth login\n"
                "      Verify:        claude auth status\n"
                "      Alternatively set ANTHROPIC_API_KEY for Console billing."
            )
        else:
            problems.append(
                "no credentials found.\n"
                "      Either run `claude` once to log in, or set ANTHROPIC_API_KEY\n"
                "      (key: https://platform.claude.com/settings/keys)"
            )
    if problems:
        bar = "=" * 66
        print(f"\n{bar}\n  CANNOT START\n{bar}")
        for p in problems:
            print(f"  - {p}")
        print(bar + "\n")
        sys.exit(1)


def describe(name: str, args: dict) -> str:
    args = args or {}
    if name in ("Agent", "Task"):
        return f"dispatch  {args.get('description') or args.get('subagent_type', '?')}"
    if name == "WebSearch":
        return f"search    {args.get('query', '')[:64]}"
    if name == "WebFetch":
        return f"fetch     {args.get('url', '')[:64]}"
    if name == "Bash":
        return f"bash      {(args.get('command') or '')[:56]}"
    if name in ("Read", "Write", "Edit"):
        return f"{name.lower():<9} {args.get('file_path', '')}"
    return name


# ---------------------------------------------------------------------------- loop


async def drain(client: ClaudeSDKClient, bar: Bar) -> str:
    """Stream one exchange. Show progress live, return the final text."""
    agent_calls: set[str] = set()
    text_parts: list[str] = []

    try:
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                # Only surface the parent's activity; subagent chatter stays inside.
                inner = getattr(message, "parent_tool_use_id", None)
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        if block.name in ("Agent", "Task"):
                            agent_calls.add(block.id)
                            bar.dispatched += 1
                            bar.phase = "researching"
                            bar.log(describe(block.name, block.input))
                        elif block.name == "WebSearch":
                            bar.searches += 1
                        elif block.name == "WebFetch":
                            bar.fetches += 1
                        elif block.name in ("Write", "Edit") and not inner:
                            bar.phase = "writing"
                            bar.log(describe(block.name, block.input))
                        elif not inner:
                            bar.log(describe(block.name, block.input))
                        bar.draw()
                    elif hasattr(block, "text") and not inner:
                        text_parts.append(block.text)

            elif isinstance(message, UserMessage):
                for block in message.content if isinstance(message.content, list) else []:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id in agent_calls:
                        bar.done += 1
                        if bar.done >= bar.dispatched >= EXPECTED_BEATS:
                            bar.phase = "synthesizing"
                        bar.log(f"returned  beat {bar.done}/{max(bar.total, bar.dispatched)}")
                bar.draw()

            elif isinstance(message, ResultMessage):
                bar.phase = "done"
                bar.draw(force=True)
                bar.clear()
                cost = getattr(message, "total_cost_usd", None)
                tail = f" · ${cost:.2f}" if cost else ""
                print(f"\n--- {message.subtype} in {bar.elapsed()}{tail}\n")
                if message.subtype == "error_max_budget_usd":
                    print("    Budget cap hit. Raise it with --budget, or trim the coverage map.\n")
                if getattr(message, "result", None):
                    return message.result

    except Exception as err:
        bar.clear()
        print(f"\n--- run failed after {bar.elapsed()}")
        print(f"    {type(err).__name__}: {err}")
        low = str(err).lower()
        if any(k in low for k in ("auth", "api key", "credit", "401", "403")):
            print("    Auth or billing problem. Run `claude` once to confirm your login works,")
            print("    or set ANTHROPIC_API_KEY and add credits at")
            print("    https://platform.claude.com/settings/billing")
        return ""

    return "".join(text_parts)


async def exchange(client: ClaudeSDKClient, prompt: str, live: bool) -> None:
    bar = Bar(EXPECTED_BEATS, live)
    ticker = asyncio.create_task(bar.ticker()) if live else None
    try:
        await client.query(prompt)
        final = await drain(client, bar)
    finally:
        if ticker:
            ticker.cancel()
        bar.clear()
    if final.strip():
        print(final)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly", "monthly", "earnings-week"])
    ap.add_argument("--cron", action="store_true", help="plain output, run once, exit")
    ap.add_argument("--budget", type=float, default=5.0, help="spend cap, API-key auth only")
    ap.add_argument("--no-sourcedesk", action="store_true",
                    help="skip the sourcedesk pre-pass; researchers search only")
    args = ap.parse_args()

    preflight()
    live = sys.stdout.isatty() and not args.cron
    mode, _ = auth_mode()

    desk = ""
    if not args.no_sourcedesk:
        print("Sourcedesk pre-pass...", flush=True)
        desk = sourcedesk_prepass(quiet=args.cron)

    prompt = TRIGGER
    if args.mode:
        prompt += f"\n\nRUN_MODE override for this run: {args.mode}. Ignore run_mode in config.yaml."

    print(f"Daily brief agent  ·  opus-5 synthesizer, sonnet researchers")
    print(f"Auth: {mode}")
    print(f"Project: {PROJECT}")
    if desk:
        print(f"Sourcedesk: {desk}")
    print()

    try:
        async with ClaudeSDKClient(options=build_options(args.budget)) as client:
            await exchange(client, prompt, live)

            if not args.cron:
                print("Follow-ups: /deeper <item>, /thesis check, /explain <term>, "
                      "/interview, /build, /quiet. Blank line to quit.\n")
                while True:
                    try:
                        line = (await asyncio.to_thread(input, "> ")).strip()
                    except (EOFError, KeyboardInterrupt):
                        break
                    if not line:
                        break
                    await exchange(client, line, live)
    finally:
        # Runs even when the session ends on a budget cap or an SDK teardown error,
        # which is exactly when the agent did not get to do it itself.
        finish(args)


def finish(args: argparse.Namespace) -> None:
    today = PROJECT / "briefs" / f"{date.today():%Y-%m-%d}.md"
    if not today.exists():
        print("No brief was written today.")
        return

    lint = subprocess.run([sys.executable, str(PROJECT / "lint.py"), "--fix", str(today)],
                          capture_output=True, text=True)
    if lint.stdout.strip():
        print(lint.stdout.rstrip())

    subprocess.run([sys.executable, str(PROJECT / "render.py"), str(today)],
                   capture_output=True)
    page = today.with_suffix(".html")
    print(f"HTML: {page}")

    # The agent appends its own run record at the end of a clean run. When it was cut
    # short, write a minimal one so the log has no gaps.
    runs = PROJECT / "state" / "runs.jsonl"
    stamp = f"{date.today():%Y-%m-%d}"
    existing = runs.read_text() if runs.exists() else ""
    if f'"date": "{stamp}"' not in existing and f'"date":"{stamp}"' not in existing:
        words = len(re.sub(r"https?://\S+", "", today.read_text()).split())
        with runs.open("a") as fh:
            fh.write(json.dumps({"date": stamp, "mode": args.mode or "daily",
                                 "words": words, "truncated": True}) + "\n")
        print("Run record was missing, appended a minimal one.")

    if not args.cron:
        subprocess.run(["open", str(page)], capture_output=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
