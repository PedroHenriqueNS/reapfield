"""Shared test scaffolding. No network, no ANTHROPIC_API_KEY, ever."""

from __future__ import annotations

import pathlib

import pytest

from reapfield import fetch as fetch_mod
from reapfield.cache import SelectorCache, SelectorEntry
from reapfield.config import Config
from reapfield.spec import Field

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any test that reaches the real network fails loudly instead of hanging."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fetch_mod.reset()
    fetch_mod.TRANSPORT = None
    yield
    fetch_mod.reset()
    fetch_mod.TRANSPORT = None


@pytest.fixture
def cache(tmp_path) -> SelectorCache:
    return SelectorCache(root=tmp_path)


@pytest.fixture
def cfg(tmp_path) -> Config:
    """Config that never reads the developer's real ~/.config or ~/.cache."""
    return Config(use_cache=False)


class StubDerive:
    """Stands in for the LLM. Records every call so tests can count them."""

    def __init__(self, *responses: dict[str, SelectorEntry]):
        self.responses = list(responses)
        self.calls: list[tuple[str, list[str], str]] = []

    async def __call__(self, reduced_dom: str, fields: list[Field], mode: str):
        self.calls.append((reduced_dom, [f.name for f in fields], mode))
        if not self.responses:
            return {}
        nxt = self.responses.pop(0)
        # Only ever hand back entries for fields that were actually asked for.
        wanted = {f.name for f in fields}
        return {k: v for k, v in nxt.items() if k in wanted}

    @property
    def count(self) -> int:
        return len(self.calls)


@pytest.fixture
def never_derive() -> StubDerive:
    """A deriver that must not be called -- assert `.count == 0`."""
    return StubDerive()


def entry(selector: str, attr: str = "text", row: str | None = None) -> SelectorEntry:
    return SelectorEntry(selector=selector, attr=attr, row=row)
