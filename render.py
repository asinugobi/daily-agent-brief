#!/usr/bin/env python3
"""Render a brief from Markdown to a newspaper-styled HTML page.

    ./.venv/bin/python render.py                      # today's brief
    ./.venv/bin/python render.py briefs/2026-09-02.md # a specific one
    ./.venv/bin/python render.py --all                # re-render every brief

Rendering is deterministic and lives here, not in the agent's prompt. The agent
writes Markdown; this decides how it looks. Restyle without touching the prompt.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import markdown

PROJECT = Path(__file__).parent.resolve()

CSS = """
:root {
  --paper:      #fdfcf8;
  --ink:        #16150f;
  --ink-soft:   #55524a;
  --ink-faint:  #8a867c;
  --rule:       #d9d5c9;
  --rule-hair:  #e8e5db;
  --link:       #0a4d7a;
  --flag:       #a8261c;
  --wash:       #f5f2e9;
  --measure:    43rem;
  --serif:      "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Times New Roman", serif;
  --sans:       "Helvetica Neue", Helvetica, Arial, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #14140f; --ink: #ece8dc; --ink-soft: #a8a396; --ink-faint: #78746a;
    --rule: #35332b; --rule-hair: #26241e; --link: #7fb6dd; --flag: #d9645a; --wash: #1c1b15;
  }
}
:root[data-theme="dark"] {
  --paper: #14140f; --ink: #ece8dc; --ink-soft: #a8a396; --ink-faint: #78746a;
  --rule: #35332b; --rule-hair: #26241e; --link: #7fb6dd; --flag: #d9645a; --wash: #1c1b15;
}

body { background: var(--paper); color: var(--ink); font-family: var(--serif);
       font-size: 18px; line-height: 1.58; margin: 0; padding: 0 1.25rem 6rem;
       -webkit-font-smoothing: antialiased; }
.wrap { max-width: var(--measure); margin: 0 auto; }

/* masthead ------------------------------------------------------------- */
.masthead { text-align: center; padding: 2.75rem 0 0; }
.masthead .hair { border-top: 1px solid var(--ink); margin-bottom: 1.15rem; }
.masthead h1 { font-size: clamp(1.7rem, 5.5vw, 2.35rem); line-height: 1.05; margin: 0;
  font-weight: 700; letter-spacing: 0.02em; font-variant: small-caps; }
.dateline { font-family: var(--sans); font-size: 0.64rem; letter-spacing: 0.19em;
  text-transform: uppercase; color: var(--ink-soft); margin: 0.95rem 0 0.75rem; }
.tally { font-family: var(--sans); font-size: 0.66rem; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--ink-faint); padding-bottom: 1.1rem; }
.masthead .hair-b { border-top: 3px solid var(--ink); border-bottom: 1px solid var(--ink);
  height: 3px; margin-bottom: 2.25rem; }

/* section headings ------------------------------------------------------ */
h2 { font-family: var(--sans); font-size: 0.7rem; font-weight: 700; letter-spacing: 0.2em;
  text-transform: uppercase; color: var(--ink); margin: 3rem 0 0; padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--ink); }
h2 + * { margin-top: 1.1rem; }
h3 { font-size: 1.24rem; line-height: 1.28; font-weight: 700; margin: 2.1rem 0 0.55rem;
  letter-spacing: -0.008em; }

p { margin: 0 0 0.95rem; }
a { color: var(--link); text-decoration: none; border-bottom: 1px solid color-mix(in srgb, var(--link) 32%, transparent); }
a:hover { border-bottom-color: var(--link); }

/* lede ------------------------------------------------------------------ */
.lede { font-size: 1.16rem; line-height: 1.5; }
.lede ul { list-style: none; padding: 0; margin: 0; }
.lede li { padding: 0.72rem 0 0.72rem 2.15rem; border-bottom: 1px solid var(--rule-hair);
  position: relative; counter-increment: lede; }
.lede li:first-child { border-top: 1px solid var(--rule-hair); }
.lede li::before { content: counter(lede); position: absolute; left: 0; top: 0.78rem;
  font-family: var(--sans); font-size: 0.7rem; font-weight: 700; color: var(--flag);
  letter-spacing: 0.05em; }
.lede ul { counter-reset: lede; }

/* item labels ----------------------------------------------------------- */
p > em:first-child, p > strong:first-child.label {
  font-family: var(--sans); font-style: normal; font-weight: 700; font-size: 0.63rem;
  letter-spacing: 0.15em; text-transform: uppercase; color: var(--ink-faint);
  display: block; margin-bottom: 0.18rem; }

/* confidence pills ------------------------------------------------------ */
code { font-family: var(--sans); font-size: 0.62rem; font-weight: 700; letter-spacing: 0.13em;
  text-transform: uppercase; padding: 0.17em 0.5em 0.19em; border: 1px solid var(--rule);
  border-radius: 2px; color: var(--ink-soft); background: var(--wash); white-space: nowrap; }
code.confirmed { color: #1d6b3f; border-color: #9ec7ad; background: color-mix(in srgb, #1d6b3f 8%, transparent); }
code.reported  { color: #8a6412; border-color: #d3bb7e; background: color-mix(in srgb, #8a6412 9%, transparent); }
code.rumor     { color: var(--flag); border-color: #dda9a4; background: color-mix(in srgb, #a8261c 8%, transparent); }

/* tables ---------------------------------------------------------------- */
.scroll { overflow-x: auto; margin: 0 0 1.2rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th { font-family: var(--sans); font-size: 0.6rem; font-weight: 700; letter-spacing: 0.14em;
  text-transform: uppercase; text-align: left; padding: 0.5rem 0.7rem 0.5rem 0;
  border-bottom: 1.5px solid var(--ink); color: var(--ink-soft); white-space: nowrap; }
td { padding: 0.55rem 0.7rem 0.55rem 0; border-bottom: 1px solid var(--rule-hair);
  vertical-align: top; }
tr:last-child td { border-bottom: 1px solid var(--rule); }

/* callouts -------------------------------------------------------------- */
.callout { background: var(--wash); border-left: 3px solid var(--flag);
  padding: 1.05rem 1.25rem 0.4rem; margin: 1.1rem 0 1.4rem; }
.callout p:last-child { margin-bottom: 0.65rem; }
.quiet { color: var(--ink-soft); font-size: 0.92rem; }
.quiet li { margin-bottom: 0.42rem; }

ul, ol { padding-left: 1.15rem; margin: 0 0 1rem; }
li { margin-bottom: 0.3rem; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2.4rem 0; }

footer { margin-top: 3.5rem; padding-top: 1rem; border-top: 1px solid var(--rule);
  font-family: var(--sans); font-size: 0.64rem; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--ink-faint); text-align: center; }

@media print {
  body { background: #fff; color: #000; font-size: 11pt; padding: 0; }
  h2 { page-break-after: avoid; } h3 { page-break-after: avoid; }
  .callout { border-left-color: #000; background: #f4f4f4; }
  a { color: #000; border: 0; }
}
@media (max-width: 480px) { body { font-size: 17px; padding: 0 1rem 4rem; } }
"""

# Sections rendered in the muted, compact style rather than as body copy.
QUIET = {"other news", "run notes", "open loops"}
CALLOUT = {"disconfirming evidence"}


MONTHS = ("January February March April May June July August "
          "September October November December").split()


def split_front_matter(md: str) -> tuple[str, str, str]:
    """Return (dateline, tally, body). The nameplate is fixed, not taken from the file."""
    lines = md.splitlines()
    head, body_start = [], 0
    for i, line in enumerate(lines[:6]):
        if line.startswith("# "):
            head.append(line[2:].strip())
            body_start = i + 1
        elif line.strip() and not line.startswith("#") and head:
            head.append(line.strip())
            body_start = i + 1
            break
    blob = "  ".join(head)

    # date, formatted long: "Wednesday, September 2, 2026"
    pretty = ""
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", blob):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            wd = date(y, mo, d).strftime("%A")
            pretty = f"{wd}, {MONTHS[mo - 1]} {d}, {y}"
        except ValueError:
            pretty = m.group(0)
    elif m := re.search(r"(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+[^.]+", blob):
        pretty = m.group(0).strip()

    def grab(*keys: str) -> str:
        for k in keys:
            if m := re.search(rf"{k}\s*:?\s*([\w.\-]+)", blob, re.I):
                return m.group(1).rstrip(".")
        return ""

    dateline = " · ".join(x for x in (
        pretty,
        (grab("mode") or "").upper(),
        (f"{grab('lookback')} lookback" if grab("lookback") else ""),
    ) if x)

    counts = [
        (grab("candidates gathered", "gathered"), "gathered"),
        (grab("need to know", "need_to_know"), "need to know"),
        (grab("published"), "published"),
        (grab("other news", "other_news"), "other news"),
        (grab("dropped"), "dropped"),
    ]
    tally = " · ".join(f"{n} {label}" for n, label in counts if n and n.isdigit())

    return dateline, tally, "\n".join(lines[body_start:])


def decorate(html: str) -> str:
    """Post-process the converted HTML: pills, scrollable tables, section classes."""
    # confidence labels become coloured pills
    html = re.sub(r"<code>(confirmed|reported|rumor)</code>",
                  lambda m: f'<code class="{m.group(1)}">{m.group(1)}</code>', html, flags=re.I)
    # tables scroll on narrow screens instead of blowing out the page
    html = re.sub(r"<table>", '<div class="scroll"><table>', html)
    html = re.sub(r"</table>", "</table></div>", html)

    # tag each h2 block so sections can be styled by name
    out, cls = [], None
    for chunk in re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.S):
        m = re.match(r"<h2[^>]*>(.*?)</h2>", chunk, flags=re.S)
        if m:
            name = re.sub(r"<[^>]+>", "", m.group(1)).strip().lower()
            cls = ("callout" if name in CALLOUT else
                   "quiet" if name in QUIET else
                   "lede" if name in ("tl;dr", "tldr") else None)
            out.append(chunk)
        elif chunk.strip():
            out.append(f'<div class="{cls}">{chunk}</div>' if cls else chunk)
        else:
            out.append(chunk)
    return "".join(out)


def render(md_path: Path) -> Path:
    dateline, tally, body = split_front_matter(md_path.read_text())
    title = "The Daily Brief"
    html_body = markdown.markdown(body, extensions=["tables", "sane_lists", "attr_list"])

    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {dateline.split(" · ")[0]}</title>
<style>{CSS}</style>
</head><body>
<div class="wrap">
<header class="masthead">
  <div class="hair"></div>
  <h1>{title}</h1>
  <div class="dateline">{dateline}</div>
  {f'<div class="tally">{tally}</div>' if tally else ''}
  <div class="hair-b"></div>
</header>
<main>
{decorate(html_body)}
</main>
<footer>Generated by the daily brief agent · {md_path.name}</footer>
</div>
</body></html>"""

    out = md_path.with_suffix(".html")
    out.write_text(page)
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    briefs = PROJECT / "briefs"

    if "--all" in sys.argv:
        targets = sorted(briefs.glob("*.md"))
    elif args:
        targets = [Path(a) if Path(a).is_absolute() else PROJECT / a for a in args]
    else:
        today = briefs / f"{date.today():%Y-%m-%d}.md"
        targets = [today] if today.exists() else sorted(briefs.glob("*.md"))[-1:]

    if not targets:
        print("no briefs found in ./briefs", file=sys.stderr)
        sys.exit(1)
    for t in targets:
        print(render(t))


if __name__ == "__main__":
    main()
