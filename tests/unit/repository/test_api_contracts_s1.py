"""S1 API format preflight and evidence v1 contracts (no raw API admission)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from fetchcore.repository.api_contracts import (
    ApiEvidenceV1, ApiSourceLocatorV1, attach_api_evidence,
)
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.knowledge.evidence import KnowledgeEvidence
from fetchcore.repository.api_detection import (
    ApiArtifactClassification,
    detect_api_artifact,
    select_api_interpreter,
)
from fetchcore.repository.safe_scan import scan_repository_text
from fetchcore.repository.semantic import SemanticEvidenceEnvelope

SENTINEL = "S1_FAKE_SECRET_DO_NOT_PERSIST"


@pytest.mark.parametrize(
    ("path", "payload", "kind", "version", "status"),
    [
        ("api.json", '{"openapi":"3.0.3","paths":{}}', "openapi", "3.0.3", "supported"),
        ("api-minified.json", '{"openapi":"3.1.1","paths":{}}', "openapi", "3.1.1", "supported"),
        ("spec.yaml", 'openapi: "3.1.0"\npaths: {}\n', "openapi", "3.1.0", "supported"),
        ("swagger.json", '{"swagger":"2.0"}', "swagger", "2.0", "supported"),
        ("example.yml", "swagger: '2.0'\n", "swagger", "2.0", "supported"),
        (
            "nested.json",
            '{"info":{"schema":"https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},"item":[]}',
            "postman_collection", "2.1.0", "supported",
        ),
        ("new.json", '{"openapi":"3.2.0"}', "openapi", "3.2.0", "unsupported"),
        ("old.json", '{"swagger":"1.2"}', "swagger", "1.2", "unsupported"),
        (
            "postman_collection.json",
            '{"info":{"schema":"https://schema.getpostman.com/json/collection/v2.0.0/collection.json"},"item":[]}',
            "postman_collection", "2.0.0", "unsupported",
        ),
        ("openapi.json", '{"openapi":"3.1.0",', "openapi", None, "invalid"),
        ("openapi.yaml", "hello: world\n", "openapi", None, "invalid"),
        ("bad.json", '{"swagger":"2.0","openapi":"3.1.0"}', "openapi", None, "invalid"),
        ("coll.postman_collection.json", '{"item":[]}', "postman_collection", None, "invalid"),
        (
            "env.json", '{"_postman_variable_scope":"environment","values":[{"value":"' +
            SENTINEL + '"}]}', "postman_environment", None, "quarantined",
        ),
        ("folder/customer.request.yaml", "method: GET\n", "postman_v3", None, "quarantined"),
        ("config.json", '{"feature":"on"}', None, None, "unrecognized"),
    ],
)
def test_detection_only_returns_closed_format_version_and_status(
    path: str, payload: str, kind: str | None, version: str | None, status: str,
) -> None:
    decision = detect_api_artifact(path, payload)
    assert (decision.kind, decision.version, decision.status) == (kind, version, status)
    assert SENTINEL not in json.dumps(decision.__dict__)


def test_malformed_and_conflicting_inputs_fail_closed_without_payload_leak() -> None:
    examples = (
        ("openapi.json", '{"openapi":"3.1.0","openapi":"3.0.0"}'),
        ("opaque.json", '{"openapi":"3.1.0","paths":{"password":"' + SENTINEL + '"},"openapi":"3.1.0"}'),
        ("opaque.json", '{"openapi":123}'),
        ("spec.yaml", "openapi: 3.1.0\nswagger: 2.0\n"),
    )
    assert all(detect_api_artifact(*case).status == "invalid" for case in examples)


@pytest.mark.parametrize(
    ("path", "pointer"),
    [
        ("../secret.yaml", "#/paths/~1orders/get"),
        ("/absolute.yaml", "#/paths"),
        ("api\\escape.yaml", "#/paths"),
        ("api.yaml", "#/paths/~2not-escaped"),
        ("api.yaml", "#bad"),
    ],
)
def test_locator_fails_closed_for_unsafe_path_or_pointer(path: str, pointer: str) -> None:
    with pytest.raises(ValueError):
        ApiSourceLocatorV1(path=path, pointer=pointer)


def test_operation_envelope_is_deterministic_and_precise_without_spans() -> None:
    observation = ApiEvidenceV1(
        kind="api_operation",
        format="openapi",
        format_version="3.1.0",
        locator=ApiSourceLocatorV1(
            path="contracts/api.yaml", pointer="#/paths/~1customers/post",
        ),
        method="POST",
        route="/customers",
        name="createCustomer",
    )
    envelope = observation.to_envelope()
    assert envelope.schema_id == "api_contract/api_operation@1"
    assert ApiEvidenceV1.from_envelope(envelope) == observation
    assert observation.discriminator == "api_operation:#/paths/~1customers/post"
    assert envelope.to_mapping()["payload"]["line_start"] is None
    assert observation.to_envelope().payload_json == envelope.payload_json


def test_repeated_postman_request_names_remain_distinct_by_pointer() -> None:
    first = ApiEvidenceV1(
        kind="api_request", format="postman_collection", format_version="2.1.0",
        locator=ApiSourceLocatorV1(path="collections/team.json", pointer="#/item/0/item/0"),
        method="GET", route="/orders", name="Get order",
    )
    second = ApiEvidenceV1(
        kind="api_request", format="postman_collection", format_version="2.1.0",
        locator=ApiSourceLocatorV1(path="collections/team.json", pointer="#/item/1/item/0"),
        method="GET", route="/orders", name="Get order",
    )
    assert first.discriminator != second.discriminator


@pytest.mark.parametrize(
    "field",
    [
        "secret",
        "raw_body",
        "authorization",
        "api_key",
    ],
)
def test_api_evidence_rejects_uncontracted_payload_fields(field: str) -> None:
    valid = ApiEvidenceV1(
        kind="api_operation", format="swagger", format_version="2.0",
        locator=ApiSourceLocatorV1("api/swagger.json", "#/paths/~1orders/get"),
        method="GET", route="/orders",
    ).to_envelope()
    payload = valid.payload()
    payload[field] = SENTINEL
    corrupt = SemanticEvidenceEnvelope.from_payload(
        namespace="api_contract", kind="api_operation", version="1", payload=payload,
    )
    with pytest.raises(ValueError):
        ApiEvidenceV1.from_envelope(corrupt)


@pytest.mark.parametrize(
    "malformed",
    [
        {"method": "POST", "route": None},
        {"method": None, "route": "/customers"},
        {"method": "INVALID", "route": "/customers"},
        {"method": "GET", "route": "https://example.test/path"},
        {"method": "GET", "route": "/path?access_token=redacted"},
    ],
)
def test_operation_contract_rejects_missing_or_unsafe_method_route(
    malformed: dict[str, str | None],
) -> None:
    with pytest.raises(ValueError):
        ApiEvidenceV1(
            kind="api_operation", format="openapi", format_version="3.0.3",
            locator=ApiSourceLocatorV1("openapi.json", "#/paths/~1customers/post"),
            method=malformed["method"], route=malformed["route"],
        )


def test_wrong_version_or_kind_cannot_hydrate() -> None:
    valid = ApiEvidenceV1(
        kind="api_schema", format="openapi", format_version="3.1.0",
        locator=ApiSourceLocatorV1("openapi.json", "#/components/schemas/Customer"),
        name="Customer",
    ).to_envelope()
    different = SemanticEvidenceEnvelope.from_payload(
        namespace="api_contract", kind="api_schema", version="2", payload=valid.payload(),
    )
    with pytest.raises(ValueError, match="incompatible"):
        ApiEvidenceV1.from_envelope(different)


@dataclass(frozen=True)
class _Registration:
    api_kind: str
    api_versions: tuple[str, ...]
    indexing_contract_id: str
    indexing_contract_version: str


def test_router_is_exact_deterministic_and_refuses_ambiguous_claims() -> None:
    recognized = detect_api_artifact("openapi.json", '{"openapi":"3.1.0"}')
    matching = _Registration("openapi", ("3.1.0",), "oas-parser", "1")
    unrelated = _Registration("swagger", ("2.0",), "swagger-parser", "1")
    assert select_api_interpreter(recognized, (unrelated, matching)) == matching
    assert select_api_interpreter(recognized, (unrelated,)) is None
    with pytest.raises(ValueError, match="ambiguous"):
        select_api_interpreter(recognized, (matching, matching))
    with pytest.raises(ValueError, match="unsafe or unsupported"):
        select_api_interpreter(
            detect_api_artifact("spec.json", '{"openapi":"3.2.0"}'), (matching,),
        )
    assert select_api_interpreter(
        ApiArtifactClassification(None, None, "unrecognized", "no_signature"), (matching,),
    ) is None


def test_status_coverage_preserves_s0_quarantine_and_snapshot_completeness(
    tmp_path: Path,
) -> None:
    (tmp_path / "unknown.json").write_text('{"openapi":"3.1.0"}', encoding="utf-8")
    (tmp_path / "new.json").write_text('{"openapi":"3.2.0"}', encoding="utf-8")
    (tmp_path / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    (tmp_path / "ordinary.json").write_text('{"enabled":true}', encoding="utf-8")
    scan = scan_repository_text(
        tmp_path, include_patterns=("*.json", "*.yaml"),
        exclude_patterns=(), max_files=10,
        max_file_bytes=100_000, max_total_bytes=1_000_000,
    )
    assert [x.relative for x in scan.sources] == ["ordinary.json"]
    counts = scan.coverage.to_mapping()["api_artifact_status_counts"]
    assert counts["supported"] == 1
    assert counts["unsupported"] == 1
    assert counts["quarantined_by_filename"] == 1
    assert all(x["api_artifact_status_counts"] == counts for x in (scan.coverage.to_mapping(),))


def test_unknown_generic_config_remains_unchanged(tmp_path: Path) -> None:
    (tmp_path / "settings.yaml").write_text("enabled: true\n", encoding="utf-8")
    scan = scan_repository_text(
        tmp_path, include_patterns=("*.yaml",), exclude_patterns=(),
        max_files=4, max_file_bytes=1024, max_total_bytes=4096,
    )
    assert len(scan.sources) == 1
    assert "api_artifact_status_counts" not in scan.coverage.to_mapping()



@pytest.mark.parametrize(
    ("format_name", "version"),
    [
        ("openapi", "3.2.0"),
        ("swagger", "3.0"),
        ("postman_collection", "2.0.0"),
        ("postman_v3", "2.1.0"),
    ],
)
def test_typed_api_fact_rejects_unsupported_format_version(
    format_name: str, version: str,
) -> None:
    with pytest.raises(ValueError, match="incompatible"):
        ApiEvidenceV1(
            kind="api_schema",  # type: ignore[arg-type]
            format=format_name,  # type: ignore[arg-type]
            format_version=version,
            locator=ApiSourceLocatorV1("api/spec.json", "#/components/schemas/Name"),
            name="Name",
        )


def test_source_lines_require_strict_integral_span() -> None:
    with pytest.raises(ValueError, match="line_start"):
        ApiSourceLocatorV1("api.json", "#/paths", line_start=True)
    with pytest.raises(ValueError, match="line_end"):
        ApiSourceLocatorV1("api.json", "#/paths", line_start=10, line_end=9)
    with pytest.raises(ValueError, match="line_end requires"):
        ApiSourceLocatorV1("api.json", "#/paths", line_end=10)



def test_enrichment_preserves_canonical_identity_and_provenance(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Verified safe fixture\n", encoding="utf-8")
    result = RepositoryIndexer(RepositoryIndexConfig(repository="test/s1")).index(tmp_path)
    original = result.documents[0]
    fact = ApiEvidenceV1(
        kind="api_operation", format="openapi", format_version="3.1.0",
        locator=ApiSourceLocatorV1(
            original.path, "#/paths/~1customers/post",
            line_start=original.line_start, line_end=original.line_end,
        ),
        method="POST", route="/customers",
    )
    enriched = attach_api_evidence(original, fact)
    assert enriched.id == original.id
    assert enriched.provenance == original.provenance
    assert enriched.content == original.content
    assert enriched.evidence.semantic[0] == fact.to_envelope()
    restored = KnowledgeEvidence.from_mapping(enriched.evidence.to_mapping())
    assert restored == enriched.evidence
    assert ApiEvidenceV1.from_envelope(restored.semantic[0]) == fact
    with pytest.raises(ValueError, match="duplicate"):
        attach_api_evidence(enriched, fact)
    with pytest.raises(ValueError, match="provenance"):
        attach_api_evidence(
            original,
            ApiEvidenceV1(
                kind="api_operation", format="openapi", format_version="3.1.0",
                locator=ApiSourceLocatorV1("other.json", "#/paths/~1customers/post"),
                method="POST", route="/customers",
            ),
        )
