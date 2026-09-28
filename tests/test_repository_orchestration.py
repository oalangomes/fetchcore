from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor as StandardThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest

from fetchcore.repository import orchestration as repository_orchestration
from fetchcore.repository.orchestration import (
    RepositoryOrchestrationError,
    load_repository_orchestration_manifest,
    orchestrate_repository_indexes,
)


def _write_manifest(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def _workspace(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    return path


def test_manifest_resolves_relative_sources_and_purpose_collections(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "repo-a")
    manifest_path = _write_manifest(
        tmp_path / "config/repositories.yaml",
        f"""
version: 1
max_concurrency: 3
purposes:
  engineering:
    projection:
      backend: solr
      collection: agentsorch-code
repositories:
  - name: repo-a
    purpose: engineering
    source:
      provider: local-workspace
      path: ../{workspace.name}
""",
    )

    manifest = load_repository_orchestration_manifest(manifest_path)

    assert manifest.max_concurrency == 3
    assert manifest.purposes["engineering"].collection == "agentsorch-code"
    assert manifest.repositories[0].source == workspace.resolve()


def test_manifest_rejects_shared_collection_across_purposes(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        """
version: 1
purposes:
  code:
    projection:
      backend: solr
      collection: agentsorch-shared
  docs:
    projection:
      backend: solr
      collection: agentsorch-shared
repositories:
  - name: repo-a
    purpose: code
    source:
      provider: local-workspace
      path: ./repo-a
""",
    )

    with pytest.raises(RepositoryOrchestrationError, match="distinct collection"):
        load_repository_orchestration_manifest(manifest_path)


def test_manifest_rejects_duplicate_target_names(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        """
version: 1
purposes:
  code:
    projection:
      backend: solr
      collection: agentsorch-code
repositories:
  - name: duplicate
    purpose: code
    source:
      provider: local-workspace
      path: ./repo-a
  - name: duplicate
    purpose: code
    source:
      provider: local-workspace
      path: ./repo-b
""",
    )

    with pytest.raises(RepositoryOrchestrationError, match="names must be unique"):
        load_repository_orchestration_manifest(manifest_path)


@pytest.mark.parametrize(
    ("purpose_projection", "repository_purpose", "expected_error"),
    [
        ("projection:\n      backend: solr", "code", "requires non-empty 'collection'"),
        (
            "projection:\n      backend: solr\n      collection: agentsorch-code",
            "unknown",
            "references unknown purpose 'unknown'",
        ),
    ],
)
def test_manifest_requires_known_purpose_with_explicit_collection(
    tmp_path: Path,
    purpose_projection: str,
    repository_purpose: str,
    expected_error: str,
) -> None:
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        f"""
version: 1
purposes:
  code:
    {purpose_projection}
repositories:
  - name: repo-a
    purpose: {repository_purpose}
    source:
      provider: local-workspace
      path: ./repo-a
""",
    )

    with pytest.raises(RepositoryOrchestrationError, match=expected_error):
        load_repository_orchestration_manifest(manifest_path)


@pytest.mark.parametrize(
    "purpose_line",
    ["", "    purpose: ''"],
    ids=("missing", "empty"),
)
def test_manifest_requires_non_empty_repository_purpose(
    tmp_path: Path,
    purpose_line: str,
) -> None:
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        f"""
version: 1
purposes:
  code:
    projection:
      backend: solr
      collection: agentsorch-code
repositories:
  - name: repo-a
{purpose_line}
    source:
      provider: local-workspace
      path: ./repo-a
""",
    )

    with pytest.raises(
        RepositoryOrchestrationError,
        match="requires non-empty 'purpose'",
    ):
        load_repository_orchestration_manifest(manifest_path)


def test_orchestration_isolates_prepare_and_execution_failures(tmp_path: Path) -> None:
    good_a = _workspace(tmp_path, "good-a")
    good_b = _workspace(tmp_path, "good-b")
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        f"""
version: 1
max_concurrency: 2
purposes:
  code:
    projection:
      backend: solr
      collection: agentsorch-code
repositories:
  - name: good-a
    purpose: code
    source:
      provider: local-workspace
      path: {good_a}
  - name: missing
    purpose: code
    source:
      provider: local-workspace
      path: {tmp_path / 'missing'}
  - name: good-b
    purpose: code
    source:
      provider: local-workspace
      path: {good_b}
  - name: disabled
    enabled: false
    purpose: code
    source:
      provider: local-workspace
      path: {tmp_path / 'disabled'}
""",
    )
    manifest = load_repository_orchestration_manifest(manifest_path)

    def runner(item: object) -> object:
        name = item.target.name  # type: ignore[attr-defined]
        if name == "good-b":
            raise RuntimeError("fixture execution failure")
        return {"repository": item.runtime.repository}  # type: ignore[attr-defined]

    payload = orchestrate_repository_indexes(manifest, runner=runner)  # type: ignore[arg-type]
    repositories = payload["repositories"]

    assert payload["status"] == "partial_failure"
    assert payload["summary"] == {
        "total": 4,
        "succeeded": 1,
        "failed": 2,
        "skipped": 1,
    }
    assert isinstance(repositories, list)
    assert [item["status"] for item in repositories] == [
        "succeeded",
        "failed",
        "failed",
        "skipped",
    ]
    assert repositories[1]["phase"] == "prepare"
    assert repositories[2]["phase"] == "execute"


def test_duplicate_projection_target_is_blocked_before_runner(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "same-repo")
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        f"""
version: 1
purposes:
  code:
    projection:
      backend: solr
      collection: agentsorch-code
repositories:
  - name: first
    purpose: code
    source:
      provider: local-workspace
      path: {workspace}
  - name: second
    purpose: code
    source:
      provider: local-workspace
      path: {workspace}
""",
    )
    manifest = load_repository_orchestration_manifest(manifest_path)
    calls: list[str] = []

    def runner(item: object) -> object:
        calls.append(item.target.name)  # type: ignore[attr-defined]
        return {}

    payload = orchestrate_repository_indexes(manifest, runner=runner)  # type: ignore[arg-type]
    repositories = payload["repositories"]

    assert calls == []
    assert payload["status"] == "failed"
    assert isinstance(repositories, list)
    assert [item["error"]["category"] for item in repositories] == [
        "duplicate_projection_target",
        "duplicate_projection_target",
    ]


def test_execution_is_bounded_and_serialized_per_collection(tmp_path: Path) -> None:
    repo_a = _workspace(tmp_path, "repo-a")
    repo_b = _workspace(tmp_path, "repo-b")
    repo_c = _workspace(tmp_path, "repo-c")
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        f"""
version: 1
max_concurrency: 3
purposes:
  code:
    projection:
      backend: solr
      collection: agentsorch-code
  docs:
    projection:
      backend: solr
      collection: agentsorch-docs
repositories:
  - name: repo-a
    purpose: code
    source:
      provider: local-workspace
      path: {repo_a}
  - name: repo-b
    purpose: code
    source:
      provider: local-workspace
      path: {repo_b}
  - name: repo-c
    purpose: docs
    source:
      provider: local-workspace
      path: {repo_c}
""",
    )
    manifest = load_repository_orchestration_manifest(manifest_path)

    guard = Lock()
    a_started = Event()
    c_started = Event()
    active_global = 0
    max_global = 0
    active_by_collection: dict[str, int] = {}
    max_by_collection: dict[str, int] = {}

    def runner(item: object) -> object:
        nonlocal active_global, max_global
        name = item.target.name  # type: ignore[attr-defined]
        collection = item.purpose.collection  # type: ignore[attr-defined]
        with guard:
            active_global += 1
            max_global = max(max_global, active_global)
            active_by_collection[collection] = active_by_collection.get(collection, 0) + 1
            max_by_collection[collection] = max(
                max_by_collection.get(collection, 0),
                active_by_collection[collection],
            )
        try:
            if name == "repo-a":
                a_started.set()
                assert c_started.wait(timeout=2)
            elif name == "repo-c":
                c_started.set()
                assert a_started.wait(timeout=2)
            return {"name": name}
        finally:
            with guard:
                active_global -= 1
                active_by_collection[collection] -= 1

    payload = orchestrate_repository_indexes(manifest, runner=runner)  # type: ignore[arg-type]

    assert payload["status"] == "succeeded"
    assert max_global >= 2
    assert max_global <= manifest.max_concurrency
    assert max_by_collection["agentsorch-code"] == 1
    assert max_by_collection["agentsorch-docs"] == 1
    assert payload["scheduling"] == {
        "global_concurrency": 3,
        "per_collection_concurrency": 1,
    }


def test_execution_uses_the_global_concurrency_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces = [_workspace(tmp_path, f"repo-{index}") for index in range(4)]
    manifest_path = _write_manifest(
        tmp_path / "repositories.yaml",
        f"""
version: 1
max_concurrency: 2
purposes:
  purpose-0:
    projection:
      backend: solr
      collection: agentsorch-0
  purpose-1:
    projection:
      backend: solr
      collection: agentsorch-1
  purpose-2:
    projection:
      backend: solr
      collection: agentsorch-2
  purpose-3:
    projection:
      backend: solr
      collection: agentsorch-3
repositories:
  - name: repo-0
    purpose: purpose-0
    source:
      provider: local-workspace
      path: {workspaces[0]}
  - name: repo-1
    purpose: purpose-1
    source:
      provider: local-workspace
      path: {workspaces[1]}
  - name: repo-2
    purpose: purpose-2
    source:
      provider: local-workspace
      path: {workspaces[2]}
  - name: repo-3
    purpose: purpose-3
    source:
      provider: local-workspace
      path: {workspaces[3]}
""",
    )
    manifest = load_repository_orchestration_manifest(manifest_path)
    configured_workers: list[int] = []

    class RecordingThreadPoolExecutor(StandardThreadPoolExecutor):
        def __init__(self, *args: object, **kwargs: object) -> None:
            configured_workers.append(int(kwargs["max_workers"]))
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        repository_orchestration,
        "ThreadPoolExecutor",
        RecordingThreadPoolExecutor,
    )
    pair = Barrier(2)
    guard = Lock()
    active = 0
    max_active = 0

    def runner(item: object) -> object:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        try:
            pair.wait(timeout=2)
            return {"name": item.target.name}  # type: ignore[attr-defined]
        finally:
            with guard:
                active -= 1

    payload = orchestrate_repository_indexes(manifest, runner=runner)  # type: ignore[arg-type]

    assert payload["status"] == "succeeded"
    assert configured_workers == [2]
    assert max_active == 2
