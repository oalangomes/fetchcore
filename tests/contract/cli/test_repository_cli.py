from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from fetchcore import cli
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.repository.registry import RegisteredRepository, RepositoryRegistryError


def invoke(tmp_path, *args):
    return CliRunner().invoke(
        cli.main,
        [
            "repo",
            "--cache-root",
            str(tmp_path / "cache"),
            "--allowed-root",
            str(tmp_path),
            *args,
        ],
    )


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("add", ["path", "--provider", "git"]),
        ("list", []),
        ("inspect", ["owner/repo"]),
    ],
)
def test_command_routes_only_through_application(tmp_path, monkeypatch, command, arguments):
    observed = []
    record = RegisteredRepository(
        "owner/repo",
        "local-workspace",
        "/workspace",
        "file:///workspace",
        None,
        None,
        "/workspace",
        None,
    )

    class Service:
        def __init__(self, **kwargs):
            assert kwargs == {"cache_root": tmp_path / "cache", "allowed_roots": (tmp_path,)}

        def add(self, source, *, provider):
            observed.append(("add", source, provider))
            return record

        def list(self):
            observed.append(("list",))
            return (record,)

        def inspect(self, reference):
            observed.append(("inspect", reference))
            return record

        def present(self, record):
            return record.to_mapping()

    monkeypatch.setattr(cli, "RepositoryApplicationService", Service)
    result = invoke(tmp_path, command, *arguments, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == command
    assert payload["status"] == "succeeded"
    assert (
        observed
        == {
            "add": [("add", "path", "git")],
            "list": [("list",)],
            "inspect": [("inspect", "owner/repo")],
        }[command]
    )


def test_cli_add_inspect_list_and_identical_repeat_output(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = invoke(tmp_path, "add", str(workspace), "--json")
    repeated = invoke(tmp_path, "add", str(workspace), "--json")
    assert first.exit_code == repeated.exit_code == 0, first.output
    assert first.output == repeated.output
    record = json.loads(first.output)["result"]
    inspect = invoke(tmp_path, "inspect", record["repository"], "--json")
    listed = invoke(tmp_path, "list", "--json")
    assert inspect.exit_code == listed.exit_code == 0
    assert json.loads(inspect.output)["result"] == record
    assert json.loads(listed.output)["result"] == [record]
    assert record["registration_status"] == "registered"
    assert record["materialization_state"] == "present"
    human = invoke(tmp_path, "inspect", record["repository"])
    for key, value in record.items():
        assert f"{key}={value if value is not None else 'unavailable'}" in human.output


def test_list_empty_and_ordered_json(tmp_path):
    empty = invoke(tmp_path, "list", "--json")
    assert empty.exit_code == 0
    assert json.loads(empty.output)["result"] == []
    assert invoke(tmp_path, "list").output == "No registered repositories.\n"
    for name in ["z", "a"]:
        source = tmp_path / name
        source.mkdir()
        assert invoke(tmp_path, "add", str(source)).exit_code == 0
    listed = invoke(tmp_path, "list", "--json")
    assert [r["repository"] for r in json.loads(listed.output)["result"]] == ["a", "z"]
    assert listed.output == invoke(tmp_path, "list", "--json").output


@pytest.mark.parametrize(
    ("command", "reference", "category"),
    [
        ("inspect", "unknown", "repository_not_found"),
        ("inspect", "../escape", "invalid_repository_reference"),
        ("add", "../escape", "unsafe_source"),
        ("add", "ftp://example.com/repo", "unsupported_source"),
        ("add", "https://user:secret@example.com/repo", "unsafe_source"),
    ],
)
def test_cli_stable_error_categories_without_secrets(tmp_path, command, reference, category):
    result = invoke(tmp_path, command, reference, "--json")
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"]["category"] == category
    assert "secret" not in result.output
    human = invoke(tmp_path, command, reference)
    assert human.exit_code == 1
    assert category in human.output
    assert "secret" not in human.output


@pytest.mark.parametrize("command", ["add", "list", "inspect"])
def test_registry_failure_uses_structured_output(tmp_path, monkeypatch, command):
    def fail(*args, **kwargs):
        raise RepositoryRegistryError("registry_persistence_failure", "Unable to persist registry")

    monkeypatch.setattr(RepositoryApplicationService, command, fail)
    args = [] if command == "list" else ["source"]
    result = invoke(tmp_path, command, *args, "--json")
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["category"] == "registry_persistence_failure"


def test_operator_cache_environment_and_explicit_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("FETCHCORE_REPOSITORY_CACHE", str(tmp_path / "canonical"))
    monkeypatch.setenv("AGENTSORCH_REPOSITORY_CACHE", str(tmp_path / "legacy"))
    assert RepositoryApplicationService().registry.path == tmp_path / "canonical" / "registry.json"
    assert RepositoryApplicationService(cache_root=tmp_path / "explicit").registry.path == (
        tmp_path / "explicit" / "registry.json"
    )
    monkeypatch.delenv("FETCHCORE_REPOSITORY_CACHE")
    assert RepositoryApplicationService().registry.path == tmp_path / "legacy" / "registry.json"


def test_help_exposes_repo_and_keeps_index_commands():
    runner = CliRunner()
    assert runner.invoke(cli.main, ["--help"]).exit_code == 0
    help_text = runner.invoke(cli.main, ["repo", "--help"]).output
    assert all(name in help_text for name in ("add", "list", "inspect"))
    index_help = runner.invoke(cli.main, ["index", "--help"]).output
    assert all(name in index_help for name in ("bootstrap", "status", "dry-run", "sync"))


def test_cache_symlink_loop_has_structured_failure(tmp_path):
    cache = tmp_path / "cache"
    cache.symlink_to(cache)
    result = invoke(tmp_path, "list", "--json")
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"]["category"] == "unsafe_source"
