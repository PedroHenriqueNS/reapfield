import tomllib
from importlib.metadata import version
from pathlib import Path

import reapfield

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _project() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]


def test_version_has_exactly_one_source():
    """A version literal in two files is a drift bug waiting to happen."""
    assert reapfield.__version__ == version("reapfield")
    assert reapfield.__version__ == _project()["version"]
    # The real version must not appear as a literal in the source. The
    # "0.0.0+unknown" fallback is allowed -- it is what an uninstalled source
    # tree reports, and it is never a released version.
    src = Path(reapfield.__file__).read_text()
    assert f'__version__ = "{reapfield.__version__}"' not in src


def test_metadata_is_complete_for_pypi():
    p = _project()
    assert p["license"] == "MIT"
    assert p["license-files"] == ["LICENSE"]
    assert p["authors"] == [
        {
            "name": "Pedro Henrique",
            "email": "106723520+PedroHenriqueNS@users.noreply.github.com",
        }
    ]
    assert p["keywords"]
    assert p["classifiers"]
    for key in ("Homepage", "Repository", "Issues", "Changelog"):
        assert key in p["urls"], f"missing project URL: {key}"
        assert "PedroHenriqueNS" in p["urls"][key]


def test_no_deprecated_license_classifier():
    """PEP 639 replaced these; setuptools and hatchling now warn on them."""
    assert not [c for c in _project()["classifiers"] if c.startswith("License ::")]


def test_package_is_marked_typed():
    assert (Path(reapfield.__file__).parent / "py.typed").is_file()
