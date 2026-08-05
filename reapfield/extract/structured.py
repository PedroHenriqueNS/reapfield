"""Deterministic extractors: JSON-LD, meta tags, __NEXT_DATA__, __NUXT__, inline JSON.

Field matching is a general rule, never a lookup table: flatten to dotted paths,
normalize both sides, match on the final path segment, shallowest wins. So
`offers.price` matches `price` by the same rule that `spec.coolant_temp_c`
matches `coolant_temp_c`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from ..spec import Field

_NUXT = re.compile(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*(?:</script>|$)", re.S)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _blobs(tree: LexborHTMLParser) -> list[Any]:
    """Every JSON object we can lift out of the page, best sources first."""
    out: list[Any] = []
    for node in tree.css('script[type="application/ld+json"]'):
        if (b := _loads(node.text())) is not None:
            out.append(b)
    for node in tree.css("script#__NEXT_DATA__"):
        if (b := _loads(node.text())) is not None:
            out.append(b)
    for node in tree.css("script"):
        raw = node.text() or ""
        if "__NUXT__" in raw and (m := _NUXT.search(raw)):
            if (b := _loads(m.group(1))) is not None:
                out.append(b)
    for node in tree.css('script[type="application/json"]'):
        if (b := _loads(node.text())) is not None:
            out.append(b)
    return out


def _meta(tree: LexborHTMLParser) -> dict[str, Any]:
    """og:title -> path 'og.title', so the final segment is 'title'.

    The bare <title> element is deliberately NOT a source. It is almost always
    "Product Name | Site Name", so trusting it would hand a `title` field the
    site's branding and, worse, shadow the real per-page selector.
    """
    flat: dict[str, Any] = {}
    for node in tree.css("meta"):
        key = node.attributes.get("property") or node.attributes.get("name")
        content = node.attributes.get("content")
        if key and content:
            flat.setdefault(key.replace(":", "."), content)
    return flat


def _flatten(obj: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("@") and k != "@type":
                continue
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten(v, f"{prefix}.{i}" if prefix else str(i), out)
    elif obj is not None and prefix:
        out.setdefault(prefix, obj)
    return out


def _match(flat: dict[str, Any], fields: list[Field]) -> dict[str, Any]:
    """Shallowest path whose final segment matches the field name."""
    targets = {f.name: _norm(f.name) for f in fields}
    best: dict[str, tuple[int, Any]] = {}
    for path, val in flat.items():
        if isinstance(val, (dict, list)):
            continue
        seg = _norm(path.split(".")[-1])
        depth = path.count(".")
        for name, target in targets.items():
            if seg == target and depth < best.get(name, (10**6, None))[0]:
                best[name] = (depth, val)
    return {f.name: best[f.name][1] if f.name in best else None for f in fields}


def _items(blob: Any) -> list[dict] | None:
    """An ItemList, or a bare array of same-@type objects."""
    if isinstance(blob, dict):
        if graph := blob.get("@graph"):
            return _items(graph)
        el = blob.get("itemListElement")
        if isinstance(el, list) and el:
            items = [e.get("item", e) if isinstance(e, dict) else e for e in el]
            return [i for i in items if isinstance(i, dict)]
        return None
    if isinstance(blob, list) and len(blob) >= 2:
        objs = [b for b in blob if isinstance(b, dict)]
        if len(objs) >= 2:
            types = {o.get("@type") for o in objs}
            if len(types) == 1:
                return objs
    return None


def extract(html: str, fields: list[Field]) -> tuple[list[dict], str | None]:
    """(records, detected_mode). Mode is None when nothing was found."""
    tree = LexborHTMLParser(html)
    blobs = _blobs(tree)

    for blob in blobs:
        items = _items(blob)
        if items and len(items) >= 2:
            recs = [_match(_flatten(it), fields) for it in items]
            if any(any(v is not None for v in r.values()) for r in recs):
                return recs, "many"

    flat: dict[str, Any] = {}
    for blob in blobs:
        for k, v in _flatten(blob).items():
            flat.setdefault(k, v)
    for k, v in _meta(tree).items():
        flat.setdefault(k, v)

    rec = _match(flat, fields)
    if any(v is not None for v in rec.values()):
        return [rec], "one"
    return [], None
