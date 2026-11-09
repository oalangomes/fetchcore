"""Resolve import-bound relations against exact current canonical declarations.

This resolver runs during explicit indexing. It never guesses a target from an
unqualified name: the import must identify one repository-local source file and
that file must contain one matching declaration.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath

from fetchcore.knowledge.evidence import SymbolEvidence
from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.semantic import SemanticRelationEvidence


class ImportBoundRelationResolver:
    """Resolve Python and JS/TS import bindings without backend dependencies."""

    indexing_contract_id = "fetchcore.import-bound-relations"
    indexing_contract_version = "1"

    def resolve(
        self, documents: tuple[KnowledgeDocument, ...],
    ) -> tuple[KnowledgeDocument, ...]:
        by_path: dict[str, list[KnowledgeDocument]] = defaultdict(list)
        for document in documents:
            by_path[document.path].append(document)
        output: list[KnowledgeDocument] = []
        for document in documents:
            if document.language not in {"python", "javascript", "typescript"}:
                output.append(document)
                continue
            original_relations = document.evidence.relations
            retained_relations = tuple(
                item for item in original_relations.semantic
                if item.evidence_basis not in {
                    "import-path", "import-path-and-declaration",
                    "import-binding-and-canonical-declaration",
                }
            )
            candidates: list[SemanticRelationEvidence] = []
            # Imports are file observations repeated on chunks. Publish once.
            if document is by_path[document.path][0]:
                for imported in document.evidence.structure.imports:
                    target_path = _import_path(document, imported.source, by_path)
                    if target_path is None:
                        continue
                    target_name = imported.name if imported.kind == "named" else None
                    target = _unique_target(by_path[target_path], target_name)
                    if target_name is not None and target is None:
                        continue
                    target_document, declaration = target or (by_path[target_path][0], None)
                    candidates.append(SemanticRelationEvidence(
                        "imports", imported.source, resolution="exact-unique",
                        evidence_basis="import-path-and-declaration" if declaration else "import-path",
                        source_identity=document.source_ref,
                        target_identity=declaration.identity.key if declaration and declaration.identity else None,
                        target_document_id=target_document.id,
                        target_source_ref=target_document.source_ref,
                        target_path=target_document.path,
                        line=imported.line,
                    ))
            for kind, observations in (("calls", document.evidence.relations.calls),):
                for observation in observations:
                    if observation.source is None or observation.resolution not in {
                        "import_binding", "dynamic_import_binding",
                    }:
                        continue
                    target_path = _import_path(document, observation.source, by_path)
                    if target_path is None:
                        continue
                    target_name = observation.target.rsplit(".", 1)[-1]
                    target = _unique_target(by_path[target_path], target_name)
                    if target is None:
                        continue
                    owner = _unique_owner(document, observation.source_symbol, observation.line)
                    if owner is None or owner.identity is None:
                        continue
                    target_document, declaration = target
                    assert declaration is not None and declaration.identity is not None
                    candidates.append(SemanticRelationEvidence(
                        kind, observation.target, resolution="exact-unique",
                        evidence_basis="import-binding-and-canonical-declaration",
                        source_identity=owner.identity.key,
                        target_identity=declaration.identity.key,
                        target_document_id=target_document.id,
                        target_source_ref=target_document.source_ref,
                        target_path=target_document.path,
                        line=observation.line,
                    ))
            for test in document.evidence.relations.test_targets:
                if test.target_symbol is None or test.call_line is None:
                    continue
                target_path = _import_path(document, test.target_source, by_path)
                if target_path is None:
                    continue
                target = _unique_target(by_path[target_path], test.target_symbol)
                owner = _unique_owner_at_line(document, test.call_line)
                if target is None or owner is None or owner.identity is None:
                    continue
                target_document, declaration = target
                assert declaration is not None and declaration.identity is not None
                candidates.append(SemanticRelationEvidence(
                    "tests", test.target_source, resolution="exact-unique",
                    evidence_basis="import-binding-and-canonical-declaration",
                    source_identity=owner.identity.key,
                    target_identity=declaration.identity.key,
                    target_document_id=target_document.id,
                    target_source_ref=target_document.source_ref,
                    target_path=target_document.path,
                    line=test.call_line,
                ))
            current = document.evidence
            if retained_relations != original_relations.semantic:
                current = replace(current, relations=replace(
                    original_relations, semantic=retained_relations,
                ))
            if candidates:
                # A source may have overlapping chunks; canonical observations
                # are attached only where their source line belongs.
                semantic = tuple(dict.fromkeys((*current.relations.semantic, *candidates)))
                current = replace(current, relations=replace(current.relations, semantic=semantic))
            if current.extraction is not None:
                capabilities = current.extraction.capabilities
                if "cross_file_resolution" not in capabilities:
                    current = replace(current, extraction=replace(
                        current.extraction,
                        capabilities=(*capabilities, "cross_file_resolution")
                        if capabilities != ("source_only",) else capabilities,
                    ))
            output.append(replace(document, evidence=current))
        return tuple(output)


def _import_path(
    source: KnowledgeDocument, imported: str,
    by_path: dict[str, list[KnowledgeDocument]],
) -> str | None:
    paths: list[str] = []
    if source.language == "python":
        if imported.startswith("."):
            level = len(imported) - len(imported.lstrip("."))
            parts = PurePosixPath(source.path).parent.parts
            if level > len(parts) + 1:
                return None
            root = parts[: len(parts) - level + 1]
            module = (*root, *imported[level:].split("."))
        else:
            module = tuple(imported.split("."))
        if not module or any(not part or part in {".", ".."} for part in module):
            return None
        paths = ["/".join(module) + ".py", "/".join((*module, "__init__.py"))]
    elif source.language in {"javascript", "typescript"}:
        if not imported.startswith(("./", "../")):
            return None
        stem = posixpath.normpath(posixpath.join(posixpath.dirname(source.path), imported))
        if stem.startswith("../") or stem in {"..", "."}:
            return None
        paths = [stem + suffix for suffix in (".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs")]
        paths += [posixpath.join(stem, "index" + suffix) for suffix in (".ts", ".tsx", ".js", ".jsx")]
        if stem.endswith((".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs")):
            paths.append(stem)
    found = [path for path in paths if path in by_path]
    return found[0] if len(found) == 1 else None


def _unique_target(
    documents: list[KnowledgeDocument], name: str | None,
) -> tuple[KnowledgeDocument, SymbolEvidence | None] | None:
    if name is None:
        return documents[0], None
    found = {
        item.identity.key: (document, item)
        for document in documents for item in document.evidence.declarations
        if item.identity is not None and item.name == name
    }
    return next(iter(found.values())) if len(found) == 1 else None


def _unique_owner(
    document: KnowledgeDocument, name: str | None, line: int | None,
) -> SymbolEvidence | None:
    if name is None or line is None:
        return None
    found = {
        item.identity.key: item
        for item in document.evidence.declarations
        if item.identity is not None
        and name in {item.name, item.qualified_name}
        and item.line_start <= line <= item.line_end
    }
    return next(iter(found.values())) if len(found) == 1 else None


def _unique_owner_at_line(document: KnowledgeDocument, line: int) -> SymbolEvidence | None:
    found = {
        item.identity.key: item
        for item in document.evidence.declarations
        if item.identity is not None and item.line_start <= line <= item.line_end
    }
    return next(iter(found.values())) if len(found) == 1 else None
