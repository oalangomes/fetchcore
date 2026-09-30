from __future__ import annotations

from pathlib import Path

import pytest

from fetchcore.backends.opensearch import OpenSearchSettings
from fetchcore.backends.qdrant_vector import QdrantVectorSettings
from fetchcore.backends.solr import SolrSettings
from fetchcore.repository.document_policy import (
    DOCUMENT_INDEX_POLICY_PATH,
    LEGACY_DOCUMENT_INDEX_POLICY_PATH,
    RepositoryDocumentPolicyError,
    load_repository_document_index_policy,
)
from fetchcore.repository.git_source import (
    DEFAULT_REPOSITORY_CACHE,
    FETCHCORE_REPOSITORY_CACHE_ENV,
    LEGACY_REPOSITORY_CACHE_ENV,
    GitRepositorySourceProvider,
)
from fetchcore.repository.index_policy import (
    INDEXING_POLICY_PATH,
    LEGACY_INDEXING_POLICY_PATH,
    RepositoryIndexPolicyError,
    load_repository_index_policy,
)
from fetchcore.repository.indexing import DEFAULT_EXCLUDE_PATTERNS
from fetchcore.repository.knowledge import load_repository_knowledge


def _write(path: Path, content: str = "version: 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_backend_defaults_use_fetchcore_namespace() -> None:
    assert SolrSettings().collection == "fetchcore-knowledge"
    assert OpenSearchSettings().index == "fetchcore-knowledge"
    assert QdrantVectorSettings().collection == "fetchcore-vector"


def test_repository_cache_defaults_to_fetchcore_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(FETCHCORE_REPOSITORY_CACHE_ENV, raising=False)
    monkeypatch.delenv(LEGACY_REPOSITORY_CACHE_ENV, raising=False)

    provider = GitRepositorySourceProvider()

    assert provider.cache_root == DEFAULT_REPOSITORY_CACHE.resolve() / "git"


def test_fetchcore_repository_cache_env_precedes_legacy_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetchcore_root = tmp_path / "fetchcore"
    legacy_root = tmp_path / "legacy"
    monkeypatch.setenv(FETCHCORE_REPOSITORY_CACHE_ENV, str(fetchcore_root))
    monkeypatch.setenv(LEGACY_REPOSITORY_CACHE_ENV, str(legacy_root))

    provider = GitRepositorySourceProvider()

    assert provider.cache_root == fetchcore_root.resolve() / "git"


def test_legacy_repository_cache_env_remains_explicit_compatibility_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_root = tmp_path / "legacy"
    monkeypatch.delenv(FETCHCORE_REPOSITORY_CACHE_ENV, raising=False)
    monkeypatch.setenv(LEGACY_REPOSITORY_CACHE_ENV, str(legacy_root))

    provider = GitRepositorySourceProvider()

    assert provider.cache_root == legacy_root.resolve() / "git"


def test_index_policy_prefers_fetchcore_path(tmp_path: Path) -> None:
    canonical = _write(tmp_path / INDEXING_POLICY_PATH)

    policy = load_repository_index_policy(tmp_path)

    assert policy.source == canonical


def test_index_policy_reads_legacy_path_only_when_canonical_is_absent(
    tmp_path: Path,
) -> None:
    legacy = _write(tmp_path / LEGACY_INDEXING_POLICY_PATH)

    policy = load_repository_index_policy(tmp_path)

    assert policy.source == legacy


def test_index_policy_rejects_ambiguous_dual_namespace(tmp_path: Path) -> None:
    _write(tmp_path / INDEXING_POLICY_PATH)
    _write(tmp_path / LEGACY_INDEXING_POLICY_PATH)

    with pytest.raises(RepositoryIndexPolicyError, match="both FetchCore and legacy"):
        load_repository_index_policy(tmp_path)


def test_document_policy_prefers_fetchcore_path(tmp_path: Path) -> None:
    canonical = _write(tmp_path / DOCUMENT_INDEX_POLICY_PATH)

    policy = load_repository_document_index_policy(tmp_path)

    assert policy.source == canonical


def test_document_policy_reads_legacy_path_only_when_canonical_is_absent(
    tmp_path: Path,
) -> None:
    legacy = _write(tmp_path / LEGACY_DOCUMENT_INDEX_POLICY_PATH)

    policy = load_repository_document_index_policy(tmp_path)

    assert policy.source == legacy


def test_document_policy_rejects_ambiguous_dual_namespace(tmp_path: Path) -> None:
    _write(tmp_path / DOCUMENT_INDEX_POLICY_PATH)
    _write(tmp_path / LEGACY_DOCUMENT_INDEX_POLICY_PATH)

    with pytest.raises(RepositoryDocumentPolicyError, match="both FetchCore and legacy"):
        load_repository_document_index_policy(tmp_path)


def test_repository_knowledge_uses_fetchcore_namespace(tmp_path: Path) -> None:
    manifest_path = _write(
        tmp_path / ".fetchcore" / "repository-knowledge.yaml",
        "schema_version: 1\n",
    )

    manifest = load_repository_knowledge(tmp_path, include_global=False)

    assert manifest.loaded_from == (str(manifest_path),)


def test_repository_knowledge_reads_legacy_namespace_when_canonical_is_absent(
    tmp_path: Path,
) -> None:
    manifest_path = _write(
        tmp_path / ".agentsorch" / "repository-knowledge.yaml",
        "schema_version: 1\n",
    )

    manifest = load_repository_knowledge(tmp_path, include_global=False)

    assert manifest.loaded_from == (str(manifest_path),)


def test_repository_knowledge_rejects_ambiguous_dual_namespace(tmp_path: Path) -> None:
    _write(tmp_path / ".fetchcore" / "repository-knowledge.yaml", "schema_version: 1\n")
    _write(tmp_path / ".agentsorch" / "repository-knowledge.yaml", "schema_version: 1\n")

    with pytest.raises(ValueError, match="both FetchCore and legacy repository"):
        load_repository_knowledge(tmp_path, include_global=False)


def test_canonical_indexing_excludes_fetchcore_and_legacy_metadata() -> None:
    assert ".fetchcore" in DEFAULT_EXCLUDE_PATTERNS
    assert ".agentsorch" in DEFAULT_EXCLUDE_PATTERNS
