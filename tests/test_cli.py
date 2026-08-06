import pytest

from reapfield import __version__
from reapfield.cli import build_parser, main


def test_version_flag_prints_the_version(capsys):
    """SECURITY.md and the bug template both tell reporters to run this."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_usage_error_exits_two():
    with pytest.raises(SystemExit) as exc:
        main(["https://example.com"])  # missing --fields
    assert exc.value.code == 2


def test_bad_field_type_is_a_usage_error(capsys):
    assert main(["https://example.com", "--fields", "price:complex"]) == 2
    assert "unknown type" in capsys.readouterr().err


def test_one_and_many_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["u", "--fields", "t", "--one", "--many"])


def test_no_flag_is_accepted_and_then_ignored():
    """-v/--verbose parsed fine and changed nothing, which is worse than no flag.

    Every option the parser accepts must reach `load()` or be read in `main()`.
    """
    import inspect

    from reapfield import cli

    source = inspect.getsource(cli.main)
    declared = {
        a.dest
        for a in cli.build_parser()._actions
        if a.dest not in ("help", "version", "url", "fields", "format")
    }
    unused = {d for d in declared if f"args.{d}" not in source}
    assert unused == set(), f"declared but never read: {sorted(unused)}"
