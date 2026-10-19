"""S2: opt-in OpenAPI/Swagger canonical extraction and S0 security regression."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from fetchcore.projection import ProjectionV3Compiler
from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.openapi_extraction import sanitize_openapi
from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer
from fetchcore.repository.safe_scan import scan_repository_text
from fetchcore.retrieval.embedding import ProjectionV3EmbeddingSerializer

SECRET = "S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"


def test_large_safe_openapi_is_sanitized_but_oversize_spec_fails_closed() -> None:
    payload = json.dumps({
        "openapi": "3.1.0",
        "info": {"title": "Large safe spec", "version": "1", "description": SECRET + "x" * 300_000},
        "paths": {"/customers": {"get": {"responses": {"200": {"description": "OK"}}}}},
    })
    result = sanitize_openapi("openapi.json", payload)
    assert result is not None
    assert any(unit.kind == "api_operation" and unit.route == "/customers"
               for unit in result.units)
    assert SECRET not in json.dumps([unit.content for unit in result.units])
    assert sanitize_openapi("openapi.json", payload + " " * 700_000) is None


def _fixture(root: Path) -> None:
    (root / "openapi.json").write_text(
        json.dumps({
            "openapi": "3.1.0",
            "info": {"title": "S2 test", "version": "1"},
            "paths": {
                "/customers": {
                    "post": {
                        "operationId": "createCustomer",
                        "summary": SECRET,
                        "requestBody": {
                            "description": SECRET,
                            "content": {"application/json": {"example": {"token": SECRET}}},
                        },
                        "responses": {
                            "201": {
                                "description": SECRET,
                                "schema": {"$ref": "#/components/schemas/Customer"},
                            },
                        },
                    },
                    "get": {
                        "operationId": "listCustomers",
                        "parameters": [{"name": "page", "in": "query"}],
                        "responses": {"200": {"description": "OK"}},
                    },
                },
                "/customers/{id}": {
                    "get": {
                        "operationId": "getCustomer",
                        "responses": {"200": {"description": "Found"}},
                    },
                },
            },
            "components": {"schemas": {
                "Customer": {
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string", "example": SECRET},
                    },
                },
            }},
            "security": [{"BearerAuth": []}],
        }),
        encoding="utf-8",
    )
    (root / "legacy.yaml").write_text(
        "swagger: '2.0'\ninfo:\n  title: Customer service\n  version: '1'\n"
        "paths:\n  /orders:\n    get:\n      operationId: listOrders\n"
        "      responses:\n        '200':\n          description: OK\n"
        "definitions:\n  Order:\n    type: object\n    properties:\n"
        "      id:\n        type: string\n",
        encoding="utf-8",
    )
    (root / "demo.postman_environment.json").write_text(
        '{"values":[{"key":"token","value":"' + SECRET + '"}]}',
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Safe README\n", encoding="utf-8")


def _serialized(docs: tuple[object, ...]) -> str:
    return json.dumps([doc.to_mapping() for doc in docs], default=str)  # type: ignore[attr-defined]


def test_openapi_interpreter_recovers_typed_operations_schemas_and_refs(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    indexer = OpenApiRepositoryIndexer(RepositoryIndexConfig(repository="fixture/s2"))
    result = indexer.index(tmp_path)
    docs = result.documents
    assert result.changes.removal_detection_complete
    assert not result.telemetry.removal_detection_blockers
    facts = [
        ApiEvidenceV1.from_envelope(document.evidence.semantic[0])
        for document in docs if document.evidence.semantic
    ]
    observed = {(fact.kind, fact.method, fact.route) for fact in facts}
    assert ("api_operation", "POST", "/customers") in observed
    assert ("api_operation", "GET", "/customers") in observed
    assert ("api_operation", "GET", "/customers/{id}") in observed
    assert ("api_operation", "GET", "/orders") in observed
    assert sum(f.kind == "api_operation" for f in facts) == 4
    assert sum(f.kind == "api_schema" for f in facts) == 2
    assert sum(f.kind == "api_reference" for f in facts) == 1
    assert all(f.locator.line_start is None for f in facts)
    assert all(doc.provenance.source_checksum.startswith("sha256:") for doc in docs)
    assert SECRET not in _serialized(docs)
    assert not any(doc.path.endswith("postman_environment.json") for doc in docs)
    assert all(
        SECRET not in json.dumps(ProjectionV3Compiler().compile(doc).to_mapping())
        for doc in docs
    )
    for doc in docs:
        projection = ProjectionV3Compiler().compile(doc)
        assert SECRET not in ProjectionV3EmbeddingSerializer().serialize_record(projection)


def test_s0_is_unchanged_and_s2_requires_explicit_indexer(tmp_path: Path) -> None:
    _fixture(tmp_path)
    original = RepositoryIndexer(RepositoryIndexConfig(repository="fixture/s2"))
    safe = original.index(tmp_path)
    assert {doc.path for doc in safe.documents} == {"README.md"}
    active = OpenApiRepositoryIndexer(RepositoryIndexConfig(repository="fixture/s2"))
    assert indexing_contract_fingerprint(original) != indexing_contract_fingerprint(active)
    result = active.index(tmp_path)
    assert "openapi.json" in {doc.path for doc in result.documents}
    assert "legacy.yaml" in {doc.path for doc in result.documents}


@pytest.mark.parametrize(
    ("filename", "raw"),
    [
        ("openapi.json", '{"openapi":"3.1.0","openapi":"3.0.0","paths":{}}'),
        ("swagger.json", '{"swagger":"2.0","paths":{"secret":{}}}'),
        ("openapi.json", '{"openapi":"3.2.0","paths":{"/safe":{"get":{"responses":{}}}}}'),
        ("openapi.yaml", "openapi: 3.1.0\npaths: &p {}\nmore: *p\n"),
        ("openapi.yaml", "openapi: 3.1.0\npaths: {}\npaths: {}\n"),
        ("openapi.json", '{"openapi":"3.1.0","paths":{"/customers":{"get":{"summary":"' + SECRET + '"}}}'),
        ("openapi.json", '{"openapi":"3.1.0","paths":{"/' + SECRET + '":{"get":{"responses":{}}}}}'),
    ],
)
def test_unsafe_or_unsupported_source_is_quarantined_without_generic_fallback(
    tmp_path: Path, filename: str, raw: str,
) -> None:
    (tmp_path / filename).write_text(raw, encoding="utf-8")
    result = OpenApiRepositoryIndexer(RepositoryIndexConfig(repository="fixture/s2")).index(tmp_path)
    assert not result.documents
    assert result.changes.removal_detection_complete
    assert SECRET not in json.dumps(result.to_mapping(), default=str)


def test_safe_scanner_only_admits_sanitized_content_when_explicitly_enabled(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    from fetchcore.repository.openapi_extraction import sanitize_openapi

    opts = {
        "include_patterns": ("*.json", "*.yaml"),
        "exclude_patterns": (),
        "max_files": 20,
        "max_file_bytes": 500_000,
        "max_total_bytes": 1_000_000,
    }
    baseline = scan_repository_text(tmp_path, **opts)
    assert baseline.sources == ()
    enabled = scan_repository_text(tmp_path, api_sanitizer=sanitize_openapi, **opts)
    assert {source.relative for source in enabled.sources} == {"openapi.json", "legacy.yaml"}
    assert all(source.api_units for source in enabled.sources)
    assert all(SECRET not in source.content for source in enabled.sources)
    assert enabled.skipped["api_artifact_blocked_postman_environment"] == 1


def test_schema_only_without_paths_is_not_admitted() -> None:
    assert sanitize_openapi(
        "openapi.json",
        '{"openapi":"3.1.0","components":{"schemas":{"Test":{"type":"string"}}}}',
    ) is None


def test_interpreter_has_deterministic_id_and_source_pointer(tmp_path: Path) -> None:
    _fixture(tmp_path)
    indexer = OpenApiRepositoryIndexer(RepositoryIndexConfig(repository="fixture/s2"))
    first = indexer.index(tmp_path)
    second = indexer.index(tmp_path, previous_documents=first.documents)
    assert [doc.id for doc in first.documents] == [doc.id for doc in second.documents]
    assert len(second.changes.unchanged) == len(first.documents)
    pointers = {
        fact.locator.pointer
        for doc in first.documents
        if doc.evidence.semantic
        for fact in [ApiEvidenceV1.from_envelope(doc.evidence.semantic[0])]
    }
    assert "#/paths/~1customers/post" in pointers
    assert "#/paths/~1customers/get" in pointers



def test_frozen_s2_operation_recovery_protocol() -> None:
    protocol = Path(__file__).resolve().parents[3] / "benchmarks/api-extraction-s2/run.py"
    metrics = runpy.run_path(str(protocol))["evaluate"](repeats=2)
    assert metrics["protocol"] == "fetchcore.api-extraction-s2"
    assert metrics["expected_operations"] == 3
    assert metrics["recovered_operations"] == 3
    assert metrics["operation_ground_truth_recovery"] == 1.0
    assert metrics["postman_quarantined"]
    assert metrics["distinct_indexing_fingerprint"]
    assert not metrics["secret_sentinel_present"]



@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize("extension", ["json", "yaml"])
def test_openapi_3_x_json_and_yaml_operation_extraction(
    version: str, extension: str,
) -> None:
    operation = {
        "openapi": version,
        "paths": {"/products": {
            "get": {"operationId": "listProducts", "responses": {"200": {"description": "OK"}}},
        }},
    }
    raw = (
        json.dumps(operation)
        if extension == "json"
        else f"openapi: '{version}'\npaths:\n  /products:\n    get:\n"
             "      operationId: listProducts\n      responses:\n"
             "        '200':\n          description: OK\n"
    )
    result = sanitize_openapi(f"spec.{extension}", raw)
    assert result is not None
    units = result.units
    assert len(units) == 1
    assert units[0].format == "openapi"
    assert units[0].format_version == version
    assert units[0].pointer == "#/paths/~1products/get"
    assert (units[0].method, units[0].route) == ("GET", "/products")


def test_operation_override_and_nested_refs_are_observed_without_dereferencing() -> None:
    root = {
        "openapi": "3.1.0",
        "security": [{"BearerAuth": []}],
        "paths": {"/inventory/{id}": {
            "parameters": [
                {"name": "id", "in": "path", "schema": {"type": "string"}},
                {"name": "limit", "in": "query", "schema": {"type": "string"}},
            ],
            "get": {
                "operationId": "getInventory",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                ],
                "requestBody": {"content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Input"}},
                }},
                "responses": {"200": {"content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Output"}},
                }}, "400": {"$ref": "https://malicious.invalid/openapi.yaml"}},
            },
        }},
    }
    result = sanitize_openapi("openapi.json", json.dumps(root))
    assert result is not None
    op = [u for u in result.units if u.kind == "api_operation"]
    assert len(op) == 1
    assert op[0].content.count("query:limit") == 1
    assert "path:id" in op[0].content
    assert "security required" in op[0].content
    targets = {unit.target for unit in result.units if unit.kind == "api_reference"}
    assert targets == {"#/components/schemas/Input", "#/components/schemas/Output"}
    assert all("malicious.invalid" not in u.content for u in result.units)
    assert any(
        u.pointer.endswith("/content/application~1json/schema/$ref")
        for u in result.units if u.kind == "api_reference"
    )


def test_swagger_2_json_and_yaml_are_equivalent() -> None:
    original = {
        "swagger": "2.0",
        "paths": {"/orders": {"post": {
            "operationId": "createOrder",
            "responses": {"201": {"description": "Created"}},
        }}},
    }
    yaml_raw = (
        "swagger: '2.0'\npaths:\n  /orders:\n    post:\n"
        "      operationId: createOrder\n      responses:\n"
        "        '201':\n          description: Created\n"
    )
    json_units = sanitize_openapi("swagger.json", json.dumps(original))
    yaml_units = sanitize_openapi("swagger.yaml", yaml_raw)
    assert json_units is not None and yaml_units is not None
    assert json_units.units == yaml_units.units


def test_raw_secret_in_description_cannot_enter_safe_units() -> None:
    document = {
        "openapi": "3.0.3",
        "paths": {"/health": {"get": {
            "summary": SECRET,
            "description": SECRET,
            "responses": {"200": {"description": SECRET}},
        }}},
    }
    result = sanitize_openapi("contracts/health.json", json.dumps(document))
    assert result is not None
    assert SECRET not in json.dumps([u.__dict__ for u in result.units])
