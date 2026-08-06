"""Shrink a page to something worth paying an LLM to read.

The big win is collapsing repeated siblings: a 20-product listing costs about
the same as a 2-product one, and the collapse is also what makes the repeating
row container obvious to the model.
"""

from __future__ import annotations

import re
from collections import Counter

from selectolax.lexbor import LexborHTMLParser, LexborNode

CAP = 30_000
TEXT_LIMIT = 120
URL_LIMIT = 60
COLLAPSE_AFTER = 3  # >3 identical siblings -> keep 2, summarize the rest

DROP_TAGS = frozenset(
    {"script", "style", "svg", "noscript", "iframe", "head", "link", "meta", "template"}
)
KEEP_ATTRS = ("id", "class", "itemprop", "data-testid", "href", "src", "alt", "title")
VOID_TAGS = frozenset({"img", "br", "hr", "input", "source", "col"})


def _escape(v: str) -> str:
    """Page content is attacker-controlled, and this output is a prompt.

    A `class` of `"><!-- +99 more identical -->` interpolated raw forges the
    collapse marker that derive.py presents to the model as strong evidence of a
    listing -- flipping cardinality, and caching a bogus `row` selector with no
    TTL behind it. `<` and `"` are the two characters that break out.
    """
    return v.replace("<", "&lt;").replace('"', "&quot;")


def _sig(node: LexborNode) -> tuple[str, str]:
    return (node.tag, node.attributes.get("class") or "")


def _render(node: LexborNode, depth: int, max_depth: int) -> str:
    tag = node.tag

    if tag == "-text":
        text = re.sub(r"\s+", " ", node.text_content or "").strip()
        return _escape(text[:TEXT_LIMIT])  # text can forge the same marker

    # selectolax names pseudo-nodes "-text" / "-comment"; anything with a "-"
    # prefix other than text is markup we do not want in the payload.
    if tag in DROP_TAGS or tag.startswith("-"):
        return ""
    if depth > max_depth:
        return ""

    attrs = {}
    for k in KEEP_ATTRS:
        v = node.attributes.get(k)
        if not v:
            continue
        attrs[k] = _escape(v[:URL_LIMIT] if k in ("href", "src") else v)

    kids = list(node.iter(include_text=True))
    counts = Counter(_sig(k) for k in kids if k.tag != "-text")
    shown: Counter = Counter()
    body: list[str] = []

    for kid in kids:
        if kid.tag == "-text":
            if piece := _render(kid, depth + 1, max_depth):
                body.append(piece)
            continue
        sig = _sig(kid)
        if counts[sig] > COLLAPSE_AFTER:
            shown[sig] += 1
            if shown[sig] == COLLAPSE_AFTER:
                body.append(f"<!-- +{counts[sig] - 2} more identical -->")
            if shown[sig] > 2:
                continue
        if piece := _render(kid, depth + 1, max_depth):
            body.append(piece)

    inner = "".join(body)
    if not inner and not attrs:
        return ""  # no text, nothing addressable -- useless to the model

    astr = "".join(f' {k}="{v}"' for k, v in attrs.items())
    if tag in VOID_TAGS:
        return f"<{tag}{astr}>"
    return f"<{tag}{astr}>{inner}</{tag}>"


def reduce_dom(html: str, cap: int = CAP) -> str:
    """Reduced markup, guaranteed <= cap bytes."""
    tree = LexborHTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return ""

    out = _render(root, 0, 10**6)
    if len(out) <= cap:
        return out

    # Over cap: shed the deepest subtrees first. Binary search the depth that fits.
    lo, hi, best = 1, 40, ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _render(root, 0, mid)
        if len(candidate) <= cap:
            best, lo = candidate, mid + 1
        else:
            hi = mid - 1
    return best or out[:cap]
