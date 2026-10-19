"""Integrated, backend-free S0-S5 canonical and projection acceptance exercise."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from fetchcore.projection.core import ProjectionV3CanonicalDocumentReader, ProjectionV3Compiler
from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer
from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer

ROOT = Path(__file__).resolve().parents[2]
TRUTH = json.loads((Path(__file__).parent / "integrated-ground-truth.json").read_text())
REPOSITORY = "fetchcore/issue-181-safe-fixtures"


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _stable_documents(documents: tuple[KnowledgeDocument, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for document in documents:
        item = document.to_mapping()
        item.pop("indexed_at")
        result.append(item)
    return result


def _copy_corpus(root: Path) -> dict[str, str]:
    sources = (
        (ROOT / "benchmarks/api-extraction-s0/fixtures", "api"),
        (ROOT / "benchmarks/local-reference-s5/fixtures", "references"),
        (ROOT / "benchmarks/markdown-extraction-s4/fixtures", "markdown"),
    )
    hashes = {}
    for source, prefix in sources:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = Path(prefix) / path.relative_to(source)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            hashes[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    return hashes


def _facts(documents: tuple[KnowledgeDocument, ...]) -> tuple[
    set[tuple[str, str, str, str]], set[tuple[str, str, str]],
]:
    api: set[tuple[str, str, str, str]] = set()
    resolved: set[tuple[str, str, str]] = set()
    for document in documents:
        for envelope in document.evidence.semantic:
            if envelope.namespace == "api_contract":
                fact = ApiEvidenceV1.from_envelope(envelope)
                if fact.kind in {"api_operation", "api_request"} and fact.method and fact.route:
                    api.add((document.path, fact.kind, fact.method, fact.route))
            if envelope.schema_id in {"local_reference/resolution@1", "local_reference/resolution@2"}:
                relation = envelope.payload()
                if relation["status"] == "resolved":
                    resolved.add((str(relation["kind"]), document.path, str(relation["target_path"])))
    return api, resolved


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="fetchcore-181-") as directory:
        root = Path(directory)
        source_hashes = _copy_corpus(root)
        revision = _digest(source_hashes)
        config = RepositoryIndexConfig(repository=REPOSITORY, revision=revision)
        indexers = {
            "s0": RepositoryIndexer(config),
            "s2": OpenApiRepositoryIndexer(config),
            "s3_s4": ApiContractRepositoryIndexer(config),
            "s5": ApiContractRepositoryIndexer(
                config, resolve_local_references=True, structured_python=True,
            ),
        }
        runs = {name: indexer.index(root) for name, indexer in indexers.items()}
        repeated = indexers["s5"].index(root, previous_documents=runs["s5"].documents)
        _require(not repeated.changes.added and not repeated.changes.updated
                 and not repeated.changes.removed, "unchanged S5 reindex must be a no-op")
        _require(_stable_documents(repeated.documents) == _stable_documents(runs["s5"].documents),
                 "repeated S5 canonical corpus changed")

        expected_api = {tuple(row) for row in TRUTH["api"]}
        expected_relations = {tuple(row) for row in TRUTH["resolved_relations"]}
        api, relations = _facts(runs["s5"].documents)
        _require(api == expected_api, f"integrated API evidence mismatch: {api ^ expected_api}")
        _require(expected_relations <= relations, "integrated S5 relations missing")
        _require(not ({tuple(row) for row in TRUTH["forbidden_relations"]} & relations),
                 "S5 emitted a forbidden resolved relation")
        for document in runs["s5"].documents:
            _require(document.repository == REPOSITORY and document.revision == revision,
                     f"incorrect canonical repository/revision: {document.path}")
            _require(document.provenance.source_checksum == "sha256:" + source_hashes[document.path],
                     f"incorrect source checksum: {document.path}")
            _require(bool(document.checksum and document.id and document.source_ref),
                     f"incomplete canonical identity: {document.path}")
            for envelope in document.evidence.semantic:
                if envelope.schema_id in {"local_reference/resolution@1",
                                          "local_reference/resolution@2"}:
                    relation = envelope.payload()
                    _require(not (relation["kind"] == "route_correspondence"
                                  and relation["declaration"] == "POST /orders"
                                  and relation["status"] == "resolved"),
                             "wrong HTTP method produced a resolved route")

        s0_paths = {doc.path for doc in runs["s0"].documents}
        s2_paths = {doc.path for doc in runs["s2"].documents}
        s3_paths = {doc.path for doc in runs["s3_s4"].documents}
        for path in TRUTH["quarantined"]:
            _require(path not in s0_paths, f"S0 admitted {path}")
        _require("api/demo.postman_collection.json" not in s2_paths,
                 "S2 admitted Postman without opt-in")
        _require("api/demo.postman_collection.json" in s3_paths,
                 "S3 failed to admit sanitized Postman")
        _require("api/demo.postman_environment.json" not in s3_paths,
                 "S3 admitted Postman environment")
        _require(TRUTH["sentinel"] not in json.dumps(runs["s5"].to_mapping()),
                 "secret sentinel reached canonical output")

        markdown = [doc for doc in runs["s5"].documents if doc.path.startswith("markdown/")]
        for case in TRUTH["markdown"]:
            _require(any(doc.path == case["path"] and doc.line_start == case["line_start"]
                         and doc.line_end == case["line_end"] and case["evidence"] in doc.content
                         for doc in markdown), f"Markdown span missing: {case['id']}")

        compiler = ProjectionV3Compiler()
        reader = ProjectionV3CanonicalDocumentReader()
        projected = [compiler.compile(doc).to_mapping() for doc in runs["s5"].documents]
        hydrated = tuple(reader.read(record) for record in projected)
        _require(_stable_documents(hydrated) == _stable_documents(runs["s5"].documents),
                 "Projection v3 round trip changed canonical evidence")
        _require(TRUTH["sentinel"] not in json.dumps(projected),
                 "secret sentinel reached projected records")

        (root / "references/architecture.md").unlink()
        incremental = indexers["s5"].index(root, previous_documents=runs["s5"].documents)
        rebuilt = indexers["s5"].index(root)
        _require(_stable_documents(incremental.documents) == _stable_documents(rebuilt.documents),
                 "incremental state differs from full canonical rebuild")
        _, remaining_relations = _facts(incremental.documents)
        _require(("markdown_reference", "references/README.md", "references/architecture.md")
                 not in remaining_relations, "deleted target retains a resolved relation")
        _require(incremental.changes.removal_detection_complete,
                 "deletion detection was incomplete")
        _require(any(item.path == "references/architecture.md" for item in incremental.changes.removed),
                 "deleted target was not removed")

        return {
            "protocol": "fetchcore.issue-181-integrated-canonical-v1",
            "scope": "synthetic canonical and projection; no live backend or ranked retrieval",
            "fetchcore_product_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            ).strip(),
            "repository": REPOSITORY,
            "revision": revision,
            "source_sha256": source_hashes,
            "ground_truth_sha256": hashlib.sha256(
                (Path(__file__).parent / "integrated-ground-truth.json").read_bytes()
            ).hexdigest(),
            "arms": {
                name: {
                    "documents": len(result.documents),
                    "paths": len({doc.path for doc in result.documents}),
                    "fingerprint": indexing_contract_fingerprint(indexers[name]),
                    "canonical_digest": _digest(_stable_documents(result.documents)),
                    "skip_reasons": result.telemetry.skip_reasons,
                } for name, result in runs.items()
            },
            "api_facts": len(api),
            "markdown_spans": len(TRUTH["markdown"]),
            "resolved_relations": len(expected_relations),
            "projection_fingerprint": compiler.projection_fingerprint,
            "projection_records": len(projected),
            "projection_round_trip": True,
            "unchanged_reindex_noop": True,
            "deletion_diff": {
                "added": len(incremental.changes.added),
                "updated": len(incremental.changes.updated),
                "removed": len(incremental.changes.removed),
                "unchanged": len(incremental.changes.unchanged),
            },
            "incremental_equals_rebuild": True,
            "deleted_relation_absent": True,
            "sentinel_exposed": False,
            "status": "PASS",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
