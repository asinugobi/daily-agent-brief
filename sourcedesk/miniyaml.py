"""A deliberately tiny YAML reader for this project's config.yaml.

The venv carries no PyYAML and the pipeline is dependency-free by design, so
rather than add one this parses the exact subset config.yaml uses:

    key: scalar            # comment
    key:
      - list item
      - another

That is all. No nesting, no anchors, no flow collections, no multi-line
scalars. If config.yaml ever grows beyond this shape, add PyYAML and delete
this file rather than extending it - a half-YAML parser that silently
mis-reads a real document is worse than a dependency.
"""
import re

_LIST_ITEM = re.compile(r"^\s*-\s+(.*)$")
_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def _strip_comment(s):
    """Remove a trailing # comment that is not inside quotes."""
    out, quote = [], None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1].isspace()):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def load(path):
    data, current = {}, None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = _strip_comment(raw.rstrip("\n"))
            if not line.strip():
                continue
            m = _LIST_ITEM.match(line)
            if m and current is not None:
                data[current].append(_scalar(m.group(1)))
                continue
            m = _KEY.match(line)
            if not m:
                continue
            key = m.group(1)
            val_str = m.group(2).strip()
            if val_str == "":
                data[key] = []
                current = key
            else:
                data[key] = _scalar(val_str)
                current = None
    return data
