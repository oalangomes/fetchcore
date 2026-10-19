"""Controlled NeuroTrack typed-knowledge acceptance without source disclosure."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from fetchcore.projection.core import ProjectionV3CanonicalDocumentReader, ProjectionV3Compiler
from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import (
    KnowledgeDocument, RepositoryIndexConfig, RepositoryIndexer, RepositoryIndexResult,
)
from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer

HERE = Path(__file__).resolve().parent
TRUTH_PATH = HERE / "neurotrack-ground-truth-v1.json"
TRUTH = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def _stable(documents: tuple[KnowledgeDocument, ...]) -> list[dict[str, Any]]:
    result = []
    for document in documents:
        value = document.to_mapping()
        value.pop("indexed_at")
        result.append(value)
    return result


def _git(workspace: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(workspace), *args], text=True,
    ).strip()


def preflight(workspace: Path) -> tuple[
    dict[str, object], RepositoryIndexResult, ApiContractRepositoryIndexer, dict[str, str],
]:
    """Validate pinned real sources before any backend resource is created."""

    root = workspace.resolve(strict=True)
    _require(_git(root, "rev-parse", "HEAD") == TRUTH["revision"],
             "NeuroTrack revision does not match reviewed Ground Truth")
    _require(not _git(root, "status", "--porcelain"), "NeuroTrack checkout is dirty")
    paths = tuple(str(path) for path in TRUTH["include_paths"])
    source_hashes = {path: _sha256((root / path).read_bytes()) for path in paths}
    config = RepositoryIndexConfig(
        repository=str(TRUTH["repository"]),
        revision=str(TRUTH["revision"]),
        include_patterns=paths,
    )
    default = RepositoryIndexer(config).index(root)
    indexer = ApiContractRepositoryIndexer(
        config, resolve_local_references=True, structured_python=True,
    )
    canonical = indexer.index(root)
    repeated = indexer.index(root, previous_documents=canonical.documents)
    _require(not repeated.changes.added and not repeated.changes.updated
             and not repeated.changes.removed, "same-revision indexing changed canonical state")
    _require(_stable(canonical.documents) == _stable(repeated.documents),
             "canonical extraction is nondeterministic")

    expected_ids: dict[str, str] = {}
    for case in TRUTH["queries"]:
        matches = []
        for document in canonical.documents:
            if document.path != case["path"]:
                continue
            if "kind" in case:
                for envelope in document.evidence.semantic:
                    if envelope.namespace != "api_contract":
                        continue
                    fact = ApiEvidenceV1.from_envelope(envelope)
                    if (fact.kind, fact.method, fact.route, fact.locator.pointer) == (
                        case["kind"], case["method"], case["route"], case["pointer"],
                    ):
                        matches.append(document)
            elif (document.line_start == case["line_start"]
                  and document.line_end == case["line_end"]
                  and case["evidence"] in document.content):
                matches.append(document)
        _require(len(matches) == 1, f"Ground Truth is missing or ambiguous: {case['id']}")
        expected_ids[str(case["id"])] = matches[0].id

    relations = {
        (str(item["kind"]), document.path, str(item["target_path"]),
         item.get("target_locator"))
        for document in canonical.documents
        for envelope in document.evidence.semantic
        if envelope.kind == "resolution"
        for item in [envelope.payload()]
        if item["status"] == "resolved"
    }
    _require({tuple(row) for row in TRUTH["resolved_relations"]} <= relations,
             "reviewed local relations were not resolved")
    _require(not ({tuple(row) for row in TRUTH["forbidden_resolved_relations"]}
                  & {(kind, source, target) for kind, source, target, _ in relations}),
             "an unproven route was resolved")

    default_paths = {document.path for document in default.documents}
    typed_paths = {document.path for document in canonical.documents}
    for path in paths:
        if "postman_environment" in path:
            _require(path not in default_paths and path not in typed_paths,
                     "Postman environment escaped quarantine")
    for path in ("docs/reference/api/openapi.json",
                 "docs/reference/api/postman-collections/NeuroTrack API2.postman_collection.json"):
        _require(path not in default_paths and path in typed_paths,
                 "API artifact admission violated the opt-in boundary")

    rendered = json.dumps(canonical.to_mapping(), sort_keys=True)
    for path in paths:
        if "postman_environment" not in path:
            continue
        environment = json.loads((root / path).read_text(encoding="utf-8"))
        for item in environment.get("values", []):
            if not isinstance(item, dict):
                continue
            key, value = item.get("key"), item.get("value")
            if (isinstance(key, str) and isinstance(value, str)
                    and len(value) >= 12
                    and any(word in key.casefold()
                            for word in ("token", "password", "secret", "key"))):
                _require(value not in rendered, "environment credential reached canonical data")

    compiler = ProjectionV3Compiler()
    reader = ProjectionV3CanonicalDocumentReader()
    projected = [compiler.compile(doc).to_mapping() for doc in canonical.documents]
    _require(_stable(tuple(reader.read(record) for record in projected))
             == _stable(canonical.documents), "Projection v3 changed canonical evidence")
    for document in canonical.documents:
        _require(document.repository == TRUTH["repository"]
                 and document.revision == TRUTH["revision"],
                 "canonical repository or revision is wrong")
        _require(document.provenance.source_checksum == "sha256:" + source_hashes[document.path],
                 "canonical source checksum is wrong")

    kinds = Counter(envelope.kind for document in canonical.documents
                    for envelope in document.evidence.semantic)
    statuses = Counter(envelope.payload()["status"] for document in canonical.documents
                       for envelope in document.evidence.semantic
                       if envelope.kind == "resolution")
    summary: dict[str, object] = {
        "protocol": TRUTH["protocol"],
        "repository": TRUTH["repository"],
        "revision": TRUTH["revision"],
        "ground_truth_sha256": _sha256(TRUTH_PATH.read_bytes()),
        "source_sha256": source_hashes,
        "default_documents": len(default.documents),
        "typed_documents": len(canonical.documents),
        "typed_paths": len(typed_paths),
        "facts_by_kind": dict(kinds),
        "relation_statuses": dict(statuses),
        "reviewed_queries_recovered": len(expected_ids),
        "reviewed_relations_recovered": len(TRUTH["resolved_relations"]),
        "default_fingerprint": indexing_contract_fingerprint(RepositoryIndexer(config)),
        "typed_fingerprint": indexing_contract_fingerprint(indexer),
        "projection_fingerprint": compiler.projection_fingerprint,
        "canonical_digest": _sha256(json.dumps(
            _stable(canonical.documents), sort_keys=True, separators=(",", ":"),
        ).encode()),
        "source_values_exposed": False,
        "projection_round_trip": True,
        "unchanged_reindex_noop": True,
        "status": "PASS",
    }
    return summary, canonical, indexer, expected_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary, _, _, _ = preflight(args.workspace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"preflight={summary['status']} documents={summary['typed_documents']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
