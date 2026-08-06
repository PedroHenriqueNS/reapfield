import pytest

from reapfield.spec import build_model, coerce_text, parse_fields


def test_field_parsing():
    fields = parse_fields("title, price:float, in-stock:bool")
    assert [f.name for f in fields] == ["title", "price", "in_stock"]
    assert [f.type for f in fields] == [str, float, bool]
    assert fields[2].raw == "in-stock:bool"


def test_unannotated_is_str():
    assert parse_fields("headline")[0].type is str


@pytest.mark.parametrize("bad", ["", "  ,  ", "title:complex", "title, title"])
def test_rejected_specs(bad):
    with pytest.raises(ValueError):
        parse_fields(bad)


@pytest.mark.parametrize(
    "text,typ,expected",
    [
        ("£51.77", float, 51.77),
        ("1,234.5", float, 1234.5),
        ("22", int, 22),
        ("In stock (22 available)", bool, True),
        ("Out of stock", bool, False),
        ("  spaced  out ", str, "spaced out"),
    ],
)
def test_coercion(text, typ, expected):
    assert coerce_text(text, typ) == expected


def test_out_of_stock_is_not_read_as_available():
    # "not available" contains "available"; negations must be checked first.
    assert coerce_text("Currently not available", bool) is False


@pytest.mark.parametrize("text,typ", [("Add to basket", float), ("", str), ("maybe", bool)])
def test_coercion_failure_raises(text, typ):
    with pytest.raises(ValueError):
        coerce_text(text, typ)


def test_model_is_all_optional():
    model = build_model(parse_fields("title, price:float"))
    assert model().model_dump() == {"title": None, "price": None}
