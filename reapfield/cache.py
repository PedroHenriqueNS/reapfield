"""Two caches with deliberately different lifetimes.

SelectorCache has NO TTL: a selector that still works is still correct, and
expiring it just buys LLM calls. It is invalidated by *failure* only -- see
extract/__init__.py, which drops an entry that matched nothing, or whose value
failed the declared type.

Note the limit that follows from that, because it is not obvious: an untyped
(`str`) field whose selector starts matching the wrong element is not a
detectable failure -- any non-empty text is a valid `str`, and it will stay
cached until `--refresh`. Declaring a type is what makes a wrong-node selector
self-correcting. extract/__init__.py's module docstring has the full rule.

ResponseCache DOES have a TTL, because page content genuinely changes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .config import cache_dir
from .spec import Field


@dataclass
class SelectorEntry:
    selector: str
    attr: str = "text"  # "text" | "@href" | "@content" | ...
    row: str | None = None  # repeating container; set in list mode
    derived_at: str = ""  # informational only -- NOT a TTL
    source: Literal["llm", "config"] = "llm"
    # The key is a hash, so without these a cache listing is unreadable.
    # Stamped on put(); both default so pre-existing cache files still load.
    field: str = ""
    mode: str = ""

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")


def _safe(domain: str) -> str:
    return "".join(c if c.isalnum() or c in ".-_" else "_" for c in domain) or "_"


def _key(domain: str, f: Field, mode: str) -> str:
    raw = "\x00".join([domain, f.name, f.type.__name__, mode])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class SelectorCache:
    """One JSON file per domain under ~/.cache/reapfield/selectors/."""

    def __init__(self, root: Path | None = None):
        self.root = (root or cache_dir()) / "selectors"
        self._loaded: dict[str, dict] = {}

    def _path(self, domain: str) -> Path:
        return self.root / f"{_safe(domain)}.json"

    def _data(self, domain: str) -> dict:
        if domain not in self._loaded:
            p = self._path(domain)
            try:
                self._loaded[domain] = json.loads(p.read_text())
            except (OSError, ValueError):
                self._loaded[domain] = {}
        return self._loaded[domain]

    def _flush(self, domain: str) -> None:
        p = self._path(domain)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._data(domain), indent=2, sort_keys=True))

    def get(self, domain: str, f: Field, mode: str) -> SelectorEntry | None:
        raw = self._data(domain).get(_key(domain, f, mode))
        return SelectorEntry(**raw) if raw else None

    def put(self, domain: str, f: Field, mode: str, entry: SelectorEntry) -> None:
        if not entry.derived_at:
            entry.derived_at = SelectorEntry.now()
        entry.field, entry.mode = f.name, mode
        self._data(domain)[_key(domain, f, mode)] = asdict(entry)
        self._flush(domain)

    def drop(self, domain: str, f: Field, mode: str) -> None:
        if self._data(domain).pop(_key(domain, f, mode), None) is not None:
            self._flush(domain)

    def all_for(self, domain: str) -> dict[str, dict]:
        return dict(self._data(domain))


class ResponseCache:
    """Fetched HTML keyed by URL, with a TTL. Skipped entirely when disabled."""

    def __init__(self, root: Path | None = None, ttl: int = 3600, enabled: bool = True):
        self.root = (root or cache_dir()) / "responses"
        self.ttl = ttl
        self.enabled = enabled

    def _path(self, url: str) -> Path:
        return self.root / f"{hashlib.sha256(url.encode()).hexdigest()}.json"

    def get(self, url: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            blob = json.loads(self._path(url).read_text())
        except (OSError, ValueError):
            return None
        if time.time() - blob.get("fetched_at", 0) > self.ttl:
            return None
        return blob

    def put(self, url: str, status: int, html: str, via: str) -> None:
        if not self.enabled:
            return
        p = self._path(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "url": url,
                    "status": status,
                    "html": html,
                    "via": via,
                    "fetched_at": time.time(),
                }
            )
        )
