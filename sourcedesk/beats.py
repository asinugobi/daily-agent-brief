"""Route clustered sourcedesk items into the agent's nine beats.

The beat names here must match the coverage map in system-prompt.md section 3
exactly, because the agent dispatches one researcher per beat and each one reads
the file named after its beat.

Routing is source-first, keyword-second: which feed something came from is a far
stronger signal than what words are in its headline, so group and domain carry
most of the weight and keywords only break ties or rescue cross-cutting
journalism that could belong anywhere.
"""
import re

# Beat name -> (source group_keys, domains, keyword patterns, weight per hit)
BEATS = {
    "Compute, chips, cloud, energy": {
        "groups": {"energy"},
        "ids": {"nvidia_blog", "semianalysis_dylan_patel", "nvidia_robotics"},
        "keywords": [r"\bcapex\b", r"data ?cent", r"\bgigawatt", r"\bGW\b", r"\bMW\b",
                     r"\bfab\b", r"foundry", r"packaging", r"\bHBM\b", r"\bGPU\b",
                     r"\bTPU\b", r"accelerat", r"power purchase", r"\bgrid\b",
                     r"nuclear", r"electricity", r"cloud pricing", r"inference cost",
                     r"semiconductor", r"chip"],
    },
    "Foundation model labs": {
        "groups": {"ai_primary"},
        "keywords": [r"\bOpenAI\b", r"\bAnthropic\b", r"DeepMind", r"\bxAI\b",
                     r"Mistral", r"\bGPT\b", r"Claude", r"Gemini", r"Llama",
                     r"system card", r"model card", r"frontier model",
                     r"releases? .*model", r"post-?training"],
    },
    "Investing and markets": {
        # Journalism is deliberately NOT group-pinned here. A wire is
        # cross-cutting by nature: pinning the group to one beat meant a
        # Bloomberg story about export controls could never reach geopolitics.
        # Journalism routes on content instead.
        "groups": {"edgar"},
        "keywords": [r"\bearnings\b", r"guidance", r"\brevenue\b", r"\bfiled\b",
                     r"\b8-K\b", r"\b10-[QK]\b", r"valuation", r"funding round",
                     r"\bIPO\b", r"raises \$", r"\bstake\b", r"buyback",
                     r"analyst", r"\bcapex\b"],
    },
    "Geopolitics and geoeconomics": {
        "ids": {"chinatalk_jordan_schneider", "cset_georgetown"},
        "keywords": [r"export control", r"\btariff", r"\bChina\b", r"Taiwan",
                     r"sanction", r"sovereign AI", r"critical mineral",
                     r"entity list", r"\bGulf\b", r"national security"],
    },
    "Enterprise AI and applications": {
        "ids": {"stratechery_ben_thompson", "platformer_casey_newton"},
        "keywords": [r"enterprise", r"adoption", r"\bseats?\b", r"deployment",
                     r"retention", r"pricing model", r"agent(ic)? deploy",
                     r"copilot", r"customers?\b"],
    },
    "Robotics and physical AI": {
        "domains": {"physical"},
        "keywords": [r"robot", r"humanoid", r"manipulat", r"teleoperat",
                     r"actuator", r"warehouse", r"embodied", r"sim-to-real",
                     r"locomotion", r"dexter"],
    },
    "Mobility and AVs": {
        "ids": {"waymo_blog"},
        "keywords": [r"robotaxi", r"autonomous vehicle", r"self-?driving",
                     r"driverless", r"\bAV\b", r"fleet", r"Waymo", r"Cruise",
                     r"Zoox", r"\bADAS\b"],
    },
    "Policy, safety, regulation": {
        "groups": {"ai_policy"},
        "keywords": [r"regulat", r"\bAI Act\b", r"rulemaking", r"\bNIST\b",
                     r"safety institute", r"Federal Register", r"complian",
                     r"liability", r"procurement", r"executive order",
                     r"proposed rule"],
    },
    "Research and capability evidence": {
        "groups": {"ai_research", "physical_research", "academic", "institutional"},
        "keywords": [r"\barXiv\b", r"benchmark", r"\beval", r"paper",
                     r"peer[- ]review", r"working paper", r"cost curve"],
    },
}

# Ordered so a tie falls to the more specific beat rather than a catch-all.
PRIORITY = [
    "Robotics and physical AI",
    "Mobility and AVs",
    "Foundation model labs",
    "Policy, safety, regulation",
    "Compute, chips, cloud, energy",
    "Geopolitics and geoeconomics",
    "Investing and markets",
    "Enterprise AI and applications",
    "Research and capability evidence",
]

_COMPILED = {
    name: [re.compile(p, re.I) for p in spec.get("keywords", [])]
    for name, spec in BEATS.items()
}

GROUP_POINTS = 6
ID_POINTS = 8
DOMAIN_POINTS = 4
KEYWORD_POINTS = 2
MAX_KEYWORD_POINTS = 8   # enough for content alone to route a cross-cutting wire

RESEARCH_BEAT = "Research and capability evidence"
RESEARCH_GROUPS = {"ai_research", "physical_research"}
RESEARCH_OUTLETS = {"arxiv.org", "nature.com", "science.org"}

# A second beat needs real evidence, not a bare domain match. Without this a
# preprint lands in both the research beat and whichever topical beat shares its
# domain, and the topical pack fills with papers instead of news.
SECONDARY_FLOOR = 9


def is_research(row):
    return (row["grp_key"] in RESEARCH_GROUPS
            or row["outlet"] in RESEARCH_OUTLETS)


def score_beat(name, row):
    spec = BEATS[name]
    score = 0
    if row["grp_key"] in spec.get("groups", ()):
        score += GROUP_POINTS
    if row["feed_id"] in spec.get("ids", ()):
        score += ID_POINTS
    if row["domain"] in spec.get("domains", ()):
        score += DOMAIN_POINTS
    hay = "%s %s" % (row["title"] or "", (row["summary"] or "")[:400])
    hits = sum(1 for rx in _COMPILED[name] if rx.search(hay))
    score += min(hits * KEYWORD_POINTS, MAX_KEYWORD_POINTS)
    return score


def assign(row, max_beats=2, floor=2):
    """Beats this item belongs to, best first. Empty when nothing fits.

    Preprints and journals are pinned to the research beat. The coverage map
    gives research 3% and robotics 10%; letting every cs.RO paper also occupy a
    robotics slot spends the robotics researcher's attention on literature
    instead of news.
    """
    # A single keyword is enough to route a cross-cutting wire. The pack is a
    # lead list the researcher filters, so recall beats precision here - a
    # missed item is invisible, a loose one costs one line of reading.
    scored = [(score_beat(n, row), PRIORITY.index(n), n) for n in BEATS]
    scored = [s for s in scored if s[0] >= floor]
    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], t[1]))

    if is_research(row):
        topical = [t for t in scored if t[2] != RESEARCH_BEAT
                   and t[0] >= SECONDARY_FLOOR]
        out = [RESEARCH_BEAT]
        if topical and max_beats > 1:
            out.append(topical[0][2])
        return out

    names = [n for _, _, n in scored[:1]]
    for sc, _, n in scored[1:max_beats]:
        if sc >= SECONDARY_FLOOR:
            names.append(n)
    return names
