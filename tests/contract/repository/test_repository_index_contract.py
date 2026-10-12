from __future__ import annotations

import re
from pathlib import Path

from pytest import MonkeyPatch

from fetchcore.repository import index_fingerprint
from fetchcore.repository.document_indexing import DocumentRepositoryIndexer
from fetchcore.repository.document_policy import RepositoryDocumentIndexPolicy
from fetchcore.repository.index_contract import (
    IndexContractSolrSnapshot,
    indexing_contract_fingerprint,
)
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.structure import default_structural_symbol_extractors
from fetchcore.backends.solr import SOLR_PROJECTION_VERSION, SolrIndexSnapshot


def _indexer(**overrides: object) -> RepositoryIndexer:
    values: dict[str, object] = {
        "repository": "example/repo",
        "branch": "main",
        "revision": "rev-a",
    }
    values.update(overrides)
    return RepositoryIndexer(
        RepositoryIndexConfig(**values),  # type: ignore[arg-type]
        symbol_extractors=default_structural_symbol_extractors(),
    )


def test_fingerprint_ignores_repository_observation_identity() -> None:
    first = indexing_contract_fingerprint(_indexer())
    second = indexing_contract_fingerprint(
        _indexer(repository="another/repo", branch="feature", revision="rev-b")
    )

    assert first == second


def test_fingerprint_changes_when_corpus_policy_changes() -> None:
    baseline = indexing_contract_fingerprint(_indexer())
    changed_chunking = indexing_contract_fingerprint(_indexer(max_chunk_lines=80))
    changed_markdown_budget = indexing_contract_fingerprint(
        _indexer(max_markdown_chunk_chars=32_000)
    )
    changed_excludes = indexing_contract_fingerprint(
        _indexer(exclude_patterns=("node_modules", "generated", "vendor"))
    )

    assert changed_chunking != baseline
    assert changed_markdown_budget != baseline
    assert changed_excludes != baseline
    assert changed_chunking != changed_excludes


def test_fingerprint_changes_when_active_document_source_policy_changes() -> None:
    config = RepositoryIndexConfig(
        repository="example/repo",
        branch="main",
        revision="rev-a",
    )
    baseline = DocumentRepositoryIndexer(
        config,
        document_policy=RepositoryDocumentIndexPolicy(enabled=True),
    )
    xlsx_only = DocumentRepositoryIndexer(
        config,
        document_policy=RepositoryDocumentIndexPolicy(
            enabled=True,
            formats=("xlsx",),
        ),
    )

    assert baseline.document_policy.enabled is True
    assert xlsx_only.document_policy.enabled is True
    assert indexing_contract_fingerprint(baseline) != indexing_contract_fingerprint(xlsx_only)


def test_contract_version_participates_in_fingerprint_without_mutating_projection(
    monkeypatch: MonkeyPatch,
) -> None:
    indexer = _indexer()
    current_contract_version = index_fingerprint.INDEXING_CONTRACT_VERSION
    current_projection_version = SOLR_PROJECTION_VERSION
    current = indexing_contract_fingerprint(indexer)

    monkeypatch.setattr(
        index_fingerprint,
        "INDEXING_CONTRACT_VERSION",
        f"{current_contract_version}-synthetic-alternative",
    )
    alternative = indexing_contract_fingerprint(indexer)

    assert current.startswith("sha256:")
    assert len(current.removeprefix("sha256:")) == 64
    assert current != alternative
    assert SOLR_PROJECTION_VERSION == current_projection_version


def test_repository_tests_do_not_pin_contract_version_literals() -> None:
    tests_root = Path(__file__).parent
    constants = (
        "INDEXING_CONTRACT_VERSION",
        "SOLR_PROJECTION_VERSION",
    )
    offenders: list[str] = []

    for path in sorted(tests_root.glob("test_*.py")):
        content = path.read_text(encoding="utf-8")
        for constant in constants:
            pattern = re.compile(
                rf"\b{re.escape(constant)}\s*==\s*(['\"])\d+\1"
            )
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.name}:{line}:{constant}")

    assert offenders == []


def test_snapshot_round_trip_preserves_indexing_fingerprint() -> None:
    snapshot = IndexContractSolrSnapshot(
        repository="example/repo",
        branch="main",
        revision="rev-a",
        corpus_checksum="corpus",
        document_count=42,
        projection_version=SOLR_PROJECTION_VERSION,
        indexed_at="2026-08-19T18:00:00+00:00",
        indexing_fingerprint="sha256:fingerprint",
    )

    restored = IndexContractSolrSnapshot.from_solr(snapshot.to_solr())

    assert restored == snapshot
    assert restored.to_mapping()["indexing_fingerprint"] == "sha256:fingerprint"
    assert "canonical_payload" not in restored.to_mapping()


def test_legacy_snapshot_without_fingerprint_is_readable_but_stale() -> None:
    legacy = SolrIndexSnapshot(
        repository="example/repo",
        branch="main",
        revision="rev-a",
        corpus_checksum="corpus",
        document_count=42,
        projection_version=SOLR_PROJECTION_VERSION,
        indexed_at="2026-08-19T18:00:00+00:00",
    )

    restored = IndexContractSolrSnapshot.from_solr(legacy.to_solr())

    assert restored.repository == legacy.repository
    assert restored.revision == legacy.revision
    assert restored.indexing_fingerprint is None
