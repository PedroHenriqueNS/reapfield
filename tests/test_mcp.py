import re
import socket

import pytest
from conftest import StubDerive, entry, fixture

from reapfield import fetch as fetch_mod
from reapfield import mcp_server
from reapfield.errors import UnsafeURL

URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


@pytest.fixture
def served(monkeypatch):
    """Serve the detail fixture in place of the network, for both entry points."""

    async def fake_fetch(url, cfg, responses=None):
        return fetch_mod.Response(url=url, status=200, html=fixture("detail.html"), via="http")

    monkeypatch.setattr("reapfield.fetch_mod.fetch", fake_fetch)
    monkeypatch.setenv("REAPFIELD_MCP_ALLOW_PRIVATE", "1")  # skip DNS in tests


@pytest.fixture
def stub_llm(monkeypatch):
    derive = StubDerive(
        {"title": entry("h1"), "price": entry(".price_color"), "in_stock": entry(".instock.availability")}
    )
    monkeypatch.setattr("reapfield._default_deriver", lambda cfg: derive)
    return derive


async def test_tool_wraps_core(served, stub_llm, monkeypatch, tmp_path):
    """The MCP tool and the CLI path must produce identical field values.

    If logic ever drifts into one adapter and not the other, this is what catches it.
    """
    from reapfield import scrape as scrape_core
    from reapfield.cache import SelectorCache
    from reapfield.config import Config

    monkeypatch.setattr("reapfield.cache.cache_dir", lambda: tmp_path)

    via_core = await scrape_core(
        URL, "title, price:float, in_stock:bool", Config(use_cache=False),
        cache=SelectorCache(root=tmp_path), derive=stub_llm,
    )
    via_tool = await mcp_server.scrape(URL, "title, price:float, in_stock:bool")

    assert via_tool.records == via_core.records
    assert via_tool.mode == via_core.mode
    assert via_tool.records[0]["price"] == 51.77


async def test_partial_is_not_an_error(served, monkeypatch, tmp_path):
    """2 of 4 fields comes back as a result with `misses`, not a raised exception."""
    monkeypatch.setattr("reapfield.cache.cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "reapfield._default_deriver",
        lambda cfg: StubDerive({"title": entry("h1"), "price": entry(".price_color")}),
    )

    result = await mcp_server.scrape(URL, "title, price:float, isbn, publisher")

    assert result.records[0]["title"] == "A Light in the Attic"
    assert result.records[0]["isbn"] is None
    assert set(result.misses) == {"isbn", "publisher"}


# --- trust boundary ---------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "data:text/html,<h1>x</h1>",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # the one that matters
        "http://10.0.0.5/admin",
        "http://[::1]/",
    ],
)
def test_url_validation_rejects(bad, monkeypatch):
    monkeypatch.delenv("REAPFIELD_MCP_ALLOW_PRIVATE", raising=False)
    with pytest.raises(UnsafeURL):
        mcp_server.check_url(bad)


def test_hostname_resolving_to_private_is_rejected(monkeypatch):
    """String blocklists lose to DNS. We check the resolved IP, so rebinding loses."""
    monkeypatch.delenv("REAPFIELD_MCP_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(UnsafeURL, match=re.escape("127.0.0.1")):
        mcp_server.check_url("http://totally-innocent.example.com/")


def test_allow_private_opts_out(monkeypatch):
    monkeypatch.setenv("REAPFIELD_MCP_ALLOW_PRIVATE", "1")
    mcp_server.check_url("http://127.0.0.1:8080/")  # must not raise


def test_public_host_passes(monkeypatch):
    monkeypatch.delenv("REAPFIELD_MCP_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    mcp_server.check_url("https://example.com/page")


def test_all_three_tools_are_registered():
    assert {t.name for t in mcp_server.mcp._tool_manager.list_tools()} == {
        "scrape",
        "list_cached_selectors",
        "refresh_selectors",
    }
