from conftest import fixture

from reapfield.extract import extract
from reapfield.spec import parse_fields


async def test_jsonld_hit(cfg, cache, never_derive):
    """JSON-LD fills every field, so the LLM is never asked."""
    fields = parse_fields("title, price:float, rating:float")
    result = await extract(
        fixture("jsonld.html"), "https://example.com/p/1", fields, cfg, cache, never_derive
    )

    assert result.llm_calls == 0
    assert never_derive.count == 0
    assert result.mode == "one"
    assert result.records[0] == {"title": "Ghost in the Wires", "price": 21.9, "rating": 4.5}
    assert result.misses == {}


async def test_nested_path_matches_on_final_segment(cfg, cache, never_derive):
    """`offers.price` matches a field called `price` -- shallowest path wins."""
    fields = parse_fields("price:float")
    result = await extract(
        fixture("jsonld.html"), "https://example.com/p/1", fields, cfg, cache, never_derive
    )
    assert result.records[0]["price"] == 21.9


async def test_field_names_are_arbitrary(cfg, cache, never_derive):
    """The invariant: no field name is special anywhere in the codebase.

    `reactor_id` and `coolant_temp_c` must travel the exact code path that
    `title` and `price` do. This test fails the moment someone adds a
    KNOWN_FIELDS table or a per-field heuristic.
    """
    fields = parse_fields("reactor_id, coolant_temp_c:float, control_rods_inserted:int")
    result = await extract(
        fixture("reactor.html"), "https://example.com/unit/4", fields, cfg, cache, never_derive
    )

    assert result.llm_calls == 0
    assert result.records[0] == {
        "reactor_id": "RBMK-1000",
        "coolant_temp_c": 284.6,
        "control_rods_inserted": 211,
    }
