"""Tolerant feed parsing: RSS 2.0, RSS 1.0/RDF, Atom, and JSON Feed.

No feedparser in the target environment, so this handles the shapes directly.
Namespaces are stripped rather than matched, because real feeds are inconsistent
about which namespace they declare for the same element.
"""
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.entities import name2codepoint
from xml.etree import ElementTree as ET

from . import canon

UTC = timezone.utc

# XML defines only five entities; feeds routinely emit HTML ones (&nbsp;, &mdash;)
# which make a strict parser fail the whole document. The Federal Reserve G.17
# feed does exactly this. Rewrite named entities to numeric before parsing.
_ENTITY_RE = re.compile(rb"&([A-Za-z][A-Za-z0-9]{1,31});")
_XML_SAFE = {b"amp", b"lt", b"gt", b"quot", b"apos"}


def _fix_entities(body):
    def repl(m):
        name = m.group(1)
        if name in _XML_SAFE:
            return m.group(0)
        cp = name2codepoint.get(name.decode("ascii", "ignore"))
        if cp is None:
            return b"&amp;" + name + b";"   # unknown: keep visible, stay valid
        return ("&#%d;" % cp).encode("ascii")
    return _ENTITY_RE.sub(repl, body)


def _local(tag):
    """'{http://www.w3.org/2005/Atom}entry' -> 'entry'"""
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _first(el, names):
    """First direct-ish child whose local name matches, searching shallowly."""
    for child in el:
        if _local(child.tag) in names:
            return child
    return None


def _text(el, names):
    c = _first(el, names)
    if c is None:
        return ""
    if c.text and c.text.strip():
        return c.text
    # Atom allows nested XHTML content
    inner = "".join(ET.tostring(x, encoding="unicode") for x in c)
    return inner or (c.text or "")


def _link(el):
    """RSS uses <link>text</link>; Atom uses <link href= rel=alternate/>."""
    best, fallback = "", ""
    for child in el:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href:
            rel = (child.get("rel") or "alternate").lower()
            if rel == "alternate" and not best:
                best = href
            elif not fallback:
                fallback = href
        elif child.text and child.text.strip() and not best:
            best = child.text.strip()
    if best:
        return best
    if fallback:
        return fallback
    # RSS 1.0 sometimes puts the URL on rdf:about
    for k, v in el.attrib.items():
        if _local(k) == "about":
            return v
    return ""


_ISO_CLEAN = re.compile(r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def parse_date(s):
    """RFC 822 or ISO 8601 -> aware UTC datetime, or None. Python 3.9 safe."""
    if not s:
        return None
    s = s.strip()
    # RFC 822 / 1123, the RSS norm
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError):
        pass
    # ISO 8601. 3.9's fromisoformat rejects 'Z' and fractional-second variants.
    t = s.replace("Z", "+00:00").replace("z", "+00:00")
    t = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", t)
    for candidate in (t, re.sub(r"\.\d+", "", t)):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d",
                "%d %b %Y", "%b %d, %Y", "%Y%m%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 6].strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


TITLE = {"title"}
DESC = {"description", "summary", "subtitle", "content", "encoded"}
DATE = {"pubdate", "published", "updated", "date", "modified", "created",
        "issued", "lastbuilddate"}
GUID = {"guid", "id", "identifier"}
ITEM = {"item", "entry"}


class NotAFeed(Exception):
    """Body parsed, but it is data rather than an item stream."""


def parse(body, source_url=""):
    """bytes -> list of dicts. Raises NotAFeed for non-feed payloads."""
    if not body:
        return []
    head = body[:400].lstrip()
    if head[:1] in (b"{", b"["):
        return _parse_json(body)
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        # Recover in escalating steps: strip BOM/preamble, then repair the
        # HTML entities that strict XML rejects.
        cleaned = body.lstrip().lstrip(b"\xef\xbb\xbf")
        cut = cleaned.find(b"<")
        if cut > 0:
            cleaned = cleaned[cut:]
        root = None
        for attempt in (cleaned, _fix_entities(cleaned)):
            try:
                root = ET.fromstring(attempt)
                break
            except ET.ParseError as e:
                err = e
        if root is None:
            raise NotAFeed("xml parse error: %s" % err)

    if _local(root.tag) == "html":
        raise NotAFeed("html document, not a feed")

    items = [el for el in root.iter() if _local(el.tag) in ITEM]
    if not items:
        raise NotAFeed("no <item>/<entry> elements")

    out = []
    for el in items:
        title = canon.clean_text(_text(el, TITLE))
        link = _link(el)
        if not title and not link:
            continue
        guid = canon.clean_text(_text(el, GUID)) or link or title
        raw_date = ""
        for child in el:
            if _local(child.tag) in DATE and child.text:
                raw_date = child.text.strip()
                break
        out.append({
            "guid": guid[:500],
            "url": link[:1000],
            "title": title[:600],
            "summary": canon.clean_text(_text(el, DESC))[:1200],
            "published": parse_date(raw_date),
        })
    return out


def _parse_json(body):
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except ValueError as e:
        raise NotAFeed("json parse error: %s" % e)
    # JSON Feed (jsonfeed.org)
    if isinstance(doc, dict) and isinstance(doc.get("items"), list) and doc.get("version"):
        out = []
        for it in doc["items"]:
            if not isinstance(it, dict):
                continue
            out.append({
                "guid": str(it.get("id") or it.get("url") or "")[:500],
                "url": str(it.get("url") or "")[:1000],
                "title": canon.clean_text(it.get("title") or "")[:600],
                "summary": canon.clean_text(
                    it.get("summary") or it.get("content_text") or "")[:1200],
                "published": parse_date(it.get("date_published") or it.get("date_modified") or ""),
            })
        return out
    raise NotAFeed("json payload is data, not a feed (route to the API queue)")
