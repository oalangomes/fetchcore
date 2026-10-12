"""Check first-run CLI verbs documented in the launch README."""

from __future__ import annotations

from click.testing import CliRunner

from fetchcore.cli import main


def test_documented_commands_have_help_without_backend_setup() -> None:
    runner = CliRunner()
    for command in (
        ["--help"],
        ["repo", "add", "--help"],
        ["repo", "list", "--help"],
        ["index", "plan", "--help"],
        ["index", "rebuild", "--help"],
        ["index", "status", "--help"],
        ["search", "--help"],
        ["source", "--help"],
    ):
        result = runner.invoke(main, command)
        assert result.exit_code == 0, (command, result.output)


def test_search_is_repository_scoped() -> None:
    result = CliRunner().invoke(main, ["search", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output
    assert "required" in result.output.lower()
