from reapfield.config import DEFAULT_UA, load


def test_user_agent_points_at_the_real_repository():
    """The UA is sent to every site we touch. A dead URL there is a broken
    link in someone else's access logs."""
    assert "PedroHenriqueNS/reapfield" in DEFAULT_UA
    assert "PedroSilvaDry" not in DEFAULT_UA
    assert DEFAULT_UA.startswith("reapfield/")


def test_user_agent_is_honest_and_identifiable():
    assert "+https://github.com/" in DEFAULT_UA
    assert "Mozilla" not in DEFAULT_UA  # never impersonate a browser


def test_default_config_loads_without_files(tmp_path, monkeypatch):
    monkeypatch.setattr("reapfield.config.config_dir", lambda: tmp_path)
    cfg = load(local=tmp_path / "nonexistent.toml")
    assert cfg.concurrency == 4
    assert cfg.user_agent == DEFAULT_UA
