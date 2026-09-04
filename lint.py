#!/usr/bin/env python3
"""Check a brief against the mechanical rules in system-prompt.md sections 8 and 9.

    ./.venv/bin/python lint.py                # check today's brief
    ./.venv/bin/python lint.py --fix          # check and repair what is repairable
    ./.venv/bin/python lint.py briefs/x.md    # a specific file

The model cannot reliably police its own em dashes or count its own words, so those
checks live here. Exits non-zero when problems remain, which makes it usable as a gate.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).parent.resolve()

# From section 9. Substring match, case-insensitive.
BANNED = [
    "here's the thing", "let me be clear", "to be honest", "it's worth noting",
    "what nobody", "the part everyone misses", "the real story:",
    "marks a pivotal", "a testament to", "underscores the", "signals a broader",
    "experts say", "experts agree", "studies show", "analysts expect",
    "could potentially", "may possibly", "it remains to be seen",
    "leverage", "utilize", "robust", "headwinds", "tailwinds",
    "inflection point", "at scale", "ecosystem play", "move the needle",
    "low-hanging fruit", "synergy", "game-chang", "unprecedented",
]
# Words that are legitimate in finance writing when quoting a source verbatim.
ALLOW_IN_QUOTES = True


def prose_words(md: str) -> int:
    """Word count as a reader experiences it: no URLs, no markdown syntax, no tables."""
    t = re.sub(r"```.*?```", "", md, flags=re.S)          # code fences
    t = re.sub(r"^\|.*$", "", t, flags=re.M)              # table rows
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)        # links keep their text
    t = re.sub(r"https?://\S+", "", t)                    # bare urls
    t = re.sub(r"[#*_`>-]", " ", t)                       # markdown syntax
    return len(t.split())


def fix_dashes(md: str) -> tuple[str, int]:
    """Remove em dashes from prose. Table cells keep them: there they mean 'no data'."""
    out, n = [], 0
    for line in md.splitlines():
        if line.lstrip().startswith("|"):                 # table row, leave alone
            out.append(line)
            continue
        fixed = line.replace(" — ", ", ").replace(" – ", ", ")
        fixed = re.sub(r"(?<=\w)—(?=\w)", ", ", fixed)
        fixed = fixed.replace("—", ", ").replace(" ,", ",")
        n += len(line) - len(line.replace("—", "").replace("–", ""))
        out.append(fixed)
    return "\n".join(out) + ("\n" if md.endswith("\n") else ""), n


def budget_from_config() -> int:
    cfg = PROJECT / "config.yaml"
    if cfg.exists():
        if m := re.search(r"read_budget_words:\s*(\d+)", cfg.read_text()):
            return int(m.group(1))
    return 1900


def check(path: Path, fix: bool) -> int:
    md = path.read_text()
    problems: list[str] = []

    dashes = md.count("—") + md.count("–")
    table_dashes = sum(l.count("—") + l.count("–")
                       for l in md.splitlines() if l.lstrip().startswith("|"))
    prose_dashes = dashes - table_dashes

    if prose_dashes:
        if fix:
            md, _ = fix_dashes(md)
            print(f"  fixed    {prose_dashes} em dash{'es' if prose_dashes > 1 else ''} in prose")
        else:
            problems.append(f"{prose_dashes} em dashes in prose (section 8 bans them)")

    words = prose_words(md)
    budget = budget_from_config()
    over = words - budget
    if over > budget * 0.10:
        problems.append(
            f"{words} words against a {budget} budget, {over} over "
            f"({over / budget:.0%}). Demote items from the bottom of Need to know.")
    else:
        print(f"  ok       {words} words, budget {budget}")

    lower = md.lower()
    for phrase in BANNED:
        if phrase in lower:
            for i, line in enumerate(md.splitlines(), 1):
                if phrase in line.lower():
                    problems.append(f'line {i}: banned phrase "{phrase}"')
                    break

    # scaffolding leaks, allowed only inside Run notes
    body = md.split("## Run notes")[0]
    for term in ("subagent", "beat-researcher", "materiality score", "threshold"):
        if term in body.lower():
            problems.append(f'scaffolding leak outside Run notes: "{term}"')

    if fix and md != path.read_text():
        path.write_text(md)

    if problems:
        print(f"\n  {len(problems)} problem{'s' if len(problems) > 1 else ''} in {path.name}:")
        for p in problems:
            print(f"    - {p}")
        return 1
    print(f"  clean    {path.name}")
    return 0


def main() -> None:
    fix = "--fix" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        targets = [Path(a) if Path(a).is_absolute() else PROJECT / a for a in args]
    else:
        today = PROJECT / "briefs" / f"{date.today():%Y-%m-%d}.md"
        targets = [today] if today.exists() else sorted((PROJECT / "briefs").glob("*.md"))[-1:]
    sys.exit(max((check(t, fix) for t in targets), default=0))


if __name__ == "__main__":
    main()
