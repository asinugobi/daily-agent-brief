#!/usr/bin/env python3
"""Re-runnable: sync source-desk.html to the current build_sources.py data.

Splices the generated array in and recomputes every displayed count from that
array, so the page can never disagree with the data. Safe to run repeatedly.
Run build_sources.py first.
"""
import re, json

import os as _os
from pathlib import Path as _Path
# Generated files live beside this script, not in the caller's cwd,
# so the agent can invoke it from the project root.
_os.chdir(_Path(__file__).resolve().parent)
from collections import Counter

html = open('source-desk.html', encoding='utf-8').read()
newjs = open('sources_js.txt', encoding='utf-8').read()

# ---- 1. splice data array ----
start = html.index('const S = [')
end = html.index('const LBL=')
html = html[:start] + newjs + "\n\n" + html[end:]

# ---- 2. recompute stats from the data itself ----
data = json.loads(newjs[len("const S = "):].rstrip().rstrip(';'))
items = [i for g in data for i in g["items"]]
total = len(items)
acc = Counter(i["a"] for i in items)
dom = Counter(i["d"] for i in items)
tier1 = sum(1 for i in items if i["t"] == 1)
print(f"total={total} access={dict(acc)} domain={dict(dom)} tier1={tier1}")

# ---- 3. kicker ----
html = re.sub(r'(<span><span class="dot"></span> )\d+( sources verified</span>)',
              rf'\g<1>{total}\g<2>', html)

# ---- 4. stat strip ----
strip = f'''<div class="strip">
  <div class="stat"><span class="n">{total}</span><span class="l">Sources</span></div>
  <div class="stat"><span class="n">{acc["open"]}</span><span class="l">Open access</span></div>
  <div class="stat"><span class="n">{acc["scrape"]}</span><span class="l">No feed &middot; scrape</span></div>
  <div class="stat"><span class="n">{dom["physical"]}</span><span class="l">Physical AI</span></div>
  <div class="stat"><span class="n">{tier1}</span><span class="l">Tier 1 primary</span></div>
</div>

'''
s0 = html.index('<div class="strip">')
s1 = html.index('<section>', s0)
html = html[:s0] + strip + html[s1:]

# ---- 5. footer ----
html = re.sub(r'(<footer>)\s*\d+ sources', rf'\g<1>\n  {total} sources', html)

# ---- 6. EDGAR caveat (insert once) ----
if 'User-Agent' not in html:
    cav = '''<div class="cav">
      <h4>EDGAR needs a User-Agent</h4>
      <p>Every <code>www.sec.gov</code> path returns <strong>403 without a User-Agent declaring a contact email</strong> &mdash; verified: identical requests went 403 to 200 on that header alone. <code>data.sec.gov</code> is laxer, so a pipeline can pass tests and still break on the bulk endpoints. Set it globally and stay under 10 requests/second.</p>
    </div>
  '''
    anchor = '<div class="cav">\n      <h4>Deduplicate before you read</h4>'
    html = html.replace(anchor, cav + anchor)

# ---- 7. standfirst: set wholesale so re-runs cannot append to their own output ----
STANDFIRST = ('<p class="standfirst">A tiered, endpoint-verified source list for reporting daily developments across '
              '<strong>AI, physical AI and the economics of automation</strong> &mdash; down to the filings where capex '
              'and R&amp;D become auditable numbers. Every feed below was fetched and confirmed to return live content '
              'on the verification date; the dead ones were removed, and the ones that block automated clients are '
              'labelled as such rather than quietly left in to fail at 6am.</p>')
html = re.sub(r'<p class="standfirst">.*?</p>', lambda _: STANDFIRST, html, count=1, flags=re.S)

open('source-desk.html', 'w', encoding='utf-8').write(html)
print("patched. bytes:", len(html))
