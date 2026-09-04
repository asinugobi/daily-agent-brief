#!/usr/bin/env python3
"""Single source of truth for the source desk.

Emits sources.yaml (machine-readable) and sources_js.txt (the JS array literal
spliced into source-desk.html) so the two deliverables cannot drift apart.

Note markup: *emphasis* and `code` render as <em>/<code> in HTML, stripped in YAML.
Every endpoint below was fetched and validated 2026-09-04.
"""
import json, re

import os as _os
from pathlib import Path as _Path
# Generated files live beside this script, not in the caller's cwd,
# so the agent can invoke it from the project root.
_os.chdir(_Path(__file__).resolve().parent)

# (name, url, tier, access, cadence, note)
# access: open | paywall_headlines | license_required | key_required | scrape
GROUPS = [
 ("AI &middot; first-party announcements", "ai_primary", "ai", [
  ("OpenAI News","https://openai.com/news/rss.xml",1,"open","irregular",""),
  ("Google DeepMind Blog","https://deepmind.google/blog/rss.xml",1,"open","weekly",""),
  ("Google Research Blog","https://research.google/blog/rss/",1,"open","weekly",""),
  ("Meta Newsroom","https://about.fb.com/news/feed/",1,"open","daily","Corporate-wide feed - filter for AI keywords."),
  ("NVIDIA Blog","https://blogs.nvidia.com/feed/",1,"open","daily","Marketing-heavy. Useful for hardware and supply signals, not analysis."),
  ("Hugging Face Blog","https://huggingface.co/blog/feed.xml",1,"open","daily",""),
  ("Anthropic News","https://www.anthropic.com/news",1,"scrape","irregular","No RSS endpoint exists. Poll the HTML index."),
  ("Mistral AI News","https://mistral.ai/news",1,"scrape","irregular","No RSS endpoint exists."),
 ]),
 ("Physical AI &middot; first-party", "physical_primary", "physical", [
  ("NVIDIA Robotics","https://blogs.nvidia.com/blog/category/robotics/feed/",1,"open","weekly","The Jetson/Isaac/GR00T stack. Best single first-party feed for embodied AI tooling."),
  ("Waymo Blog","https://waymo.com/blog/rss.xml",1,"open","several weekly","Deepest public operating record of any autonomy company at scale."),
  ("Boston Dynamics","https://bostondynamics.com/blog/",1,"scrape","irregular","Feed endpoint returns zero items - poll the HTML index."),
  ("Figure AI","https://www.figure.ai/news",1,"scrape","irregular","Humanoids. No feed. Announcement-driven, so *verify claims against demo video*."),
  ("Physical Intelligence","https://www.physicalintelligence.company/blog",1,"scrape","irregular","Robot foundation models. No feed."),
  ("Agility Robotics","https://agilityrobotics.com/news",1,"scrape","irregular","Warehouse humanoids. No feed."),
 ]),
 ("AI &middot; research preprints", "ai_research", "ai", [
  ("arXiv cs.AI","https://export.arxiv.org/rss/cs.AI",1,"open","daily","~270 papers/day and *not peer reviewed*. Signal of research direction, not established finding."),
  ("arXiv cs.LG - Machine Learning","https://export.arxiv.org/rss/cs.LG",1,"open","daily",""),
  ("arXiv cs.CL - Computation and Language","https://export.arxiv.org/rss/cs.CL",1,"open","daily",""),
  ("arXiv econ.GN - General Economics","https://export.arxiv.org/rss/econ.GN",1,"open","daily","Low volume. The best single feed for AI-and-labour-market preprints."),
 ]),
 ("Physical AI &middot; research", "physical_research", "physical", [
  ("arXiv cs.RO - Robotics","https://export.arxiv.org/rss/cs.RO",1,"open","daily","~75/day. The core physical-AI preprint feed."),
  ("arXiv eess.SY - Systems and Control","https://export.arxiv.org/rss/eess.SY",1,"open","daily","Control theory underpinning real-world actuation."),
  ("arXiv cs.MA - Multiagent Systems","https://export.arxiv.org/rss/cs.MA",1,"open","daily","Fleet coordination and multi-robot autonomy."),
  ("Science Robotics","https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=scirobotics",1,"paywall_headlines","weekly","Peer reviewed - the quality bar arXiv does not clear."),
  ("Nature Machine Intelligence","https://www.nature.com/natmachintell.rss",1,"paywall_headlines","monthly","Peer reviewed. Embodied learning and robot foundation models."),
 ]),
 ("Physical AI &middot; industry and installation data", "physical_industry", "physical", [
  ("IEEE Spectrum - Robotics","https://spectrum.ieee.org/feeds/topic/robotics.rss",2,"open","daily","Technically literate and fully open. The strongest robotics reporting on this list."),
  ("The Robot Report","https://www.therobotreport.com/feed/",2,"open","daily","Funding rounds, deployments, order books. The trade press of record."),
  ("Robohub","https://robohub.org/feed/",3,"open","daily","Researcher-written. Bridges the preprint literature and industry."),
  ("IFR World Robotics","https://ifr.org/ifr-press-releases",1,"scrape","annual","*The authoritative robot installation statistics* - functionally the BLS of robotics. No feed; poll the press page. Annual World Robotics report is the anchor dataset for automation economics."),
  ("A3 - Association for Advancing Automation","https://www.automate.org/news",1,"scrape","quarterly","North American robot order and shipment statistics. Returns 403 to bots - poll or license."),
 ]),
 ("Automation economics", "automation_economics", "econ", [
  ("Fed G.17 - Industrial Production and Capacity Utilization","https://www.federalreserve.gov/feeds/g17.xml",1,"open","monthly","*The core automation-economics primary series.* Capacity utilization is the demand signal that precedes automation capex."),
  ("MIT Initiative on the Digital Economy","https://ide.mit.edu/feed/",4,"open","weekly","Where the Acemoglu/Autor-lineage work on automation and labour displacement surfaces first."),
  ("BLS Productivity and Costs","https://api.bls.gov/publicAPI/v2/timeseries/data/",1,"open","quarterly","Via the API - `PRS85006092` is nonfarm business output per hour. The series any automation-productivity claim must be checked against."),
  ("Census Annual Business Survey","https://www.census.gov/programs-surveys/abs.html",1,"scrape","annual","Carries firm-level *robotics and AI adoption* questions. The only large-sample US adoption data. No feed."),
 ]),
 ("Company disclosure &middot; SEC EDGAR", "edgar", "econ", [
  ("EDGAR XBRL Frames API","https://data.sec.gov/api/xbrl/frames/us-gaap/PaymentsToAcquirePropertyPlantAndEquipment/USD/CY2025Q1.json",1,"open","quarterly","*One concept across every filer for one period* - the single most useful endpoint here. Verified: this call returns 2,701 filers' capex for CY2025Q1, ranking Alphabet 17.2B, Microsoft 16.8B, Meta 12.9B. Swap the concept for `ResearchAndDevelopmentExpense` or any us-gaap tag. Caution: filers use different tags for the same economics, so a frame is a starting population, not a complete one."),
  ("EDGAR Company Facts API","https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json",1,"open","continuous","Every XBRL fact a filer has ever reported, in one document. Zero-pad the CIK to ten digits (this example is NVIDIA)."),
  ("EDGAR Company Concept API","https://data.sec.gov/api/xbrl/companyconcept/CIK0001045810/us-gaap/ResearchAndDevelopmentExpense.json",1,"open","continuous","One concept's full history for one filer, with the accession number behind each figure - so every number you publish is citable to a filing."),
  ("EDGAR Submissions API","https://data.sec.gov/submissions/CIK0001045810.json",1,"open","continuous","Complete filing history and metadata for a filer. The entry point for tracking who filed what, when."),
  ("EDGAR Full-Text Search","https://efts.sec.gov/LATEST/search-index?q=%22artificial+intelligence%22&forms=10-K",1,"open","daily","Searches filing *text*, not just metadata. The way to track how language like `physical AI` or `agentic` spreads through 10-K risk factors - a leading indicator disclosure picks up before revenue does."),
  ("EDGAR Current Filings Stream","https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",1,"open","continuous","Real-time Atom feed of filings as they land. Change `type=` for 10-K, 10-Q, S-1. The fastest legitimate route to a material corporate event."),
  ("EDGAR Insider Transactions - Form 4","https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&output=atom",1,"open","continuous","Officer and director trades, filed within two business days."),
  ("EDGAR Institutional Holdings - 13F","https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=13F-HR&output=atom",1,"open","quarterly","Institutional positions. *Filed 45 days after quarter-end* - stale by construction, so never report it as a current holding."),
  ("SEC Company Tickers Map","https://www.sec.gov/files/company_tickers.json",1,"open","weekly","Ticker-to-CIK lookup. You need this before any other EDGAR call - fetch once and cache."),
  ("EDGAR Financial Statement Data Sets","https://www.sec.gov/files/dera/data/financial-statement-data-sets/2025q1.zip",1,"open","quarterly","Bulk quarterly ZIP of every filer's numeric XBRL data. Use this instead of thousands of API calls when doing cross-sectional work."),
  ("EDGAR Daily Index","https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/",1,"open","daily","Every filing accepted each day. The completeness backstop if a feed drops something."),
  ("SEC Press Releases","https://www.sec.gov/news/pressreleases.rss",1,"open","daily","Enforcement actions and rulemaking. Pair with `https://www.sec.gov/news/speeches-statements.rss` for policy signals."),
 ]),
 ("Energy and AI infrastructure", "energy", "econ", [
  ("IEA - International Energy Agency","https://www.iea.org/analysis",1,"scrape","weekly","*No RSS exists anywhere on iea.org* - verified across eight candidate paths. Poll the analysis index. The authoritative source on datacenter electricity demand, now a first-order AI economics story."),
  ("EIA Today in Energy","https://www.eia.gov/rss/todayinenergy.xml",1,"open","daily","US Energy Information Administration. Daily, chart-led, and open - the practical substitute for IEA's missing feed."),
  ("EIA Press Releases","https://www.eia.gov/rss/press_rss.xml",1,"open","weekly","Short-Term Energy Outlook and electricity demand projections."),
 ]),
 ("AI &middot; policy and regulation", "ai_policy", "ai", [
  ("Federal Register - artificial intelligence","https://www.federalregister.gov/api/v1/documents.json?conditions[term]=artificial+intelligence&order=newest",1,"open","daily","JSON API, no key. The authoritative record of US rulemaking. Add `conditions[type]=RULE` to cut notice noise."),
  ("NIST News","https://www.nist.gov/news-events/news/rss.xml",1,"open","daily","Source for AI Risk Management Framework updates."),
  ("European Commission - Digital Strategy","https://digital-strategy.ec.europa.eu/en/rss.xml",1,"open","daily","Primary feed for EU AI Act implementation and guidance."),
  ("European Commission Press Corner","https://ec.europa.eu/commission/presscorner/api/rss?language=en",1,"open","daily",""),
  ("UK AI Security Institute","https://www.gov.uk/search/news-and-communications.atom?organisations%5B%5D=ai-safety-institute",1,"open","weekly","Atom. The gov.uk org slug is still `ai-safety-institute` after the rename."),
  ("CSET - Georgetown","https://cset.georgetown.edu/feed/",4,"open","weekly","Center for Security and Emerging Technology. Rigorous on compute, chips and China policy."),
  ("RAND","https://www.rand.org/news/press.xml",4,"open","weekly","The AI-topic feed returns zero items; the press feed carries the same research."),
 ]),
 ("Economics &middot; US agencies and Treasury", "econ_primary_us", "econ", [
  ("BLS Public Data API v2","https://api.bls.gov/publicAPI/v2/timeseries/data/",1,"open","scheduled","POST. CPI, jobs, PPI, ECI. Works unregistered at low volume. Key series: `CUUR0000SA0` CPI-U, `LNS14000000` unemployment, `CES0000000001` payrolls. Its RSS returns 403 - use this."),
  ("BLS Release Calendar","https://www.bls.gov/schedule/news_release/",1,"scrape","scheduled","*Critical for a daily report.* Major prints drop 08:30 ET on dates known months ahead."),
  ("Bureau of Economic Analysis","https://apps.bea.gov/rss/rss.xml",1,"open","scheduled","GDP, PCE price index (the Fed's preferred inflation gauge), trade balance."),
  ("Census Economic Indicators","https://www.census.gov/economic-indicators/indicator.xml",1,"open","scheduled","Retail sales, housing starts, durable goods, business inventories."),
  ("Treasury Daily Yield Curve","https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026",1,"open","daily","Atom/OData XML, ~15:30 ET each business day. Increment the year parameter annually."),
  ("FRED - St. Louis Fed","https://api.stlouisfed.org/fred/",1,"key_required","continuous","Free key. The best aggregation layer - 800k+ series, normalised, with vintage and revision history."),
 ]),
 ("Economics &middot; central banks", "econ_central_banks", "econ", [
  ("Federal Reserve - Monetary Policy","https://www.federalreserve.gov/feeds/press_monetary.xml",1,"open","scheduled","FOMC statements and minutes. The highest-value single economics feed on this list."),
  ("Federal Reserve - Speeches","https://www.federalreserve.gov/feeds/speeches.xml",1,"open","weekly","Where policy shifts are trailed before they are made."),
  ("Federal Reserve - All Press Releases","https://www.federalreserve.gov/feeds/press_all.xml",1,"open","daily",""),
  ("European Central Bank Press","https://www.ecb.europa.eu/rss/press.html",1,"open","daily",""),
  ("Bank of England News","https://www.bankofengland.co.uk/rss/news",1,"open","daily",""),
  ("Bank of Canada Press Releases","https://www.bankofcanada.ca/content_type/press-releases/feed/",1,"open","weekly",""),
  ("Reserve Bank of Australia","https://www.rba.gov.au/rss/rss-cb-media-releases.xml",1,"open","weekly",""),
 ]),
 ("Reference journalism", "journalism", "cross", [
  ("Bloomberg Markets","https://feeds.bloomberg.com/markets/news.rss",2,"paywall_headlines","continuous",""),
  ("Bloomberg Technology","https://feeds.bloomberg.com/technology/news.rss",2,"paywall_headlines","continuous",""),
  ("Wall Street Journal - Markets","https://feeds.a.dj.com/rss/RSSMarketsMain.xml",2,"paywall_headlines","continuous",""),
  ("Financial Times","https://www.ft.com/rss/home",2,"paywall_headlines","continuous",""),
  ("New York Times - Economy","https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",2,"paywall_headlines","daily",""),
  ("New York Times - Technology","https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",2,"paywall_headlines","daily",""),
  ("The Economist - Finance and Economics","https://www.economist.com/finance-and-economics/rss.xml",2,"paywall_headlines","weekly",""),
  ("BBC Business","https://feeds.bbci.co.uk/news/business/rss.xml",2,"open","continuous","The best fully-open general business wire substitute."),
  ("MIT Technology Review","https://www.technologyreview.com/feed/",2,"paywall_headlines","daily",""),
  ("IEEE Spectrum - AI","https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",2,"open","daily","The strongest technically-literate AI reporting that is fully open."),
  ("Ars Technica - IT","https://feeds.arstechnica.com/arstechnica/technology-lab",2,"open","daily",""),
  ("The Verge","https://www.theverge.com/rss/index.xml",2,"open","continuous","High volume, consumer-tech skew. Filter hard."),
  ("Nature - News","https://www.nature.com/nature.rss",2,"paywall_headlines","weekly","Peer-reviewed research news across AI and robotics."),
  ("Science - News","https://www.science.org/rss/news_current.xml",2,"paywall_headlines","daily",""),
  ("Reuters","https://www.reuters.com/",2,"license_required","continuous","RSS returns 401. Requires Reuters Connect licensing or an aggregator holding those rights."),
  ("Associated Press","https://apnews.com/",2,"license_required","continuous","RSS returns 401. AP Newsroom licensing required."),
 ]),
 ("Management consulting research", "consulting", "cross", [
  ("McKinsey Insights","https://www.mckinsey.com/insights/rss",3,"open","daily","*The only MBB firm with a working open feed* - verified 50 items. Carries McKinsey Global Institute output, which is the substantive research arm."),
  ("BCG Publications","https://www.bcg.com/publications",3,"scrape","weekly","Returns 403 to automated clients across every RSS path tried. Poll the HTML index. BCG Henderson Institute is the research arm."),
  ("Bain Insights","https://www.bain.com/insights/",3,"scrape","weekly","Returns 403. No working feed. The annual Bain Technology Report is the flagship for AI market sizing."),
 ]),
 ("Academic research", "academic", "cross", [
  ("Stanford AI Lab (SAIL) Blog","https://ai.stanford.edu/blog/feed.xml",4,"open","several weekly","Researcher-written. The working feed for Stanford AI output."),
  ("Stanford HAI","https://hai.stanford.edu/news",4,"scrape","weekly","*No feed on any tested path.* Poll the index. Publishes the annual AI Index Report - the single most-cited AI statistics compendium."),
  ("MIT News - Artificial Intelligence","https://news.mit.edu/rss/topic/artificial-intelligence2",4,"open","daily","High volume and reliably tagged. The best single academic AI feed."),
  ("MIT CSAIL","https://www.csail.mit.edu/rss.xml",4,"open","weekly","Computer Science and AI Laboratory - core robotics and systems work."),
  ("MIT Initiative on the Digital Economy","https://ide.mit.edu/feed/",4,"open","weekly","Also listed under automation economics - the labour-displacement research hub."),
  ("Berkeley News","https://news.berkeley.edu/feed/",4,"open","daily",""),
  ("Berkeley Engineering","https://engineering.berkeley.edu/feed/",4,"open","weekly",""),
  ("Berkeley AI Research (BAIR) Blog","https://bair.berkeley.edu/blog/",4,"scrape","weekly","*Feed unreachable from automated clients* (connection refused on both http and https). Poll the HTML index. High-value on robot learning."),
  ("Wharton - Knowledge at Wharton","https://knowledge.wharton.upenn.edu/feed/",4,"open","daily","UPenn. Strong on the business and labour economics of AI adoption."),
  ("Harvard Berkman Klein Center","https://cyber.harvard.edu/",4,"scrape","weekly","No working feed on any tested path. AI governance and policy research."),
  ("Harvard Business School Working Knowledge","https://hbswk.hbs.edu/",4,"scrape","weekly","No working feed. Firm-level AI adoption research."),
  ("Kellogg Insight - Northwestern","https://insight.kellogg.northwestern.edu/",4,"scrape","weekly","No working feed on any tested path. Strong on AI and organisational economics."),
  ("Duke Pratt School of Engineering","https://pratt.duke.edu/feed/",4,"open","weekly","The working Duke feed - today.duke.edu returns 404 on every feed path."),
 ]),
 ("Specialist analysis &middot; AI and physical AI", "analysis_ai", "ai", [
  ("SemiAnalysis - Dylan Patel","https://www.semianalysis.com/feed",3,"paywall_headlines","weekly","Best-in-class on compute economics, datacenter buildout and chip supply."),
  ("Stratechery - Ben Thompson","https://stratechery.com/feed/",3,"paywall_headlines","daily","Strategy and business-model analysis of the AI platform shift."),
  ("Import AI - Jack Clark","https://importai.substack.com/feed",3,"open","weekly","Author is an Anthropic co-founder - *disclose the conflict* when citing on Anthropic."),
  ("Interconnects - Nathan Lambert","https://www.interconnects.ai/feed",3,"open","weekly","Deep on open models, RLHF and post-training."),
  ("Don't Worry About the Vase - Zvi Mowshowitz","https://thezvi.substack.com/feed",3,"open","weekly","The highest-recall single AI roundup published. Strong AI-risk viewpoint - excellent lead-finder, attribute for opinion."),
  ("Platformer - Casey Newton","https://www.platformer.news/rss/",3,"paywall_headlines","several weekly",""),
  ("Transformer - Shakeel Hashim","https://www.transformernews.ai/feed",3,"open","several weekly","AI policy and safety newsroom."),
  ("ChinaTalk - Jordan Schneider","https://www.chinatalk.media/feed",3,"open","several weekly","Best English-language source on China AI policy and export controls - which is *also* the best source on robotics supply chains."),
 ]),
 ("Specialist analysis &middot; economics", "analysis_econ", "econ", [
  ("Calculated Risk - Bill McBride","https://calculatedrisk.substack.com/feed",3,"open","daily","Housing and employment. Long track record, chart-forward."),
  ("Conversable Economist - Timothy Taylor","https://conversableeconomist.com/feed/",3,"open","several weekly","Managing editor of the Journal of Economic Perspectives. Notably even-handed."),
  ("Noahpinion - Noah Smith","https://www.noahpinion.blog/feed",3,"paywall_headlines","several weekly","Opinionated macro commentary. Label clearly as opinion."),
 ]),
 ("Institutional research", "institutional", "cross", [
  ("NBER New Working Papers","https://www.nber.org/rss/new.xml",4,"open","weekly","*Working papers are not peer reviewed.* The primary venue for automation-and-labour research."),
  ("NY Fed - Liberty Street Economics","https://libertystreeteconomics.newyorkfed.org/feed/",4,"open","several weekly",""),
  ("Atlanta Fed GDPNow","https://www.atlantafed.org/rss/gdpnow",4,"open","several weekly","Real-time GDP nowcast, updated after major releases. A *model output*, not a forecast or an official estimate - always say so."),
  ("San Francisco Fed Research","https://www.frbsf.org/feed/",4,"open","weekly",""),
  ("OECD Ecoscope","https://oecdecoscope.blog/feed/",4,"open","weekly",""),
  ("Our World in Data","https://ourworldindata.org/atom.xml",4,"open","weekly","Long-run series on technology adoption and productivity, with sources shown."),
  ("Brookings Institution","https://www.brookings.edu/topic/economy/",4,"scrape","daily","`/feed/` returns HTML - bot-blocked."),
  ("Peterson Institute (PIIE)","https://www.piie.com/research",4,"scrape","weekly","No working RSS endpoint found."),
  ("International Monetary Fund","https://www.imf.org/en/News",4,"scrape","daily","RSS returns 403 to automated clients. Data via the IMF Data API."),
  ("Bank for International Settlements","https://www.bis.org/press/index.htm",4,"scrape","weekly","No current RSS endpoint found."),
 ]),
]

# An endpoint's access model says whether you may fetch it; its KIND says how.
# Several tier-1 sources are query APIs or bulk archives, not item streams -
# polling them like feeds produces confusing "failures" for endpoints that are
# working perfectly. They need bespoke handlers, so they are routed, not polled.
API_IDS = {
    "edgar_xbrl_frames_api", "edgar_company_facts_api",
    "edgar_company_concept_api", "edgar_submissions_api",
    "edgar_full_text_search", "sec_company_tickers_map",
    "federal_register_artificial_intelligence", "bls_public_data_api_v2",
    "bls_productivity_and_costs", "fred_st_louis_fed",
}
BULK_IDS = {"edgar_financial_statement_data_sets"}
INDEX_IDS = {"edgar_daily_index"}


def kind_of(sid, access):
    if sid in API_IDS:
        return "api"
    if sid in BULK_IDS:
        return "bulk"
    if sid in INDEX_IDS or access in ("scrape", "license_required"):
        return "page"
    return "feed"


# Corroboration must count independent publishers, not feeds. arXiv cs.AI,
# cs.LG and cs.RO are five feeds and one publisher: a paper cross-listed to all
# three is one document, not three sources agreeing. Same for NYT Economy vs
# Technology, Bloomberg Markets vs Technology, and the Fed's several feeds.
_MULTI_SUFFIX = ("co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "co.jp", "co.nz")
_OUTLET_OVERRIDE = {
    "about.fb.com": "meta.com",
    "ai.meta.com": "meta.com",
    "blogs.nvidia.com": "nvidia.com",
    "deepmind.google": "google.com",
    "research.google": "google.com",
    "efts.sec.gov": "sec.gov",
    "data.sec.gov": "sec.gov",
    "feeds.a.dj.com": "wsj.com",
    "feeds.bbci.co.uk": "bbc.co.uk",
}


def outlet_of(url):
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in _OUTLET_OVERRIDE:
        return _OUTLET_OVERRIDE[host]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_SUFFIX:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def slug(name):
    """Stable id from a source name - referenced by config filters and the store."""
    s = name.lower()
    s = s.replace("&middot;", " ").replace("&amp;", " and ")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return re.sub(r"_+", "_", s)


def yq(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

def strip_markup(s):
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return s

def clean_label(s):
    return s.replace("&middot;", "-")

# ---- emit YAML ----
# A source may be deliberately cross-listed in two groups (it is genuinely
# relevant to both). That is fine for the page, but the fetcher must not poll it
# twice - so a repeated slug is only allowed when the URL matches, and the JSON
# emitted for the pipeline is deduplicated by URL below.
ids = {}
for _, _, _, _items in GROUPS:
    for i in _items:
        sid, url = slug(i[0]), i[1]
        if sid in ids and ids[sid] != url:
            raise SystemExit(f"slug collision {sid!r} across different URLs:\n  {ids[sid]}\n  {url}")
        ids[sid] = url

out = ['# Source list for daily AI, physical AI and economic developments',
       '# All endpoints verified live 2026-09-04. Re-validate quarterly; feed URLs rot.',
       '#',
       '# tier 1 = primary record (agencies, central banks, first-party, preprints). Cite directly.',
       '# tier 2 = reference journalism. Corroborate single-source scoops.',
       '# tier 3 = specialist analysis (named viewpoint). Attribute as analysis, never as fact.',
       '# tier 4 = institutional and academic research (slow, deep, high credibility).',
       '#',
       '# access: open | paywall_headlines | license_required | key_required | scrape',
       '# domain: ai | physical | econ | cross',
       '',
       'meta:',
       '  verified: 2026-09-04',
       '  revalidate_every: 90d',
       '  note: >',
       '    Reuters and AP RSS are licensing-gated (401). BLS, IMF, BCG, Bain and A3',
       '    return 403 to automated clients. IEA publishes no RSS on any path. These are',
       '    labelled scrape or license_required rather than left in as endpoints that fail.',
       '']
total = 0
for label, key, domain, items in GROUPS:
    out.append(f'# --- {clean_label(label)} ---')
    out.append(f'{key}:')
    for n, u, t, a, c, note in items:
        total += 1
        out.append(f'  - id: {slug(n)}')
        out.append(f'    name: {yq(n)}')
        out.append(f'    url: {yq(u)}')
        out.append(f'    tier: {t}')
        out.append(f'    access: {a}')
        out.append(f'    domain: {domain}')
        out.append(f'    cadence: {yq(c)}')
        if note:
            out.append(f'    note: {yq(strip_markup(note))}')
    out.append('')
open('sources.yaml', 'w').write('\n'.join(out))

# ---- emit JS ----
js = []
for label, key, domain, items in GROUPS:
    rows = []
    for n, u, t, a, c, note in items:
        r = {"n": n, "u": u, "t": t, "a": a, "c": c, "d": domain}
        if note:
            r["no"] = note
        rows.append(r)
    js.append({"g": label, "d": domain, "items": rows})
open('sources_js.txt', 'w').write("const S = " + json.dumps(js, ensure_ascii=True, indent=1) + ";")

# ---- emit JSON for the fetch pipeline (no PyYAML in the target env) ----
# Deduplicated by URL: two rows pointing at one endpoint are one poll target.
by_url, order = {}, []
for label, key, domain, items in GROUPS:
    for n, u, t, a, c, note in items:
        if u in by_url:
            by_url[u]["also_in"].append(clean_label(label))
            continue
        by_url[u] = {
            "id": slug(n), "name": n, "url": u, "tier": t, "access": a,
            "kind": kind_of(slug(n), a), "outlet": outlet_of(u),
            "domain": domain, "cadence": c, "group": clean_label(label),
            "group_key": key, "note": strip_markup(note), "also_in": [],
        }
        order.append(u)
feeds = [by_url[u] for u in order]
dupes = total - len(feeds)
json.dump({"verified": "2026-09-04", "listed": total, "unique": len(feeds),
           "feeds": feeds}, open('sources.json', 'w'), indent=1)
print(f"sources.json: {len(feeds)} unique endpoints ({dupes} cross-listed row(s) merged)")

# ---- stats ----
from collections import Counter
acc = Counter(a for _, _, _, items in GROUPS for *_, a, _, _ in [(i[0], i[1], i[2], i[3], i[4], i[5]) for i in items])
acc = Counter(i[3] for _, _, _, items in GROUPS for i in items)
dom = Counter(d for _, _, d, items in GROUPS for _ in items)
print("TOTAL:", total)
print("access:", dict(acc))
print("domain:", dict(dom))
print("groups:", len(GROUPS))
