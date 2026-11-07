"""Exact structural keys are derived from canonical declarations only."""

from pathlib import Path

import pytest

from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.navigation import find_symbol
from fetchcore.repository.structure import default_structural_symbol_extractors
from fetchcore.repository.structure_lookup import (
    structural_lookup_fingerprint, structural_lookup_keys,
)


def test_lookup_keys_preserve_homonyms_and_canonical_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("def run():\n    pass\n", encoding="utf-8")
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(repository="sample/repo", revision="rev-a"),
        symbol_extractors=default_structural_symbol_extractors(),
    )
    documents = indexer.index(tmp_path).documents
    found = find_symbol(documents, "run")
    assert len(found) == 2
    assert {item.identity for item in found} == {
        identity for document in documents
        for identity in structural_lookup_keys(document).identities
    }
    assert all("run" in structural_lookup_keys(doc).names for doc in documents)
    assert len(structural_lookup_fingerprint()) == 71
    before = indexing_contract_fingerprint(indexer)
    monkeypatch.setattr(
        "fetchcore.repository.index_fingerprint.structural_lookup_fingerprint",
        lambda: "changed",
    )
    assert indexing_contract_fingerprint(indexer) != before
