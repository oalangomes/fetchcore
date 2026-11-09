from __future__ import annotations

from pathlib import Path

import pytest

from fetchcore.repository.cross_file_relations import ImportBoundRelationResolver
from fetchcore.repository.extractors.registry import default_repository_extractors
from fetchcore.repository.incremental import GitDeltaPlan, index_repository_delta
from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.navigation import find_symbol, get_related
from fetchcore.repository.semantic_indexing import SemanticDocumentRepositoryIndexer
from fetchcore.repository.structure_lookup import structural_lookup_keys


def _indexer(revision: str) -> SemanticDocumentRepositoryIndexer:
    return SemanticDocumentRepositoryIndexer(
        RepositoryIndexConfig(repository="example/repo", revision=revision),
        symbol_extractors=default_repository_extractors(),
        corpus_resolvers=(ImportBoundRelationResolver(),),
    )


@pytest.mark.parametrize(
    ("suffix", "import_statement", "target_source", "caller_source"),
    [
        (
            ".py", "from target import run as execute",
            "def run():\n    return 1\n",
            "def caller():\n    execute()\n",
        ),
        (
            ".ts", "import { run as execute } from './target';",
            "export function run() { return 1; }\n",
            "export function caller() { execute(); }\n",
        ),
    ],
)
def test_import_bound_call_has_precise_target_and_reverse_key(
    tmp_path: Path, suffix: str, import_statement: str,
    target_source: str, caller_source: str,
) -> None:
    (tmp_path / f"target{suffix}").write_text(target_source, encoding="utf-8")
    (tmp_path / f"consumer{suffix}").write_text(
        import_statement + "\n" + caller_source, encoding="utf-8",
    )
    documents = _indexer("rev-a").index(tmp_path).documents
    target = find_symbol(documents, "run")[0]
    caller = find_symbol(documents, "caller")[0]
    outgoing = get_related(documents, caller, relation_types=("calls",))
    assert len(outgoing) == 1
    assert outgoing[0].status == "resolved"
    assert outgoing[0].target_identity == target.identity
    assert outgoing[0].target_checksum == target.checksum
    assert outgoing[0].target_revision == "rev-a"
    incoming = get_related(documents, target, relation_types=("calls",), direction="incoming")
    assert len(incoming) == 1
    assert incoming[0].source_identity == caller.identity
    assert incoming[0].source_ref == caller.source_ref
    assert incoming[0].checksum == caller.checksum
    assert incoming[0].direction == "incoming"
    source = next(doc for doc in documents if doc.id == outgoing[0].target_document_id)
    assert target.identity in structural_lookup_keys(
        next(doc for doc in documents if doc.id == caller.document_id)
    ).incoming_identities
    assert source.path == target.path
    assert get_related(documents, caller, relation_types=("calls",), require_resolution=True) == outgoing


def test_ambiguous_declaration_is_never_resolved_and_target_removal_clears_edge(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "target.py"
    target_path.write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "consumer.py").write_text(
        "from target import run as execute\ndef caller():\n    execute()\n",
        encoding="utf-8",
    )
    original = _indexer("rev-a").index(tmp_path)
    caller = find_symbol(original.documents, "caller")[0]
    target = find_symbol(original.documents, "run")[0]
    assert get_related(original.documents, target, direction="incoming")

    target_path.write_text(
        "def run():\n    pass\nclass Other:\n    def run(self):\n        pass\n",
        encoding="utf-8",
    )
    changed = index_repository_delta(
        _indexer("rev-b"), tmp_path, original.documents,
        GitDeltaPlan("git-delta", "rev-a", "rev-b", changed_paths=("target.py",)),
    )
    assert changed is not None
    assert not any(
        relation.target_identity == target.identity
        for document in changed.documents
        for relation in document.evidence.relations.semantic
    )
    assert structural_lookup_keys(next(
        document for document in changed.documents if document.id == caller.document_id
    )).incoming_identities == ()

    target_path.unlink()
    removed = index_repository_delta(
        _indexer("rev-c"), tmp_path, changed.documents,
        GitDeltaPlan("git-delta", "rev-b", "rev-c", removed_paths=("target.py",)),
    )
    assert removed is not None
    assert not any(document.path == "target.py" for document in removed.documents)
    assert not any(
        relation.target_document_id is not None
        for document in removed.documents
        for relation in document.evidence.relations.semantic
    )
