"""Apply cached/derived CSS selectors. No LLM, no network -- pure replay."""

from __future__ import annotations

from selectolax.lexbor import LexborNode

from ..cache import SelectorEntry

ROW_KEY = "_row"  # config-pinnable repeating container


def value_of(node: LexborNode, attr: str) -> str | None:
    """'text' -> text content; '@href' -> that attribute."""
    if not attr or attr == "text":
        return node.text(strip=True)
    if attr.startswith("@"):
        return node.attributes.get(attr[1:])
    return node.attributes.get(attr)


def pick(scope: LexborNode, entry: SelectorEntry) -> str | None:
    """None means the selector matched nothing -- which is a cache miss."""
    try:
        node = scope.css_first(entry.selector)
    except Exception:  # a malformed selector from a model is expected input
        return None
    return value_of(node, entry.attr) if node is not None else None


def rows(scope: LexborNode, selector: str) -> list[LexborNode]:
    try:
        return scope.css(selector)
    except Exception:  # a malformed selector from a model is expected input
        return []
