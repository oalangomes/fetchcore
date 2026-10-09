"""Contract + real local-backend smoke coverage; no external services required."""

from pathlib import Path

from fastapi.testclient import TestClient

from fetchcore.application import RepositoryBinding, RetrievalApplicationService
from tools.explorer.api.app import ExplorerContext, create_app


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "eligibility.py").write_text(
        "def eligibility_validator(customer):\n"
        "    return customer.get('active', False)\n",
        encoding="utf-8",
    )
    repo = "example/catalog"
    service = RetrievalApplicationService(
        {repo: RepositoryBinding(repository=repo, workspace=tmp_path, backend="local-semantic")}
    )
    return TestClient(
        create_app(
            ExplorerContext(
                service=service,
                repository=repo,
                backend="local-semantic",
                revision=None,
                capabilities={"lexical": True, "dense": False, "hybrid": False},
                retrieval=None,
            )
        )
    )


def test_real_search_preserves_native_contract_and_source_read(tmp_path: Path) -> None:
    client = _client(tmp_path)
    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["capabilities"]["dense"] is False
    response = client.post(
        "/api/search",
        json={
            "repository": "example/catalog",
            "limit": 10,
            "intent": {"schema_version": 2, "canonical_query": "eligibility_validator"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "local-semantic"
    assert body["results"], body
    assert "telemetry" in body
    hit = next(hit for hit in body["results"] if "eligibility.py" in hit["source_ref"])
    assert "eligibility_validator" in hit["content"]
    assert "source_ref" in hit
    # Optional query matches are omitted when a backend emits none.
    assert isinstance(hit.get("query_matches", []), list)
    source = client.post(
        "/api/source",
        json={"repository": "example/catalog", "source_ref": hit["source_ref"]},
    )
    assert source.status_code == 200, source.text
    assert "eligibility_validator" in source.json()["content"]


def test_unsupported_intent_fails_closed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    result = client.post(
        "/api/search",
        json={"intent": {"schema_version": 2, "canonical_query": "foo", "imaginary": True}},
    )
    assert result.status_code == 422
    assert result.json()["error"]["category"] == "schema_mismatch"


def test_repo_binding_and_source_path_authority(tmp_path: Path) -> None:
    client = _client(tmp_path)
    forbidden = client.post(
        "/api/search",
        json={"repository": "another/repo", "intent": {"schema_version": 2, "canonical_query": "foo"}},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["category"] == "unauthorized_repository"
    traversal = client.post(
        "/api/source",
        json={"source_ref": "../secrets", "repository": "example/catalog"},
    )
    assert traversal.status_code == 403
    assert traversal.json()["error"]["category"] == "unauthorized_source"


def test_request_limits_and_unknown_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for payload in (
        {"intent": {"schema_version": 2, "canonical_query": "foo"}, "limit": 101},
        {"intent": {"schema_version": 2, "canonical_query": "foo"}, "backend": "solr"},
    ):
        assert client.post("/api/search", json=payload).status_code == 422
    assert client.post("/api/source", json={"source_ref": "eligibility.py", "context_lines": 101}).status_code == 422


def test_exact_path_anchor_passes_to_existing_targeted_service(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/search",
        json={
            "intent": {
                "schema_version": 2,
                "canonical_query": "show eligibility",
                "anchors": [{"type": "anchor", "kind": "path", "value": "eligibility.py", "origin": "caller"}],
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["targeted"]["outcome"] == "exact_unique"
