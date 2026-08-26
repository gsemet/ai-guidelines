"""Contract tests for the standalone command surface."""

from click.testing import CliRunner

from ai_guidelines.cli import guidelines


def test_root_help_exposes_only_standalone_commands() -> None:
    """The installed executable has the documented root command surface."""
    result = CliRunner().invoke(guidelines, ["--help"])

    assert result.exit_code == 0
    assert "Usage: guidelines" in result.output
    assert "sync" in result.output
    assert "list" in result.output
    assert "search" in result.output
    assert "add" in result.output
    assert "remove" in result.output
    assert "outdated" in result.output
    assert "update" in result.output
    assert "cache" in result.output
    assert "plugins" not in result.output
    assert "guidelines guidelines" not in result.output


def test_short_help_alias_works_for_root_and_nested_commands() -> None:
    """Every command accepts both short and long help options."""
    runner = CliRunner()
    for arguments in (
        (),
        ("sync",),
        ("list",),
        ("search",),
        ("add",),
        ("remove",),
        ("outdated",),
        ("update",),
        ("cache",),
        ("cache", "clean"),
        ("cache", "prune"),
        ("cache", "dir"),
        ("cache", "size"),
    ):
        result = runner.invoke(guidelines, [*arguments, "-h"])
        assert result.exit_code == 0, (arguments, result.output)
        assert "Usage:" in result.output


def test_root_command_collection_is_exact() -> None:
    """No undocumented parent, plugin, or compatibility commands are exposed."""
    assert set(guidelines.commands) == {
        "sync",
        "list",
        "search",
        "add",
        "remove",
        "outdated",
        "update",
        "cache",
    }


def test_removed_parent_and_plugin_aliases_are_not_commands() -> None:
    """Unknown parent, plugin, and self-nested command paths are rejected."""
    runner = CliRunner()

    for arguments in (("plugins",), ("migrate",), ("guidelines", "sync")):
        result = runner.invoke(guidelines, list(arguments))
        assert result.exit_code != 0


def test_cache_help_exposes_only_maintenance_commands() -> None:
    """Cache help is part of the public standalone contract."""
    result = CliRunner().invoke(guidelines, ["cache", "--help"])

    assert result.exit_code == 0
    assert all(command in result.output for command in ("clean", "prune", "dir", "size"))
