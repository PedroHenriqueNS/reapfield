import pytest
from conftest import StubDerive, entry, fixture

from reapfield.errors import ModeConflict
from reapfield.extract import extract
from reapfield.spec import parse_fields

DETAIL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
LISTING = "https://books.toscrape.com/"
DOMAIN = "books.toscrape.com"


async def test_partial_extraction(cfg, cache):
    """2 of 4 fields found: the record still ships, with reasons for the rest."""
    fields = parse_fields("title, price:float, isbn, publisher")
    derive = StubDerive({"title": entry("h1"), "price": entry(".price_color")})

    result = await extract(fixture("detail.html"), DETAIL, fields, cfg, cache, derive)

    assert result.records[0]["title"] == "A Light in the Attic"
    assert result.records[0]["price"] == 51.77
    assert result.records[0]["isbn"] is None
    assert result.records[0]["publisher"] is None
    assert set(result.misses) == {"isbn", "publisher"}


async def test_cardinality_detection_many(cfg, cache):
    """The listing fixture holds 20 products; a row selector must find all of them."""
    fields = parse_fields("title, price:float")
    derive = StubDerive(
        {
            "title": entry("h3 a", attr="@title", row="article.product_pod"),
            "price": entry(".price_color", row="article.product_pod"),
        }
    )

    result = await extract(fixture("listing.html"), LISTING, fields, cfg, cache, derive)

    assert result.mode == "many"
    assert len(result.records) == 20
    assert result.records[0] == {"title": "A Light in the Attic", "price": 51.77}


async def test_cardinality_detection_one(cfg, cache):
    fields = parse_fields("title")
    derive = StubDerive({"title": entry("h1")})
    result = await extract(fixture("detail.html"), DETAIL, fields, cfg, cache, derive)
    assert result.mode == "one"
    assert len(result.records) == 1


async def test_forced_one_on_a_listing_is_an_error(cache):
    """--one against a listing must fail loudly, never silently keep row 1."""
    from reapfield.config import Config

    cfg = Config(mode="one", use_cache=False)
    derive = StubDerive({"title": entry("h3 a", attr="@title", row="article.product_pod")})

    with pytest.raises(ModeConflict):
        await extract(fixture("listing.html"), LISTING, parse_fields("title"), cfg, cache, derive)


async def test_mode_is_part_of_the_cache_key(cfg, cache):
    """A detail selector and a listing-row selector must not collide."""
    fields = parse_fields("title")
    cache.put(DOMAIN, fields[0], "one", entry("h1"))
    cache.put(DOMAIN, fields[0], "many", entry("h3 a", attr="@title", row="article.product_pod"))

    assert cache.get(DOMAIN, fields[0], "one").selector == "h1"
    assert cache.get(DOMAIN, fields[0], "many").selector == "h3 a"


async def test_cached_many_entries_replay_without_the_llm(cfg, cache, never_derive):
    """Second run on a listing: mode comes back from the cache, no LLM call."""
    fields = parse_fields("title, price:float")
    cache.put(DOMAIN, fields[0], "many", entry("h3 a", attr="@title", row="article.product_pod"))
    cache.put(DOMAIN, fields[1], "many", entry(".price_color", row="article.product_pod"))

    result = await extract(fixture("listing.html"), LISTING, fields, cfg, cache, never_derive)

    assert never_derive.count == 0
    assert result.mode == "many"
    assert len(result.records) == 20


async def test_no_llm_reports_instead_of_calling(cache, never_derive):
    from reapfield.config import Config

    cfg = Config(no_llm=True, use_cache=False)
    result = await extract(
        fixture("detail.html"), DETAIL, parse_fields("title"), cfg, cache, never_derive
    )
    assert never_derive.count == 0
    assert "title" in result.misses


async def test_llm_budget_caps_the_calls(cfg, cache):
    """One derivation, one re-derivation after the coercion guard. Never a third."""
    fields = parse_fields("price:float")
    cache.put(DOMAIN, fields[0], "one", entry("h1"))
    derive = StubDerive(
        {"price": entry("h1")}, {"price": entry("h1")}, {"price": entry("h1")}
    )

    await extract(fixture("detail.html"), DETAIL, fields, cfg, cache, derive)

    assert derive.count <= cfg.max_llm_calls == 2


# --- partial structured data -------------------------------------------------
#
# JSON-LD that carries a field on some items and not others. Treating the field
# as "done" because one item had it is how data the page contained goes missing.

PARTIAL_JSONLD = """<html><body>
<script type="application/ld+json">
{"@type": "ItemList", "itemListElement": [
  {"@type": "Product", "name": "One", "price": "10.00"},
  {"@type": "Product", "name": "Two"},
  {"@type": "Product", "name": "Three"}]}
</script>
<div class="row"><span class="n">One</span><span class="p">10.00</span></div>
<div class="row"><span class="n">Two</span><span class="p">20.00</span></div>
<div class="row"><span class="n">Three</span><span class="p">30.00</span></div>
</body></html>"""

LIST_URL = "https://shop.example/all"


async def test_partly_filled_structured_field_still_runs_selectors(cfg, cache):
    """price is in the JSON-LD for item 1 only; the HTML has all three."""
    fields = parse_fields("name, price:float")
    derive = StubDerive({"price": entry(".p", row=".row")})

    result = await extract(PARTIAL_JSONLD, LIST_URL, fields, cfg, cache, derive)

    assert [r["price"] for r in result.records] == [10.0, 20.0, 30.0]
    assert result.misses == {}


async def test_unfillable_gap_is_reported_as_partial(cache, never_derive):
    """No way to fill the other two -> say so, rather than exit 0 on half the data."""
    from reapfield.config import Config

    cfg = Config(no_llm=True, use_cache=False)
    result = await extract(PARTIAL_JSONLD, LIST_URL, parse_fields("name, price:float"), cfg, cache, never_derive)

    assert [r["price"] for r in result.records] == [10.0, None, None]
    assert result.misses["price"] == "found on 1 of 3 records"


# --- the limit of the coercion guard -----------------------------------------


async def test_wrong_str_selector_is_not_detectable(cfg, cache, never_derive):
    """Honest documentation of a real limit -- do NOT "fix" this with a heuristic.

    `.price_color` is plainly not a title, but any non-empty text is a valid
    `str`. Telling otherwise would mean knowing what "title" means, and field
    names are arbitrary (spec.py, "Invariant"). So this stays cached.
    """
    fields = parse_fields("title")
    cache.put(DOMAIN, fields[0], "one", entry(".price_color"))

    result = await extract(fixture("detail.html"), DETAIL, fields, cfg, cache, never_derive)

    assert never_derive.count == 0
    assert result.records[0]["title"] == "£51.77"
    assert cache.get(DOMAIN, fields[0], "one").selector == ".price_color"


async def test_declaring_a_type_is_what_buys_detection(cfg, cache):
    """The same wrong node, on a typed field, does re-derive. That is the trade."""
    fields = parse_fields("price:float")
    cache.put(DOMAIN, fields[0], "one", entry("h1"))
    derive = StubDerive({"price": entry(".price_color")})

    result = await extract(fixture("detail.html"), DETAIL, fields, cfg, cache, derive)

    assert derive.count == 1
    assert result.records[0]["price"] == 51.77
