"""Config: ~/.config/reapfield/config.toml, then ./reapfield.toml overriding per-key.

Nothing here ever writes config. Derived selectors live in the cache, which is a
separate store -- see SPEC.md, "Cache vs config".
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT_UA = "reapfield/0.1 (+https://github.com/PedroSilvaDry/reapfield)"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "reapfield"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "reapfield"


@dataclass
class DomainConfig:
    fetcher: str = "auto"  # auto | http | browser
    rate_limit: float | None = None  # requests/sec
    wait_for: str | None = None  # browser mode: selector to await
    pagination: str | None = None  # --many: next-page link
    selectors: dict[str, str] = field(default_factory=dict)  # pinned, authoritative


@dataclass
class Config:
    concurrency: int = 4
    user_agent: str = DEFAULT_UA
    domains: dict[str, DomainConfig] = field(default_factory=dict)

    # Runtime flags, set from CLI/MCP -- not read from the TOML.
    mode: str = "auto"  # auto | one | many
    no_llm: bool = False
    max_llm_calls: int = 2
    refresh: bool = False
    use_cache: bool = True
    cache_ttl: int = 3600
    model: str = DEFAULT_MODEL
    scroll: int = 0
    paginate: int = 0
    timeout: float = 20.0

    def for_domain(self, domain: str) -> DomainConfig:
        return self.domains.get(domain, DomainConfig())


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(local: Path | None = None, **overrides) -> Config:
    """Global config, then ./reapfield.toml, then explicit runtime overrides."""
    local = Path("reapfield.toml") if local is None else local
    data = _merge(_read(config_dir() / "config.toml"), _read(local))

    domains = {
        name: DomainConfig(
            fetcher=d.get("fetcher", "auto"),
            rate_limit=d.get("rate_limit"),
            wait_for=d.get("wait_for"),
            pagination=d.get("pagination"),
            selectors=dict(d.get("selectors", {})),
        )
        for name, d in (data.get("domains") or {}).items()
    }

    cfg = Config(
        concurrency=data.get("concurrency", 4),
        user_agent=data.get("user_agent", DEFAULT_UA),
        domains=domains,
        model=os.environ.get("REAPFIELD_LLM_MODEL", DEFAULT_MODEL),
    )
    return replace(cfg, **{k: v for k, v in overrides.items() if v is not None})
