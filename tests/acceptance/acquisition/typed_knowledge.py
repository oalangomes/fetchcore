"""Verify pinned real source truth against canonical typed knowledge, without backends."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from fetchcore.projection.core import ProjectionV3CanonicalDocumentReader, ProjectionV3Compiler
from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer
from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer

TRUTH_PATH = Path(__file__).resolve().parents[3] / "benchmarks/typed-knowledge-retrieval/ground-truth.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def _stable(documents: tuple[KnowledgeDocument, ...]) -> list[dict[str, Any]]:
    records = []
    for document in documents:
        record = document.to_mapping()
        record.pop("indexed_at")
        records.append(record)
    return records


def _pointer(root: object, pointer: str) -> Any:
    value = root
    for part in pointer.removeprefix("#/").split("/"):
        token = part.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
    return value


def _verify_source(root: Path, truth: dict[str, Any]) -> dict[str, str]:
    corpus = truth["corpus"]
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    _require(revision == corpus["revision"], "corpus revision differs from reviewed Ground Truth")
    _require(not subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip(), "corpus checkout is dirty")
    hashes = {}
    for path, expected in corpus["source_sha256"].items():
        source = (root / path).resolve(strict=True)
        _require(source.is_relative_to(root) and source.is_file(), f"unsafe corpus path: {path}")
        observed = _sha(source.read_bytes())
        _require(observed == expected, f"source checksum changed: {path}")
        hashes[path] = observed
    for case in truth["acquisition_cases"]:
        path = case["path"]
        source = root / path
        if case["kind"] == "api_operation":
            parsed = json.loads(source.read_text(encoding="utf-8"))
            methods = parsed["paths"].get(case["route"], {})
            exists = case["method"].lower() in methods
            _require(exists == (case["expect"] == "present"), f"source method mismatch: {case['id']}")
            if exists:
                _require(_pointer(parsed, case["pointer"]) == methods[case["method"].lower()], f"source pointer mismatch: {case['id']}")
        elif case["kind"] == "api_request":
            parsed = json.loads(source.read_text(encoding="utf-8"))
            item = _pointer(parsed, case["pointer"])
            request = item["request"]
            route = "/" + "/".join(request["url"]["path"])
            _require((request["method"], route) == (case["method"], case.get("source_route", case["route"])), f"source request mismatch: {case['id']}")
        elif case["kind"] == "ambiguous_request":
            parsed = json.loads(source.read_text(encoding="utf-8"))
            pointers = case["pointers"]
            _require(len(pointers) >= 2 and len(set(pointers)) == len(pointers), f"invalid ambiguity: {case['id']}")
            names = set()
            for pointer in pointers:
                item = _pointer(parsed, pointer)
                request = item["request"]
                route = "/" + "/".join(request["url"]["path"])
                _require((request["method"], route) == (case["method"], case["route"]), f"source ambiguity mismatch: {case['id']}")
                names.add(item.get("name"))
            _require(len(names) >= 2, f"ambiguous requests are not distinct: {case['id']}")
        elif case["kind"] == "markdown_span":
            lines = source.read_text(encoding="utf-8").splitlines()
            start, end = case["line_start"], case["line_end"]
            _require(1 <= start <= end <= len(lines) and case["evidence"] in "\n".join(lines[start - 1:end]), f"source span mismatch: {case['id']}")
        else:
            _require(case["kind"] == "quarantined_source" and "postman_environment" in path, f"invalid negative case: {case['id']}")
    for relation in truth["relation_cases"]:
        if relation["expect"] != "resolved":
            continue
        source = root / relation["source_path"]
        if relation["kind"] == "api_reference":
            parsed = json.loads(source.read_text(encoding="utf-8"))
            _require(_pointer(parsed, relation["target_locator"]) is not None, f"missing source reference target: {relation['id']}")
            _require(relation["target_locator"] in source.read_text(encoding="utf-8"), f"missing literal source reference: {relation['id']}")
        else:
            target = Path(relation["target_path"]).name
            _require(target in source.read_text(encoding="utf-8"), f"missing Markdown link: {relation['id']}")
    return hashes


def preflight(workspace: Path, truth_path: Path = TRUTH_PATH) -> tuple[dict[str, Any], tuple[KnowledgeDocument, ...], dict[str, str]]:
    """Return an attested canonical corpus and expected IDs for later retrieval."""
    root = workspace.resolve(strict=True)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    hashes = _verify_source(root, truth)
    corpus = truth["corpus"]
    config = RepositoryIndexConfig(repository=corpus["repository"], revision=corpus["revision"], include_patterns=tuple(hashes))
    indexers = {
        "default": RepositoryIndexer(config),
        "openapi": OpenApiRepositoryIndexer(config),
        "api_contracts": ApiContractRepositoryIndexer(config),
        "local_relations": ApiContractRepositoryIndexer(config, resolve_local_references=True),
    }
    runs = {name: indexer.index(root) for name, indexer in indexers.items()}
    canonical = runs["local_relations"]
    default_paths = {d.path for d in runs["default"].documents}
    openapi_paths = {d.path for d in runs["openapi"].documents}
    typed_paths = {d.path for d in canonical.documents}
    api_path = next(c["path"] for c in truth["acquisition_cases"] if c["kind"] == "api_operation")
    postman_path = next(c["path"] for c in truth["acquisition_cases"] if c["kind"] == "api_request")
    _require(api_path not in default_paths and postman_path not in default_paths, "default indexer admitted API artifact")
    _require(api_path in openapi_paths and postman_path not in openapi_paths, "OpenAPI opt-in boundary failed")
    _require(api_path in typed_paths and postman_path in typed_paths, "combined API opt-in failed")
    for case in truth["acquisition_cases"]:
        if case["kind"] == "quarantined_source":
            _require(case["path"] not in typed_paths, f"unsafe source admitted: {case['id']}")
    expected_ids = {}
    for case in truth["acquisition_cases"]:
        if case["kind"] == "quarantined_source" or case["expect"] == "pending":
            continue
        matches = []
        for document in canonical.documents:
            if document.path != case["path"]:
                continue
            if case["kind"] == "markdown_span":
                if document.line_start == case["line_start"] and document.line_end == case["line_end"] and case["evidence"] in document.content:
                    matches.append(document)
            else:
                for envelope in document.evidence.semantic:
                    if envelope.namespace != "api_contract":
                        continue
                    fact = ApiEvidenceV1.from_envelope(envelope)
                    if (fact.kind, fact.method, fact.route, fact.locator.pointer) == (case["kind"], case["method"], case["route"], case["pointer"]):
                        matches.append(document)
        expected = 1 if case["expect"] == "present" else 0
        _require(len(matches) == expected, f"canonical evidence mismatch: {case['id']} ({len(matches)})")
        if matches:
            expected_ids[case["id"]] = matches[0].id
    _require({case["id"] for case in truth["ranking_ground_truth"]} == set(expected_ids), "ranking Ground Truth must name exactly the verified positive cases")
    relations = {(payload["kind"], document.path, payload["target_path"], payload.get("target_locator"))
                 for document in canonical.documents for envelope in document.evidence.semantic
                 if envelope.kind == "resolution" for payload in [envelope.payload()]
                 if payload["status"] == "resolved"}
    for case in truth["relation_cases"]:
        key = (case["kind"], case["source_path"], case["target_path"], case["target_locator"])
        if case["expect"] == "resolved":
            _require(key in relations, f"expected relation missing: {case['id']}")
        else:
            _require(key not in relations, f"unproved relation resolved: {case['id']}")
    for document in canonical.documents:
        _require(document.repository == corpus["repository"] and document.revision == corpus["revision"], f"identity mismatch: {document.path}")
        _require(document.provenance.source_checksum == "sha256:" + hashes[document.path], f"source checksum mismatch: {document.path}")
        _require(bool(document.id and document.checksum and document.source_ref), f"incomplete provenance: {document.path}")
    compiler, reader = ProjectionV3Compiler(), ProjectionV3CanonicalDocumentReader()
    projected = tuple(compiler.compile(document).to_mapping() for document in canonical.documents)
    _require(_stable(tuple(reader.read(record) for record in projected)) == _stable(canonical.documents), "Projection v3 round trip changed evidence")
    repeated = indexers["local_relations"].index(root, previous_documents=canonical.documents)
    _require(not (repeated.changes.added or repeated.changes.updated or repeated.changes.removed), "unchanged reindex produced a diff")
    _require(_stable(repeated.documents) == _stable(canonical.documents), "unchanged canonical state drifted")
    with tempfile.TemporaryDirectory(prefix="typed-knowledge-") as temp:
        copy_root = Path(temp)
        for path in hashes:
            target = copy_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / path, target)
        target_path = next(c["target_path"] for c in truth["relation_cases"] if c["kind"] == "markdown_reference" and c["expect"] == "resolved")
        (copy_root / target_path).unlink()
        incremental = indexers["local_relations"].index(copy_root, previous_documents=canonical.documents)
        rebuilt = indexers["local_relations"].index(copy_root)
        _require(_stable(incremental.documents) == _stable(rebuilt.documents), "deletion diff differs from rebuild")
        _require(any(d.path == target_path for d in incremental.changes.removed), "deleted source remains canonical")
        _require(incremental.changes.removal_detection_complete, "deletion detection incomplete")
    summary = {
        "protocol": "fetchcore.canonical-consistency-v1",
        "status": "PASS",
        "scope": "real corpus acquisition and projection, no remote backend or ranked retrieval",
        "repository": corpus["repository"], "revision": corpus["revision"],
        "ground_truth_sha256": _sha(truth_path.read_bytes()), "source_sha256": hashes,
        "acquisition_cases": len(truth["acquisition_cases"]), "positive_evidence": len(expected_ids),
        "pending_cases": sum(case["expect"] == "pending" for case in truth["acquisition_cases"]),
        "relation_cases": len(truth["relation_cases"]),
        "arms": {name: {"documents": len(result.documents), "fingerprint": indexing_contract_fingerprint(indexers[name]), "skip_reasons": result.telemetry.skip_reasons} for name, result in runs.items()},
        "canonical_records": len(canonical.documents), "canonical_digest": _sha(json.dumps(_stable(canonical.documents), sort_keys=True, separators=(",", ":")).encode()),
        "projection_fingerprint": compiler.projection_fingerprint, "projection_records": len(projected),
        "unchanged_reindex_noop": True, "incremental_equals_rebuild": True,
        "deletion_diff": {"added": len(incremental.changes.added), "updated": len(incremental.changes.updated), "removed": len(incremental.changes.removed)},
        "facts_by_kind": dict(Counter(e.kind for d in canonical.documents for e in d.evidence.semantic)),
    }
    return summary, canonical.documents, expected_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--ground-truth", type=Path, default=TRUTH_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary, _, _ = preflight(args.corpus, args.ground_truth)
    rendered = json.dumps(summary, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
