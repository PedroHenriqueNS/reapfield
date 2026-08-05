import httpx
import pytest

from reapfield import report
from reapfield.config import Config


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _no_results(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"total_count": 0, "items": []})


def test_environment_captures_what_triage_needs():
    env = report.environment()
    for key in ("reapfield", "python", "platform", "machine"):
        assert key in env, f"missing environment key: {key}"
    assert env["reapfield"]


def test_environment_never_contains_credentials(monkeypatch):
    """A report is pasted into a public issue. Nothing secret may ride along."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRETVALUE")
    blob = " ".join(report.environment().values())
    assert "SECRETVALUE" not in blob
    assert "sk-ant-" not in blob


async def test_prepare_renders_every_required_section():
    async with _client(_no_results) as c:
        r = await report.prepare(
            "Boom", "Run reapfield on X", "Off-by-one in Y", "Guard the index", client=c
        )
    for heading in ("## Summary", "## How to reproduce", "## Cause",
                    "## Suggested fix", "## Environment"):
        assert heading in r.body, f"missing section: {heading}"
    assert "Boom" in r.body and "Guard the index" in r.body
    assert r.duplicate is False
    assert r.search_failed is False
    assert r.submit_url.startswith("https://github.com/PedroHenriqueNS/reapfield/issues/new")


async def test_prepare_detects_a_duplicate():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "total_count": 1,
            "items": [{
                "html_url": "https://github.com/PedroHenriqueNS/reapfield/issues/42",
                "title": "Boom on listing pages",
                "state": "closed",
            }],
        })

    async with _client(handler) as c:
        r = await report.prepare("Boom", "repro", "cause", "fix", client=c)
    assert r.duplicate is True
    assert r.existing_issue_url.endswith("/42")
    assert r.existing_issue_title == "Boom on listing pages"


@pytest.mark.parametrize("failure", [
    lambda rq: httpx.Response(403, json={"message": "rate limit exceeded"}),
    lambda rq: httpx.Response(422, json={"message": "Validation Failed"}),
])
async def test_search_failure_is_never_reported_as_no_duplicate(failure):
    """Degrading silently to "no duplicate" is how a tracker fills with the
    same bug ten times."""
    async with _client(failure) as c:
        r = await report.prepare("Boom", "repro", "cause", "fix", client=c)
    assert r.search_failed is True
    assert r.duplicate is False
    assert "could not be checked" in r.body.lower()


async def test_network_error_also_sets_search_failed():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    async with _client(boom) as c:
        r = await report.prepare("Boom", "repro", "cause", "fix", client=c)
    assert r.search_failed is True


async def test_opt_out_disables_preparation():
    cfg = Config(contribute_reports="never")
    async with _client(_no_results) as c:
        r = await report.prepare("Boom", "repro", "cause", "fix", cfg=cfg, client=c)
    assert r.reports_enabled is False
    assert r.submit_url == ""
    assert r.body == ""


def test_env_var_overrides_config(monkeypatch, tmp_path):
    from reapfield.config import load

    # Stub the global config dir too. load() always merges
    # ~/.config/reapfield/config.toml, so without this the test reads whatever
    # the developer happens to have there.
    monkeypatch.setattr("reapfield.config.config_dir", lambda: tmp_path)
    monkeypatch.setenv("REAPFIELD_ISSUE_REPORTS", "never")
    assert load(local=tmp_path / "none.toml").contribute_reports == "never"
