"""Canonicalisation: URLs, titles, and the token sets clustering runs on.

Dedupe quality lives here. Two feeds carrying one story rarely agree on the URL
(tracking params, AMP variants) or the title (publisher suffixes, casing), so
both are normalised to a comparable form before anything is compared.
"""
import re
import html
import unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# Params that identify a referral, not a document.
_DROP_PARAM_EXACT = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ref", "referer", "referrer", "source", "src", "cmpid", "campaign_id",
    "ito", "ncid", "guccounter", "guce_referrer", "guce_referrer_sig",
    "sh", "srnd", "leadSource", "smid", "partner", "cvid", "ei",
    "__twitter_impression", "s_kwcid", "spm", "share", "shareId",
    "taid", "teaser", "at_medium", "at_campaign", "at_custom1",
    "CMP", "cmp", "amp", "output", "utm",
}
_DROP_PARAM_PREFIX = ("utm_", "at_", "pk_", "piwik_", "matomo_", "_hs", "hsa_")

_AMP_SUFFIXES = ("/amp", "/amp/", ".amp", "/amp.html", "?amp=1")

# Short tokens that carry real meaning in this subject area and must survive the
# length filter that removes ordinary noise words.
_KEEP_SHORT = {
    "ai", "ml", "ev", "eu", "us", "uk", "un", "gp", "hp", "5g", "6g",
    "fed", "cpi", "ppi", "gdp", "sec", "api", "gpu", "cpu", "tpu", "llm",
    "irs", "fda", "ftc", "doj", "eia", "iea", "bls", "bea", "imf", "ecb",
    "boe", "rba", "oecd", "nato", "opec", "vc", "ipo", "m&a", "13f", "10k",
}

_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "to", "in", "on", "at", "by", "for", "with", "from",
    "as", "is", "are", "was", "were", "be", "been", "being", "it", "its", "his",
    "her", "their", "our", "your", "my", "we", "you", "they", "he", "she",
    "will", "would", "can", "could", "should", "may", "might", "must", "has",
    "have", "had", "do", "does", "did", "not", "no", "so", "up", "out", "off",
    "over", "under", "into", "about", "after", "before", "more", "most", "some",
    "such", "only", "own", "same", "just", "now", "new", "says", "said", "say",
    "amid", "amid", "via", "vs", "how", "why", "what", "when", "where", "who",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^a-z0-9&+#]+")

# " ... | The Verge", " ... - Reuters", " ... — Bloomberg"
_SUFFIX_RE = re.compile(r"\s+[|–—-]\s+[^|–—-]{2,30}$")

# Publishers disagree about how to write the same entity, which silently splits
# clusters that should merge ("Fed" vs "Federal Reserve", "data center" vs
# "datacenter"). Normalising the phrase before tokenising fixes it at the root.
_PHRASE_ALIASES = [
    (re.compile(r"\bfederal reserve\b"), "fed"),
    (re.compile(r"\bartificial intelligence\b"), "ai"),
    (re.compile(r"\bmachine learning\b"), "ml"),
    (re.compile(r"\bdata cent(er|re)s?\b"), "datacenter"),
    (re.compile(r"\bdatacent(er|re)s?\b"), "datacenter"),
    (re.compile(r"\blarge language models?\b"), "llm"),
    (re.compile(r"\bconsumer price index\b"), "cpi"),
    (re.compile(r"\bgross domestic product\b"), "gdp"),
    (re.compile(r"\bnonfarm payrolls?\b"), "payrolls"),
    (re.compile(r"\bsecurities and exchange commission\b"), "sec"),
    (re.compile(r"\beuropean central bank\b"), "ecb"),
    (re.compile(r"\bbank of england\b"), "boe"),
    (re.compile(r"\bself[- ]driving\b"), "autonomous"),
    (re.compile(r"\bdriverless\b"), "autonomous"),
    (re.compile(r"\brobotaxis?\b"), "robotaxi"),
    (re.compile(r"\bhumanoids?\b"), "humanoid"),
]


def canon_url(url):
    """Normalise a URL so the same document from two feeds compares equal."""
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower()
    if not parts.scheme:
        return url.lower()

    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"

    path = parts.path or "/"
    for suf in _AMP_SUFFIXES:
        if path.endswith(suf):
            path = path[: -len(suf)] or "/"
            break
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    keep = []
    for k, v in parse_qsl(parts.query, keep_blank_values=False):
        lk = k.lower()
        if lk in {p.lower() for p in _DROP_PARAM_EXACT}:
            continue
        if any(lk.startswith(p) for p in _DROP_PARAM_PREFIX):
            continue
        keep.append((k, v))
    query = urlencode(sorted(keep))

    return urlunsplit((scheme, host, path, query, ""))


def clean_text(s):
    """Strip HTML, unescape entities, normalise unicode and whitespace."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = unicodedata.normalize("NFKC", s)
    # curly quotes and dashes to ASCII so titles compare cleanly
    s = (s.replace("‘", "'").replace("’", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-").replace("…", "..."))
    return _WS_RE.sub(" ", s).strip()


def norm_title(title):
    """Lowercased, publisher-suffix-stripped title for exact-match dedupe."""
    t = clean_text(title)
    # Strip a trailing " - Publisher" only when what remains is still a title.
    # Three words is the floor: "OpenAI releases GPT-X | The Verge" must lose
    # its suffix, and a four-word floor was silently keeping it.
    stripped = _SUFFIX_RE.sub("", t)
    if len(stripped.split()) >= 3:
        t = stripped
    return _WS_RE.sub(" ", t.lower()).strip()


def _apply_aliases(s):
    for pat, repl in _PHRASE_ALIASES:
        s = pat.sub(repl, s)
    return s


def tokens(title, summary="", cap=40):
    """Significant token set used for near-duplicate and event clustering."""
    base = norm_title(title)
    if summary:
        base += " " + clean_text(summary).lower()[:400]
    base = _apply_aliases(base)
    out = []
    for raw in _NONWORD_RE.split(base):
        if not raw:
            continue
        if raw in _KEEP_SHORT:
            out.append(raw)
            continue
        if raw in _STOP or len(raw) < 4:
            continue
        out.append(_stem(raw))
        if len(out) >= cap:
            break
    return set(out)


def _stem(w):
    """Crude suffix trim - enough to match releases/release, reported/report."""
    for suf in ("ingly", "edly", "ations", "ation", "ings", "ing", "ies",
                "ied", "ers", "er", "ed", "es", "s"):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def overlap(a, b):
    """Containment coefficient: inter / smaller set.

    Jaccard alone under-scores a short headline against a long one carrying the
    same event, because the longer set inflates the union. Overlap catches those
    without loosening Jaccard enough to start merging unrelated stories.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))
