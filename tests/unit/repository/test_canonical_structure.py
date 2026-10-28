from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from fetchcore.application.navigation import RepositoryKnowledgeNavigationService
from fetchcore.knowledge.evidence import KnowledgeEvidence, SymbolEvidence
from fetchcore.knowledge.structure import DeclarationIdentity, ExtractionCapabilities
from fetchcore.projection.core import (
    ProjectionCompatibilityError,
    ProjectionParsingError,
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
)
from fetchcore.repository.extractors.registry import default_repository_extractors
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexConfig,
    RepositoryIndexer,
)
from fetchcore.repository.navigation import (
    RelatedEvidence,
    StructuralNavigationError,
    SymbolDefinition,
)
from fetchcore.repository.semantic import SemanticRelationEvidence
from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan
from fetchcore.retrieval.embedding import ProjectionV3EmbeddingSerializer


def _indexer(revision: str = "rev-a", *, max_lines: int = 120) -> RepositoryIndexer:
    return RepositoryIndexer(
        RepositoryIndexConfig(
            repository="example/repo", revision=revision, max_chunk_lines=max_lines,
            chunk_overlap_lines=0,
        ),
        symbol_extractors=default_repository_extractors(),
    )


def _round_trip(documents: tuple[KnowledgeDocument, ...]) -> tuple[KnowledgeDocument, ...]:
    reader = ProjectionV3CanonicalDocumentReader()
    compiler = ProjectionV3Compiler()
    restored = tuple(reader.read(compiler.compile(doc).to_mapping()) for doc in documents)
    assert tuple(doc.to_mapping() for doc in restored) == tuple(
        doc.to_mapping() for doc in documents
    )
    return restored


@pytest.mark.parametrize(
    ("path", "content", "query", "parent", "relation", "capabilities"),
    [
        (
            "consumer.py",
            "class Client:\n    def fetch(self, service):\n        service.refresh()\n",
            "consumer.Client.fetch", "consumer.Client", "service.refresh",
            {"declarations", "imports", "calls_observed", "references_observed", "tests_observed"},
        ),
        (
            "client.go",
            (
                'package catalog\nimport "net/http"\ntype Client struct {}\n'
                "func (c *Client) Fetch() {}\n"
            ),
            "catalog.Client.Fetch", "catalog.Client", "net/http",
            {"declarations", "imports"},
        ),
    ],
)
def test_extraction_to_canonical_round_trip_and_navigation(
    tmp_path: Path, path: str, content: str, query: str, parent: str,
    relation: str, capabilities: set[str],
) -> None:
    (tmp_path / path).write_text(content, encoding="utf-8")
    documents = _round_trip(_indexer().index(tmp_path).documents)
    service = RepositoryKnowledgeNavigationService(documents)
    definition = service.find_symbol(query)[0]
    container = service.find_symbol(parent)[0]

    assert definition.parent_identity == container.identity
    assert definition.identity != container.identity
    assert definition.checksum == documents[0].provenance.source_checksum
    assert definition.revision == "rev-a"
    assert SymbolDefinition.from_mapping(definition.to_mapping()) == definition
    observed = service.capabilities(path=path)
    assert len(observed) == 1
    assert set(observed[0].capabilities) == capabilities
    assert observed[0].producer.startswith("fetchcore.")
    assert observed[0].version
    assert ExtractionCapabilities.from_mapping(observed[0].to_mapping()) == observed[0]
    related = service.get_related(
        definition if path.endswith(".py") else definition.source_ref,
        relation_types=("calls",) if path.endswith(".py") else ("imports",),
    )
    assert len(related) == 1
    assert related[0].target == relation
    assert related[0].checksum == definition.checksum
    assert related[0].repository == definition.repository
    assert related[0].path == path
    assert related[0].direction == "outgoing"
    assert related[0].status == ("unresolved" if path.endswith(".py") else "observed")
    assert RelatedEvidence.from_mapping(related[0].to_mapping()) == related[0]
    with pytest.raises(StructuralNavigationError, match="resolution is unavailable"):
        service.get_related(definition, require_resolution=True)


def test_logical_identity_survives_line_signature_revision_and_body_changes(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def run(first):\n    first.old()\n", encoding="utf-8")
    original = _indexer().index(tmp_path)
    before = RepositoryKnowledgeNavigationService(original.documents).find_symbol("run")[0]
    source.write_text(
        "\n\n\ndef run(second, option=None):\n    second.new()\n", encoding="utf-8",
    )
    changed = _indexer("rev-b").index(tmp_path, previous_documents=original.documents)
    service = RepositoryKnowledgeNavigationService(_round_trip(changed.documents))
    after = service.find_symbol("run")[0]
    assert before.identity == after.identity
    assert before.line_start != after.line_start
    assert before.signature != after.signature
    assert before.checksum != after.checksum
    assert after.revision == "rev-b"
    assert {item.target for item in service.get_related(after)} == {"second.new"}
    with pytest.raises(StructuralNavigationError) as stale:
        service.get_related(before)
    assert stale.value.category == "source_missing"
    source.unlink()
    removed = _indexer("rev-c").index(tmp_path, previous_documents=changed.documents)
    assert removed.documents == ()
    assert removed.changes.removed
    assert RepositoryKnowledgeNavigationService(removed.documents).find_symbol("run") == ()


def test_homonyms_overloads_and_redefinitions_are_not_collapsed(tmp_path: Path) -> None:
    (tmp_path / "Types.java").write_text(
        "class Client {\n"
        "  void run(String value) { value.trim(); }\n"
        "  void run(Integer value) { value.intValue(); }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "duplicates.py").write_text(
        "def same(client):\n    client.first()\n\n"
        "def same(client):\n    client.second()\n",
        encoding="utf-8",
    )
    (tmp_path / "other.py").write_text("def same(client):\n    pass\n", encoding="utf-8")
    documents = _round_trip(_indexer().index(tmp_path).documents)
    service = RepositoryKnowledgeNavigationService(documents)
    overloads = service.find_symbol("Client.run")
    redefinitions = service.find_symbol("same")
    assert len(overloads) == 2
    assert len(redefinitions) == 3
    assert len({item.identity for item in overloads + redefinitions}) == 5
    assert {relation.target for item in overloads for relation in service.get_related(item)} == {
        "value.trim", "value.intValue",
    }
    for symbol in redefinitions:
        if symbol.path == "duplicates.py":
            relations = service.get_related(symbol)
            assert len(relations) == 1
            assert relations[0].line is not None
            assert symbol.line_start <= relations[0].line <= symbol.line_end
    assert service.find_symbol("same", path="other.py") == (redefinitions[-1],)
    assert service.find_symbol("run", language="go") == ()
    reversed_service = RepositoryKnowledgeNavigationService(tuple(reversed(documents)))
    assert reversed_service.find_symbol("same") == redefinitions
    assert service.find_symbol("same", limit=1) == redefinitions[:1]


def test_chunk_overlap_deduplicates_declarations_but_keeps_all_relation_observations(
    tmp_path: Path,
) -> None:
    (tmp_path / "client.py").write_text(
        "class Client:\n    def run(self, service):\n"
        "        service.first()\n        service.second()\n        service.third()\n",
        encoding="utf-8",
    )
    service = RepositoryKnowledgeNavigationService(_round_trip(
        _indexer(max_lines=2).index(tmp_path).documents,
    ))
    symbols = service.find_symbol("client.Client.run")
    assert len(symbols) == 1
    assert symbols[0].line_start == 2
    assert symbols[0].line_end == 5
    assert {item.target for item in service.get_related(symbols[0])} == {
        "service.first", "service.second", "service.third",
    }


def test_source_only_is_not_a_structural_miss_and_go_calls_are_unsupported(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\nfunc Run() {}\n", encoding="utf-8")
    service = RepositoryKnowledgeNavigationService(_indexer().index(tmp_path).documents)
    with pytest.raises(StructuralNavigationError) as unsupported:
        service.find_symbol("main", language="rust")
    assert unsupported.value.category == "unsupported_capability"
    assert service.capabilities(language="rust")[0].capabilities == ("source_only",)
    assert service.coverage(language="rust").status == "unsupported"
    assert service.coverage(language="go").status == "supported"
    assert service.coverage().status == "partial"
    assert service.coverage().sources == 2
    assert RepositoryKnowledgeNavigationService(()).coverage().status == "unknown"
    assert service.find_symbol("absent", language="go") == ()
    with pytest.raises(StructuralNavigationError) as calls:
        service.get_related(service.find_symbol("Run")[0], relation_types=("calls",))
    assert calls.value.category == "unsupported_capability"
    with pytest.raises(StructuralNavigationError):
        service.get_related(service.find_symbol("Run")[0], relation_types=("implements",))


def test_semantic_resolution_keeps_proven_target_without_attributing_it_to_symbol(
    tmp_path: Path,
) -> None:
    (tmp_path / "module.py").write_text("def run():\n    pass\n", encoding="utf-8")
    document = _indexer().index(tmp_path).documents[0]
    relations = (
        SemanticRelationEvidence("references", "Target", resolution="ambiguous"),
        SemanticRelationEvidence(
            "references", "Target", resolution="resolved", evidence_basis="exact-index",
            target_document_id="target-id", target_source_ref="repository://example/repo/target.py",
            target_path="target.py",
        ),
        SemanticRelationEvidence("references", "Target", resolution="unresolved"),
    )
    document = replace(document, evidence=replace(
        document.evidence, relations=replace(document.evidence.relations, semantic=relations),
    ))
    service = RepositoryKnowledgeNavigationService(_round_trip((document,)))
    related = service.get_related(document.source_ref, relation_types=("references",))
    assert len(related) == 3
    assert {item.status for item in related} == {"ambiguous", "resolved", "unresolved"}
    resolved = next(item for item in related if item.status == "resolved")
    assert resolved.target_document_id == "target-id"
    assert resolved.target_path == "target.py"
    assert service.get_related(service.find_symbol("run")[0]) == ()


def test_schema_and_capability_changes_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run():\n    pass\n", encoding="utf-8")
    indexer = _indexer()
    document = indexer.index(tmp_path).documents[0]
    projected = ProjectionV3Compiler().compile(document).to_mapping()
    incompatible = {**projected, "projection_fingerprint": "old-fingerprint"}
    with pytest.raises(ProjectionCompatibilityError):
        ProjectionV3CanonicalDocumentReader().read(incompatible)
    payload = json.loads(str(projected["canonical_payload"]))
    payload["evidence"]["extraction"]["capabilities"] = ["invented"]
    with pytest.raises(ProjectionParsingError):
        ProjectionV3CanonicalDocumentReader().read({
            **projected, "canonical_payload": json.dumps(payload),
        })
    baseline = indexing_contract_fingerprint(indexer)
    provider = indexer.symbol_extractors["python"]
    provider.structural_capabilities = ("declarations",)  # type: ignore[attr-defined]
    assert indexing_contract_fingerprint(indexer) != baseline
    legacy = KnowledgeEvidence.from_mapping({})
    assert legacy.extraction is None
    assert legacy.declarations == ()
    identity = DeclarationIdentity("example/repo", "module.py", "python", "function", "run")
    assert DeclarationIdentity.from_mapping(identity.to_mapping()) == identity
    with pytest.raises(ValueError):
        KnowledgeEvidence(declarations=(SymbolEvidence("run", "function", 1, 2),))


def test_optional_extractor_adapter_normalizes_without_application_language_branches(
    tmp_path: Path,
) -> None:
    class OptionalAdapter:
        indexing_contract_version = "adapter-v1"
        structural_capabilities = ("declarations",)

        def extract(self, content: str) -> tuple[StructuralSymbolSpan, ...]:
            return self.extract_structure(content).chunk_symbols

        def extract_structure(
            self, content: str, *, path: str | None = None,
        ) -> SourceStructure:
            declaration = StructuralSymbolSpan(
                "main", "function", 1, 1, qualified_name="custom.main",
            )
            return SourceStructure(
                language="rust", symbols=(declaration,), chunk_symbols=(declaration,),
            )

    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(repository="example/repo", revision="rev-a"),
        symbol_extractors={"rust": OptionalAdapter()},
    )
    documents = _round_trip(indexer.index(tmp_path).documents)
    service = RepositoryKnowledgeNavigationService(documents)
    assert service.find_symbol("custom.main")[0].language == "rust"
    assert service.coverage(language="rust").status == "supported"
    assert service.capabilities()[0].version == "adapter-v1"
    assert "OptionalAdapter" in service.capabilities()[0].producer


def test_go_cross_file_receiver_and_non_code_are_not_lost_or_fabricated(tmp_path: Path) -> None:
    (tmp_path / "types.go").write_text(
        "package catalog\ntype Client struct {}\n", encoding="utf-8",
    )
    (tmp_path / "methods.go").write_text(
        "package catalog\ntype Other struct {}\nfunc (c *Client) Fetch() {}\n"
        'var example = `\nimport "fake/package"\nfunc Fake() {}\n`\n'
        '/*\nimport "fake/comment"\nfunc Wrong() {}\n*/\n',
        encoding="utf-8",
    )
    service = RepositoryKnowledgeNavigationService(_round_trip(_indexer().index(tmp_path).documents))
    definition = service.find_symbol("catalog.Client.Fetch")[0]
    assert definition.path == "methods.go"
    assert definition.parent_identity is None  # Cross-file type ownership is not resolved.
    assert service.find_symbol("Fake") == ()
    assert service.find_symbol("Wrong") == ()
    assert service.get_related(definition.source_ref, relation_types=("imports",)) == ()


def test_exact_target_document_preserves_revision_checksum_and_rejects_conflicting_trace(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("def target():\n    pass\n", encoding="utf-8")
    documents = _indexer().index(tmp_path).documents
    source = next(doc for doc in documents if doc.path == "source.py")
    target = next(doc for doc in documents if doc.path == "target.py")
    relation = SemanticRelationEvidence(
        "references", "target.target", resolution="exact-unique",
        target_document_id=target.id, target_source_ref=target.source_ref, target_path=target.path,
    )
    source = replace(source, evidence=replace(
        source.evidence, relations=replace(source.evidence.relations, semantic=(relation,)),
    ))
    service = RepositoryKnowledgeNavigationService(_round_trip((source, target)))
    observed = service.get_related(source.source_ref)[0]
    assert observed.status == "resolved"
    assert observed.target_revision == target.revision
    assert observed.target_checksum == target.provenance.source_checksum
    corrupted = replace(source, evidence=replace(
        source.evidence,
        relations=replace(source.evidence.relations, semantic=(
            replace(relation, target_path="wrong.py"),
        )),
    ))
    with pytest.raises(StructuralNavigationError) as conflict:
        RepositoryKnowledgeNavigationService((corrupted, target)).get_related(source.source_ref)
    assert conflict.value.category == "schema_mismatch"


def test_navigation_output_limits_scope_and_unsupported_direction(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text(
        "def run(service):\n    service.first()\n    service.second()\n", encoding="utf-8",
    )
    documents = _round_trip(_indexer().index(tmp_path).documents)
    service = RepositoryKnowledgeNavigationService(documents)
    definition = service.find_symbol("run")[0]
    assert len(service.get_related(definition, limit=1)) == 1
    assert service.get_related(definition, limit=100) == service.get_related(definition)
    for limit in (0, 101, True):
        with pytest.raises(ValueError):
            service.find_symbol("run", limit=limit)
        with pytest.raises(ValueError):
            service.get_related(definition, limit=limit)
    with pytest.raises(StructuralNavigationError):
        service.get_related(definition, direction="incoming")  # type: ignore[arg-type]
    with pytest.raises(StructuralNavigationError):
        service.get_related(replace(definition, repository="other/repo"))
    with pytest.raises(ValueError):
        RepositoryKnowledgeNavigationService((
            documents[0], replace(documents[0], revision="rev-b"),
        ))
    with pytest.raises(ValueError):
        RepositoryKnowledgeNavigationService((
            documents[0], replace(documents[0], repository="other/repo"),
        ))
    legacy = replace(
        documents[0], evidence=replace(
            documents[0].evidence, declarations=(), extraction=None,
        ),
    )
    assert RepositoryKnowledgeNavigationService((legacy,)).coverage().status == "unknown"


def test_new_structural_contract_does_not_change_ranking_fields_or_embedding_input(
    tmp_path: Path,
) -> None:
    (tmp_path / "module.py").write_text(
        "class Client:\n    def run(self, service):\n        service.refresh()\n",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text("package main\nfunc Run() {}\n", encoding="utf-8")
    compiler = ProjectionV3Compiler()
    serializer = ProjectionV3EmbeddingSerializer()
    for document in _indexer().index(tmp_path).documents:
        evidence = document.evidence
        legacy = replace(document, evidence=replace(
            evidence, declarations=(), extraction=None,
            symbol=(
                replace(evidence.symbol, identity=None, parent_identity=None)
                if evidence.symbol else None
            ),
            behavior=replace(
                evidence.behavior,
                methods=tuple(
                    replace(method, identity=None, parent_identity=None)
                    for method in evidence.behavior.methods
                ),
            ),
        ))
        current_record = compiler.compile(document)
        legacy_record = compiler.compile(legacy)
        assert serializer.serialize_record(current_record) == serializer.serialize_record(legacy_record)
        current_mapping = current_record.to_mapping()
        legacy_mapping = legacy_record.to_mapping()
        current_mapping.pop("canonical_payload")
        legacy_mapping.pop("canonical_payload")
        assert current_mapping == legacy_mapping
