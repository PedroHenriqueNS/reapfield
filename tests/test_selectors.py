from conftest import entry, fixture

from reapfield.extract import BAD_TYPE, extract
from reapfield.spec import parse_fields

URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
DOMAIN = "books.toscrape.com"


async def test_cached_selector_hit(cfg, cache, never_derive):
    """A pre-seeded cache extracts with zero LLM calls. This is the steady state."""
    fields = parse_fields("title, price:float, in_stock:bool")
    for f, sel in zip(fields, ["h1", ".price_color", ".instock.availability"]):
        cache.put(DOMAIN, f, "one", entry(sel))

    result = await extract(fixture("detail.html"), URL, fields, cfg, cache, never_derive)

    assert never_derive.count == 0
    assert result.llm_calls == 0
    assert result.records[0] == {
        "title": "A Light in the Attic",
        "price": 51.77,
        "in_stock": True,
    }


async def test_cached_miss_rederives(cfg, cache):
    """A selector matching nothing is a miss: derive once, overwrite the cache."""
    from conftest import StubDerive

    fields = parse_fields("title")
    cache.put(DOMAIN, fields[0], "one", entry(".this-class-does-not-exist"))
    derive = StubDerive({"title": entry("h1")})

    result = await extract(fixture("detail.html"), URL, fields, cfg, cache, derive)

    assert derive.count == 1
    assert result.records[0]["title"] == "A Light in the Attic"
    assert cache.get(DOMAIN, fields[0], "one").selector == "h1"


async def test_coercion_failure_is_a_miss(cfg, cache):
    """The selector still matches -- it just grabs the wrong node now.

    This is the entire invalidation mechanism: nothing expired, nothing 404'd,
    the value simply stopped being a float.
    """
    from conftest import StubDerive

    fields = parse_fields("price:float")
    # h1 matches fine -- it just yields "A Light in the Attic", not a price.
    cache.put(DOMAIN, fields[0], "one", entry("h1"))
    derive = StubDerive({"price": entry(".price_color")})

    result = await extract(fixture("detail.html"), URL, fields, cfg, cache, derive)

    assert derive.count == 1
    assert result.records[0]["price"] == 51.77
    assert cache.get(DOMAIN, fields[0], "one").selector == ".price_color"


async def test_bad_type_survives_a_failed_rederive(cfg, cache):
    """If re-derivation cannot fix it either, the field is reported, not faked."""
    from conftest import StubDerive

    fields = parse_fields("price:float")
    cache.put(DOMAIN, fields[0], "one", entry("h1"))
    derive = StubDerive({"price": entry("h1")})  # the model gets it wrong twice

    result = await extract(fixture("detail.html"), URL, fields, cfg, cache, derive)

    assert result.records[0]["price"] is None
    assert result.misses["price"] == BAD_TYPE


async def test_config_pin_is_never_rederived(cache, never_derive):
    """A hand-pinned selector is authoritative -- --refresh must not touch it."""
    from reapfield.config import Config, DomainConfig

    cfg = Config(
        refresh=True,
        use_cache=False,
        domains={DOMAIN: DomainConfig(selectors={"price": ".price_color"})},
    )
    fields = parse_fields("price:float")

    result = await extract(fixture("detail.html"), URL, fields, cfg, cache, never_derive)

    assert never_derive.count == 0
    assert result.records[0]["price"] == 51.77


async def test_pin_can_read_an_attribute(cache, never_derive):
    from reapfield.config import Config, DomainConfig

    cfg = Config(
        use_cache=False,
        domains={DOMAIN: DomainConfig(selectors={"title": "h3 a@title", "_row": "article.product_pod"})},
        mode="many",
    )
    result = await extract(
        fixture("listing.html"), "https://books.toscrape.com/", parse_fields("title"),
        cfg, cache, never_derive,
    )
    assert result.records[0]["title"] == "A Light in the Attic"
