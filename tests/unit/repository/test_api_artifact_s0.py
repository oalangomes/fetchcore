"""Security regression for API artifacts during the S0 quarantine window."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
from fetchcore.application.service import (
    RepositoryBinding,
    RetrievalApplicationService,
    SourceReadRequest,
)
from fetchcore.repository.api_artifact_policy import blocked_api_artifact_kind
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.safe_scan import scan_repository_text
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    SearchIntent,
    SearchScope,
)

SENTINEL = "S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"


@pytest.mark.parametrize(
    ("filename", "text", "kind"),
    [
        ("openapi.json", '{ "openapi": "3.1.0", "x-token": "S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE" }', "openapi"),
        ("random.json", '{"openapi":"3.0.3","x-api-key":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}', "openapi"),
        ("spec.yml", "openapi: 3.0.0\ninfo:\n  token: S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE\n", "openapi"),
        ("swagger.yaml", "swagger: '2.0'\n", "swagger"),
        ("unusual.json", '{"swagger":"2.0","secret":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}', "swagger"),
        (
            "collection.json",
            '{"info":{"schema":"https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},"item":[{"request":{"auth":{"bearer":[{"value":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}]}}}]}',
            "postman_collection",
        ),
        (
            "demo.postman_collection.json",
            '{ "bad json": "S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE", ',
            "postman_collection",
        ),
        (
            "local.postman_environment.json",
            '{"values":[{"key":"TOKEN","value":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}]}',
            "postman_environment",
        ),
        (
            "opaque.json",
            '{"_postman_variable_scope":"environment","values":[{"value":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}]}',
            "postman_environment",
        ),
        ("folder/api.request.yaml", "method: GET\nsecret: S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE", "postman_v3"),
    ],
)
def test_recognized_api_artifact_never_enters_generic_scan(
    tmp_path: Path, filename: str, text: str, kind: str,
) -> None:
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    assert blocked_api_artifact_kind(filename, text) == kind
    scan = scan_repository_text(
        tmp_path,
        include_patterns=("*.json", "*.yaml", "*.yml"),
        exclude_patterns=(".git",),
        max_files=30,
        max_file_bytes=100_000,
        max_total_bytes=500_000,
    )
    assert not scan.sources
    assert scan.skipped[f"api_artifact_blocked_{kind}"] == 1
    assert SENTINEL not in json.dumps(scan.audit_mapping())
    assert SENTINEL not in json.dumps(scan.coverage.to_mapping())


def test_known_filenames_fail_closed_even_with_invalid_content() -> None:
    assert blocked_api_artifact_kind("api/Swagger.json", "not json") == "swagger"
    assert blocked_api_artifact_kind("api/OPENAPI.yml", ":::") == "openapi"
    assert blocked_api_artifact_kind("collections/test.postman_globals.json", "{}") == (
        "postman_environment"
    )


def test_unrecognized_configuration_still_indexed(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        '{"feature":"safe","enabled":true}\n', encoding="utf-8",
    )
    scan = scan_repository_text(
        tmp_path,
        include_patterns=("*.json",),
        exclude_patterns=(),
        max_files=10,
        max_file_bytes=200_000,
        max_total_bytes=300_000,
    )
    assert [source.relative for source in scan.sources] == ["settings.json"]
    assert scan.skipped.get("api_artifact_blocked_openapi", 0) == 0


def test_canonical_index_never_projects_recognized_raw_api_content(
    tmp_path: Path,
) -> None:
    (tmp_path / "demo.postman_environment.json").write_text(
        '{"values":[{"key":"authToken","value":"' + SENTINEL + '"}]}',
        encoding="utf-8",
    )
    (tmp_path / "spec.json").write_text(
        '{"openapi":"3.0.0","example":"' + SENTINEL + '"}',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Safe docs\nHello.\n", encoding="utf-8")
    indexer = RepositoryIndexer(RepositoryIndexConfig(repository="test/api-s0"))
    result = indexer.index(tmp_path)
    assert {doc.path for doc in result.documents} == {"README.md"}
    assert SENTINEL not in json.dumps([doc.to_mapping() for doc in result.documents])
    assert SENTINEL not in json.dumps(result.telemetry.to_mapping(), default=str)
    assert indexing_contract_fingerprint(indexer).startswith("sha256:")


def test_local_lexical_and_targeted_lookup_do_not_return_sensitive_api_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "unsafe.json").write_text(
        '{"_postman_variable_scope":"environment","values":[{"value":"' + SENTINEL + '"}]}',
        encoding="utf-8",
    )
    (tmp_path / "normal.md").write_text("This is a safe project description.\n", encoding="utf-8")
    scope = SearchScope(
        workspace=tmp_path,
        revision=None,
        repository="test/api-s0",
        knowledge_types=(),
        max_results=10,
        settings={"max_matches": 10},
    )
    backend = LocalSemanticBackend()
    found = backend.search(SearchIntent(canonical_query=SENTINEL), scope, ())
    assert not found.results
    assert SENTINEL not in json.dumps([r.to_mapping() for r in found.results])
    assert found.metadata["safe_scan"]["skipped"]["api_artifact_blocked_postman_environment"] == 1


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("secret.postman_environment.json", '{"values":[{"value":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}]}'),
        ("opaque.json", '{"_postman_variable_scope":"environment","value":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}'),
        ("openapi.json", '{"openapi":"3.0.0","secret":"S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"}'),
        ("collection.json", '{"info":{"schema":"https://schema.getpostman.com/json/collection/v2.1.0/collection.json"}}'),
    ],
)
def test_get_source_refuses_raw_api_artifact_even_when_requested_explicitly(
    tmp_path: Path, filename: str, payload: str,
) -> None:
    (tmp_path / filename).write_text(payload, encoding="utf-8")
    service = RetrievalApplicationService(
        {
            "test/api-s0": RepositoryBinding(
                repository="test/api-s0",
                workspace=tmp_path,
                backend="local-semantic",
                revision="snapshot",
            )
        }
    )
    with pytest.raises(KnowledgeSearchError) as caught:
        service.get_source(
            SourceReadRequest(
                repository="test/api-s0",
                source_ref=f"repository://test/api-s0/{filename}",
                revision="snapshot",
            )
        )
    assert caught.value.category == "unsafe_source"
    assert SENTINEL not in str(caught.value)


def test_safe_source_still_available_via_get_source(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Safe heading\n", encoding="utf-8")
    service = RetrievalApplicationService(
        {
            "test/api-s0": RepositoryBinding(
                repository="test/api-s0",
                workspace=tmp_path,
                backend="local-semantic",
            )
        }
    )
    read = service.get_source(
        SourceReadRequest(
            repository="test/api-s0",
            source_ref="repository://test/api-s0/README.md",
        )
    )
    assert "Safe heading" in read.content



def test_synthetic_protocol_reports_safe_current_index() -> None:
    root = Path(__file__).resolve().parents[3]
    protocol = runpy.run_path(str(root / "benchmarks/api-extraction-s0/run.py"))
    truth = json.loads(
        (root / "benchmarks/api-extraction-s0/ground-truth.json").read_text(encoding="utf-8")
    )
    metrics = protocol["measure"](
        root,
        root / "benchmarks/api-extraction-s0/fixtures",
        truth["queries"],
        truth["security_sentinel"],
        2,
    )
    assert metrics["secret_in_canonical"] is False
    assert metrics["indexing_latency_ms"]["repetitions"] == 2
    assert all(not row["secret_in_results"] for row in metrics["queries"])
    assert all(row["blocked"] and not row["secret_exposed"] for row in metrics["source_access"])
