from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fetchcore.application import (
    RepositoryBinding,
    RepositoryKnowledgeNavigationService,
    RetrievalApplicationService,
    SourceReadRequest,
)
from fetchcore.projection.core import ProjectionV3CanonicalDocumentReader, ProjectionV3Compiler
from fetchcore.repository.calls import CallEvidence
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.navigation import find_symbol, get_related
from fetchcore.repository.extractors.registry import default_repository_extractors


_GO_SOURCE = """package catalog

import (
    "fmt"
    httpalias "net/http"
)

type Client struct {
    BaseURL string
}

func (c *Client) Fetch(url string) error {
    _, _ = httpalias.NewRequest("GET", url, nil)
    return nil
}

func Build() *Client {
    fmt.Println("ready")
    return &Client{}
}

type Key string
"""


def _index(workspace: Path, *, revision: str) -> tuple:
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(
            repository="example/catalog",
            branch="main",
            revision=revision,
        ),
        symbol_extractors=default_repository_extractors(),
    )
    return indexer.index(workspace).documents


def test_go_declarations_and_imports_enter_canonical_knowledge(tmp_path: Path) -> None:
    source = tmp_path / "client.go"
    source.write_text(_GO_SOURCE, encoding="utf-8")

    documents = _index(tmp_path, revision="rev-a")
    definitions = find_symbol(documents, "Fetch", repository="example/catalog")
    client = find_symbol(documents, "catalog.Client", kind="struct")
    build = find_symbol(documents, "catalog.Build", language="go")
    key = find_symbol(documents, "catalog.Key", kind="type")

    assert len(definitions) == 1
    assert definitions[0].qualified_name == "catalog.Client.Fetch"
    assert definitions[0].line_start == 12
    assert definitions[0].revision == "rev-a"
    assert definitions[0].checksum.startswith("sha256:")
    assert len(client) == 1
    assert len(build) == 1
    assert len(key) == 1
    assert set(documents[0].evidence.structure.import_sources) == {"fmt", "net/http"}
    assert all(item.language == "go" for item in documents)
    assert len(RepositoryKnowledgeNavigationService(documents).find_symbol("Fetch")) == 1
    source_result = RetrievalApplicationService(
        {
            "example/catalog": RepositoryBinding(
                repository="example/catalog",
                workspace=tmp_path,
                backend="local-semantic",
            )
        }
    ).get_source(
        SourceReadRequest(
            source_ref=definitions[0].source_ref,
            repository="example/catalog",
            line_start=definitions[0].line_start,
            line_end=definitions[0].line_end,
        )
    )
    assert "func (c *Client) Fetch" in source_result.content


def test_navigation_is_exact_ambiguous_and_keeps_unresolved_relations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "client.go"
    source.write_text(_GO_SOURCE, encoding="utf-8")
    (tmp_path / "other.go").write_text(
        "package catalog\n\nfunc Build() *Client { return nil }\n",
        encoding="utf-8",
    )
    documents = _index(tmp_path, revision="rev-a")

    build_candidates = find_symbol(documents, "Build")
    assert len(build_candidates) == 2
    assert tuple(item.path for item in build_candidates) == ("client.go", "other.go")
    assert len(find_symbol(documents, "catalog.Build")) == 2

    build_document = next(
        document
        for document in documents
        if document.path == "client.go" and document.evidence.symbol is not None
        and document.evidence.symbol.name == "Build"
    )
    related_document = replace(
        build_document,
        evidence=replace(
            build_document.evidence,
            relations=replace(
                build_document.evidence.relations,
                calls=(
                    CallEvidence(
                        source_symbol="catalog.Build",
                        callee="Println",
                        target="fmt.Println",
                        source="fmt",
                        kind="function",
                        line=19,
                        resolution="unresolved",
                    ),
                ),
            ),
        ),
    )
    current_documents = tuple(
        related_document if item.id == build_document.id else item for item in documents
    )
    selected = find_symbol(current_documents, "catalog.Build")[0]
    relations = get_related(current_documents, selected)

    assert len(relations) == 1
    assert relations[0].target == "fmt.Println"
    assert relations[0].resolution == "unresolved"
    assert relations[0].line == 19
    assert relations[0].source_ref == build_document.source_ref


def test_go_knowledge_projection_round_trip_preserves_provenance_and_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "client.go").write_text(_GO_SOURCE, encoding="utf-8")
    document = _index(tmp_path, revision="rev-a")[0]

    projected = ProjectionV3Compiler().compile(document).to_mapping()
    restored = ProjectionV3CanonicalDocumentReader().read(projected)

    assert restored.evidence == document.evidence
    assert restored.provenance == document.provenance
    assert restored.revision == "rev-a"
    assert restored.path == "client.go"


def test_observed_python_member_call_is_navigable_without_resolution_inference(
    tmp_path: Path,
) -> None:
    (tmp_path / "consumer.py").write_text(
        "def caller(service):\n    service.refresh()\n",
        encoding="utf-8",
    )
    documents = _index(tmp_path, revision="rev-a")
    navigation = RepositoryKnowledgeNavigationService(documents)
    caller = navigation.find_symbol("consumer.caller")[0]

    related = navigation.get_related(caller)

    assert len(related) == 1
    assert related[0].kind == "calls"
    assert related[0].subtype == "member_call"
    assert related[0].target == "service.refresh"
    assert related[0].resolution == "syntax"
    assert related[0].source_ref == caller.source_ref
