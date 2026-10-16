"""Contract + real local-backend smoke coverage; no external services required."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from fetchcore.application import RepositoryBinding, RetrievalApplicationService
from tools.explorer.api.app import (
    ExplorerContext, ExplorerTarget, _explorer_runtime_config, _operator_targets, create_app,
)


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


def test_explorer_defaults_to_solr_without_changing_fetchcore_default(monkeypatch, tmp_path: Path) -> None:
    for name in ("FETCHCORE_CONFIG", "FETCHCORE_BACKEND", "FETCHCORE_MCP_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FETCHCORE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("FETCHCORE_REPOSITORY", "demo/catalog")
    monkeypatch.setenv("FETCHCORE_SOLR_COLLECTION", "lab-test")

    from fetchcore.application import load_runtime_config

    assert load_runtime_config().backend == "local-semantic"
    config = _explorer_runtime_config()
    assert config.backend == "solr"
    assert config.backend_settings["collection"] == "lab-test"
    assert config.sources["runtime.backend"] == "environment"


def test_explorer_preserves_explicit_operator_backend(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FETCHCORE_CONFIG", raising=False)
    monkeypatch.delenv("FETCHCORE_MCP_BACKEND", raising=False)
    monkeypatch.setenv("FETCHCORE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("FETCHCORE_BACKEND", "opensearch")
    assert _explorer_runtime_config().backend == "opensearch"

    monkeypatch.setenv("FETCHCORE_BACKEND", "local-semantic")
    assert _explorer_runtime_config().backend == "local-semantic"


def test_explorer_preserves_operator_file_backend(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FETCHCORE_BACKEND", raising=False)
    monkeypatch.delenv("FETCHCORE_MCP_BACKEND", raising=False)
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        "version: 1\nruntime:\n  backend: opensearch\n"
        "  workspace: " + str(tmp_path) + "\n  repository: demo/catalog\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FETCHCORE_CONFIG", str(config_path))
    config = _explorer_runtime_config()
    assert config.backend == "opensearch"
    assert config.sources["runtime.backend"] == "config_file"


def test_default_solr_never_silently_falls_back_on_failure(monkeypatch, tmp_path: Path) -> None:
    from tools.explorer.api.app import _runtime, app

    monkeypatch.delenv("FETCHCORE_CONFIG", raising=False)
    monkeypatch.delenv("FETCHCORE_BACKEND", raising=False)
    monkeypatch.delenv("FETCHCORE_MCP_BACKEND", raising=False)
    monkeypatch.setenv("FETCHCORE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("FETCHCORE_REPOSITORY", "demo/catalog")
    monkeypatch.setenv("FETCHCORE_SOLR_URL", "http://127.0.0.1:1/solr")
    monkeypatch.setenv("FETCHCORE_SOLR_COLLECTION", "unavailable")
    _runtime.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/api/config").json()["backend"] == "solr"
            search_response = client.post(
                "/api/search",
                json={"intent": {"schema_version": 2, "canonical_query": "needle"}},
            )
            assert search_response.status_code != 200
            assert search_response.json().get("backend") != "local-semantic"
    finally:
        _runtime.cache_clear()


def test_authorized_target_selection_binds_collection_and_repository(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "alpha.py").write_text("def alpha_evidence(): return True", encoding="utf-8")
    (second / "beta.py").write_text("def beta_evidence(): return True", encoding="utf-8")
    service_a = RetrievalApplicationService({
        "alpha/repo": RepositoryBinding(repository="alpha/repo", workspace=first, backend="local-semantic"),
    })
    service_b = RetrievalApplicationService({
        "beta/repo": RepositoryBinding(repository="beta/repo", workspace=second, backend="local-semantic"),
    })
    caps = {"lexical": True, "dense": False, "hybrid": False}
    ctx = ExplorerContext(
        service=service_a, repository="alpha/repo", backend="local-semantic", revision=None,
        capabilities=caps, retrieval=None,
        targets={
            "default": ExplorerTarget("default", service_a, "alpha/repo", "local-semantic", None, "main", caps, None),
            "alternate": ExplorerTarget("alternate", service_a, "alpha/repo", "local-semantic", None, "alt", caps, None),
            "other-repo": ExplorerTarget("other-repo", service_b, "beta/repo", "local-semantic", None, "beta", caps, None),
        },
    )
    client = TestClient(create_app(ctx))
    public = client.get("/api/config").json()
    assert [item["id"] for item in public["targets"]] == ["default", "alternate", "other-repo"]
    assert all("workspace" not in item and "backend_settings" not in item for item in public["targets"])
    result = client.post("/api/search", json={
        "target_id": "other-repo", "repository": "beta/repo",
        "intent": {"schema_version": 2, "canonical_query": "beta_evidence"},
    })
    assert result.status_code == 200, result.text
    hit = result.json()["results"][0]
    assert "beta.py" in hit["source_ref"]
    source = client.post("/api/source", json={
        "target_id": "other-repo", "repository": "beta/repo", "source_ref": hit["source_ref"],
    })
    assert source.status_code == 200
    assert "beta_evidence" in source.json()["content"]
    forbidden = client.post("/api/search", json={
        "target_id": "other-repo", "repository": "alpha/repo",
        "intent": {"schema_version": 2, "canonical_query": "alpha_evidence"},
    })
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["category"] == "unauthorized_repository"
    missing = client.post("/api/search", json={
        "target_id": "unlisted", "intent": {"schema_version": 2, "canonical_query": "beta"},
    })
    assert missing.status_code == 403
    assert missing.json()["error"]["category"] == "unauthorized_target"


def test_target_registry_is_operator_owned_and_fails_closed(monkeypatch, tmp_path: Path) -> None:
    from fetchcore.application import FetchCoreRuntimeConfig

    base = FetchCoreRuntimeConfig(
        backend="local-semantic", workspace=tmp_path, repository="demo/default",
    )
    monkeypatch.setenv("FETCHCORE_ENVIRONMENT", "dev")
    monkeypatch.setenv("FETCHCORE_EXPLORER_TARGETS", '{"id":"wrong-shape"}')
    try:
        _operator_targets(base)
        assert False, "a non-array target config must fail"
    except ValueError as exc:
        assert "array" in str(exc)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("FETCHCORE_EXPLORER_TARGETS", (
        '[{"id":"allowed","backend":"local-semantic",'
        '"backend_settings":{},"repository":"demo/allowed","workspace":"' + str(allowed) + '"}]'
    ))
    targets = _operator_targets(base)
    assert set(targets) == {"default", "allowed"}
    assert targets["allowed"].repository == "demo/allowed"
    assert targets["allowed"].public()["collection"] is None
    assert targets["allowed"].public()["environment"] == "dev"
    assert targets["allowed"].public()["indexing"]["supported"] is False
    monkeypatch.setenv("FETCHCORE_EXPLORER_TARGETS", (
        '[{"id":"default","backend":"local-semantic",'
        '"backend_settings":{},"repository":"demo/allowed","workspace":"' + str(allowed) + '"}]'
    ))
    try:
        _operator_targets(base)
        assert False, "duplicate default target id must fail"
    except ValueError as exc:
        assert "duplicated" in str(exc)
    monkeypatch.setenv("FETCHCORE_EXPLORER_TARGETS", (
        '[{"id":"allowed","backend":"local-semantic",'
        '"backend_settings":{},"repository":"demo/allowed","workspace":"' + str(allowed)
        + '","environment":"production"}]'
    ))
    with pytest.raises(ValueError, match="environment"):
        _operator_targets(base)


def test_readiness_is_on_demand_and_uses_authorized_target(tmp_path: Path) -> None:
    from fetchcore.application import FetchCoreRuntimeConfig
    from fetchcore.application.runtime_inspection import RuntimeInspectionService

    repo = "demo/catalog"
    service = RetrievalApplicationService({
        repo: RepositoryBinding(repository=repo, workspace=tmp_path, backend="local-semantic"),
    })
    config = FetchCoreRuntimeConfig(
        backend="local-semantic", workspace=tmp_path, repository=repo,
    )
    target = ExplorerTarget(
        "default", service, repo, "local-semantic", None, None,
        {"lexical": True}, None, runtime_config=config,
    )
    ctx = ExplorerContext(
        service=service, repository=repo, backend="local-semantic",
        revision=None, capabilities={"lexical": True}, retrieval=None,
        targets={"default": target},
    )
    client = TestClient(create_app(ctx))
    public = client.get("/api/config").json()
    assert "runtime_config" not in public["targets"][0]
    invalid = client.get("/api/readiness?target_id=unconfigured")
    assert invalid.status_code == 403
    assert invalid.json()["error"]["category"] == "unauthorized_target"
    response = client.get("/api/readiness?target_id=default")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["schema_version"] == 1
    assert report["ready"] is True
    assert report["checks"]["snapshot"]["state"] == "not_required"
    assert RuntimeInspectionService(config).readiness() == report


def test_api_is_offline_atomic_and_rejects_duplicate_runs(monkeypatch):
    import json
    def forbidden(*a, **kw):
        raise AssertionError('Artifact import must not resolve any runtime')
    monkeypatch.setattr('tools.explorer.api.app._runtime', forbidden)
    client = TestClient(create_app())
    raw = (Path(__file__).resolve().parents[1] / 'fixtures/index-runs/incremental.json').read_text()
    result = client.post('/api/indexing/artifacts', json={'artifacts': [raw]})
    assert result.status_code == 200
    assert result.json()['artifacts'][0] == json.loads(raw)
    assert client.post('/api/indexing/artifacts', json={'artifacts': [raw, raw]}).status_code == 422
    assert client.post('/api/indexing/artifacts', json={'artifacts': [raw, '{}']}).status_code == 422
    assert client.post('/api/indexing/artifacts', json={'artifacts': []}).status_code == 422
    assert client.post('/api/indexing/artifacts', json={'artifacts': [raw] * 3}).status_code == 422
