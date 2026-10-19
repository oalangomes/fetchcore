"""Typed benchmark metrics preserve exact-ID and span-budget meanings."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fetchcore.retrieval.contracts import KnowledgeSearchResponse, SearchResult

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "benchmarks/typed-knowledge-retrieval/metrics.py"
spec = importlib.util.spec_from_file_location("typed_knowledge_metrics", MODULE)
assert spec is not None and spec.loader is not None
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def _hit(id: str, path: str, start: int, end: int) -> SearchResult:
    return SearchResult(
        id=id, source=path, source_ref="repository://owner/repo/" + path,
        knowledge_type="documentation", content="evidence", score=1,
        line_start=start, line_end=end, revision="revision",
    )


def test_exact_id_ranking_and_markdown_context_yield() -> None:
    response = KnowledgeSearchResponse(
        backend="test", status="ok",
        results=(_hit("other", "docs/other.md", 1, 3),
                 _hit("expected", "docs/health.md", 16, 21)),
    )
    case = {"id": "health", "kind": "markdown_span", "repository": "owner/repo",
            "path": "docs/health.md", "line_start": 16, "line_end": 21}
    result = metrics.evaluate(response, case, "expected", k=2)
    assert result["source_recall_at_k"] == 1
    assert result["evidence_recall_at_k"] == 1
    assert result["evidence_mrr"] == .5
    assert result["span_coverage_at_k"] == 1
    assert result["context_yield_by_line_budget"]["100"]["context_yield"] == 1
    assert result["useful_evidence_density_hits"] == .5
    assert result["candidates_examined"] is None
    assert result["time_to_first_candidate_ms"] is None


def test_api_pointer_has_no_line_based_context_yield() -> None:
    response = KnowledgeSearchResponse(
        backend="test", status="ok",
        results=(_hit("expected", "docs/openapi.json", 1, 100),),
    )
    case = {"id": "operation", "kind": "api_operation", "repository": "owner/repo",
            "path": "docs/openapi.json"}
    result = metrics.evaluate(response, case, "expected", k=1)
    assert result["evidence_recall_at_k"] == 1
    assert result["span_coverage_at_k"] is None
    assert result["context_yield_by_line_budget"] is None


def test_unapproved_remote_config_stops_before_service_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(MODULE.parent))
    benchmark = MODULE.parent / "run.py"
    runner_spec = importlib.util.spec_from_file_location("typed_knowledge_run", benchmark)
    assert runner_spec is not None and runner_spec.loader is not None
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    config = json.loads((MODULE.parent / "run-config.example.json").read_text())

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("unapproved run inspected or wrote a service")

    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    with pytest.raises(RuntimeError, match="authorization is missing"):
        runner._verify_ownership(config)


def test_embedding_endpoint_preflight_rejects_model_or_dimension_mismatch(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(MODULE.parent))
    benchmark = MODULE.parent / "run.py"
    runner_spec = importlib.util.spec_from_file_location("typed_knowledge_run", benchmark)
    assert runner_spec is not None and runner_spec.loader is not None
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    url = "http://hosted-ollama:11434"
    provider = SimpleNamespace(model_id="embeddinggemma", embed=lambda _: ((0.1, 0.2),))
    monkeypatch.setattr(runner, "_http", lambda *_: {"models": [{"name": "other:latest"}]})
    with pytest.raises(RuntimeError, match="unavailable"):
        runner._verify_embedding_endpoint(url, provider, 2)
    monkeypatch.setattr(runner, "_http", lambda *_: {"models": [{"name": "embeddinggemma:latest"}]})
    with pytest.raises(RuntimeError, match="dimension"):
        runner._verify_embedding_endpoint(url, provider, 3)
    assert runner._verify_embedding_endpoint(url, provider, 2) == {
        "endpoint": url, "model": "embeddinggemma", "dimensions": 2}


def test_targeted_route_analysis_marks_ambiguous_paths_without_discarding_scores(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(MODULE.parent))
    runner_spec = importlib.util.spec_from_file_location(
        "typed_knowledge_run", MODULE.parent / "run.py")
    assert runner_spec is not None and runner_spec.loader is not None
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    documents = (SimpleNamespace(id="a", routes=("/unique", "/shared")),
                 SimpleNamespace(id="b", routes=("/shared",)))
    monkeypatch.setattr(runner, "ProjectionV3Compiler", lambda: SimpleNamespace(
        compile=lambda doc: SimpleNamespace(fields={"route_paths": doc.routes})))
    truth = {"acquisition_cases": [
        {"id": "unique", "kind": "api_operation", "expect": "present", "route": "/unique"},
        {"id": "shared", "kind": "api_request", "expect": "present", "route": "/shared"},
    ]}
    analysis = runner._targeted_route_analysis(documents, truth, {"unique": "a", "shared": "a"})
    assert analysis == {
        "unique": {"candidate_count": 1, "exact_id_score_eligible": True},
        "shared": {"candidate_count": 2, "exact_id_score_eligible": False},
    }
    with pytest.raises(AssertionError, match="does not contain reviewed evidence"):
        runner._targeted_route_analysis(documents, truth, {"unique": "b", "shared": "a"})
