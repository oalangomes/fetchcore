from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from fetchcore import cli
from fetchcore.application import indexing
from fetchcore.retrieval.config import RetrievalConfig


class _Runtime:
    repository = "oalangomes/fetchcore"
    revision = "abc123"
    fingerprint = "sha256:test"
    indexer = object()


class _Settings:
    projection_version = "3"


class _Store:
    settings = _Settings()

    def validate_schema(self, *, require_vectors: bool = False) -> None:
        assert require_vectors is False


def _context(tmp_path: Path) -> cli._IndexCommandContext:
    return cli._IndexCommandContext(
        workspace=tmp_path,
        backend="solr",
        runtime=_Runtime(),  # type: ignore[arg-type]
        store=_Store(),  # type: ignore[arg-type]
    )


def test_index_bootstrap_validates_explicit_schema(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli,
        "_build_index_context",
        lambda **_: _context(tmp_path),
    )
    result = CliRunner().invoke(
        cli.main,
        ["index", "--repository", "oalangomes/fetchcore", "bootstrap"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "bootstrap"
    assert payload["result"]["validated"] is True


def test_index_bootstrap_resolves_shared_vector_runtime_config(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    config = tmp_path / "runtime.yaml"
    config.write_text(
        "version: 1\nruntime:\n  backend: solr\n"
        "retrieval:\n  dense:\n    enabled: true\n"
        "    embedding:\n      provider: ollama\n"
        "      model: embeddinggemma\n      dimensions: 768\n"
        "backends:\n  solr:\n    url: http://solr:8983/solr\n"
        "    collection: vector-ready\n"
        "embedding:\n  ollama:\n    url: http://ollama:11434\n",
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    def build(**values: object) -> cli._IndexCommandContext:
        observed.update(values)
        return _context(tmp_path)

    monkeypatch.setattr(cli, "_build_index_context", build)  # type: ignore[attr-defined]
    result = CliRunner().invoke(
        cli.main,
        [
            "index",
            "--config",
            str(config),
            "--workspace",
            str(tmp_path),
            "--repository",
            "oalangomes/fetchcore",
            "bootstrap",
        ],
    )

    assert result.exit_code == 0, result.output
    retrieval = observed["retrieval"]
    assert isinstance(retrieval, RetrievalConfig)
    assert retrieval.mode == "dense"
    assert retrieval.dense.embedding.dimensions == 768
    assert observed["backend_settings"] == {
        "url": "http://solr:8983/solr",
        "collection": "vector-ready",
    }


def test_index_status_emits_machine_readable_json(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli,
        "_build_index_context",
        lambda **_: _context(tmp_path),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        indexing,
        "repository_index_status",
        lambda *_: {"state": "ready", "reason": "up_to_date"},
    )

    result = CliRunner().invoke(
        cli.main,
        ["index", "--repository", "oalangomes/fetchcore", "status"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "status"
    assert payload["status"] == "succeeded"
    assert payload["result"]["state"] == "ready"


def test_index_dry_run_emits_planned_mutations(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli,
        "_build_index_context",
        lambda **_: _context(tmp_path),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        indexing,
        "dry_run_repository_index",
        lambda *_: {
            "mode": "incremental",
            "noop": False,
            "planned_mutations": {"updated": 1},
        },
    )

    result = CliRunner().invoke(
        cli.main,
        ["index", "--repository", "oalangomes/fetchcore", "dry-run"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["result"]["mode"] == "incremental"
    assert payload["result"]["planned_mutations"]["updated"] == 1


def test_index_sync_uses_targeted_incremental_synchronizer(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli,
        "_build_index_context",
        lambda **_: _context(tmp_path),
    )
    observed: dict[str, object] = {}

    class _Outcome:
        def to_mapping(self) -> dict[str, object]:
            return {
                "mode": "incremental",
                "noop": False,
                "added": 0,
                "updated": 1,
                "deleted": 0,
            }

    class _Synchronizer:
        def __init__(self, store: object, indexer: object) -> None:
            observed["store"] = store
            observed["indexer"] = indexer

        def sync(self, workspace: Path) -> _Outcome:
            observed["workspace"] = workspace
            return _Outcome()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        indexing,
        "TargetedGitAwareSolrSynchronizer",
        _Synchronizer,
    )

    result = CliRunner().invoke(
        cli.main,
        ["index", "--repository", "oalangomes/fetchcore", "sync"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["result"]["mode"] == "incremental"
    assert payload["result"]["updated"] == 1
    assert observed["workspace"] == tmp_path


def test_index_backend_settings_must_be_json_object() -> None:
    result = CliRunner().invoke(
        cli.main,
        [
            "index",
            "--repository",
            "oalangomes/fetchcore",
            "--backend-settings",
            "[]",
            "status",
        ],
    )

    assert result.exit_code != 0
    assert "backend settings must contain a JSON object" in result.output
