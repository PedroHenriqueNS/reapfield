import pytest

from reapfield.cache import SelectorEntry
from reapfield.errors import DerivationFailed
from reapfield.extract.derive import _parse, build_prompt, derive
from reapfield.spec import parse_fields


def test_parses_a_bare_json_reply():
    out = _parse('{"row": null, "fields": {"title": {"selector": "h1", "attr": "text"}}}')
    assert out["fields"]["title"]["selector"] == "h1"


def test_parses_through_a_code_fence():
    out = _parse('```json\n{"row": null, "fields": {}}\n```')
    assert out == {"row": None, "fields": {}}


def test_parses_json_wrapped_in_prose():
    out = _parse('Sure! Here you go:\n{"row": ".card", "fields": {}}\nHope that helps.')
    assert out["row"] == ".card"


def test_prompt_names_every_field_and_its_type():
    prompt = build_prompt("<html/>", parse_fields("title, price:float"), "auto")
    assert "title (type: str)" in prompt
    assert "price (type: float)" in prompt


async def test_api_failure_is_not_reported_as_a_missing_field(monkeypatch):
    """A dead API must not masquerade as "the field is not on the page".

    Those two send you to completely different places -- one to the billing
    page, the other into the page's HTML.
    """
    import anthropic
    import httpx

    class Boom:
        def __init__(self, *a, **kw):
            self.messages = self

        async def create(self, **kw):
            raise anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", Boom)

    with pytest.raises(DerivationFailed, match="Anthropic API"):
        await derive("<html/>", parse_fields("title"), "auto", model="claude-haiku-4-5-20251001")


def test_derived_entries_are_marked_as_llm_sourced():
    assert SelectorEntry(selector="h1").source == "llm"
