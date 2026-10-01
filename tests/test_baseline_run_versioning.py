from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "benchmarks" / "baseline-v1" / "run.py"
SPEC = importlib.util.spec_from_file_location("fetchcore_baseline_v1_run", RUN_PATH)
assert SPEC is not None
assert SPEC.loader is not None
RUN_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN_MODULE)


def test_allocate_run_output_dir_advances_monotonically(tmp_path: Path) -> None:
    (tmp_path / "V0.3").mkdir()
    (tmp_path / "V0.invalid").mkdir()
    (tmp_path / "legacy.json").write_text("{}", encoding="utf-8")

    version, output = RUN_MODULE._allocate_run_output_dir(tmp_path)

    assert version == "V0.4"
    assert output == tmp_path / "V0.4"
    assert output.is_dir()

    next_version, next_output = RUN_MODULE._allocate_run_output_dir(tmp_path)

    assert next_version == "V0.5"
    assert next_output == tmp_path / "V0.5"
    assert next_output.is_dir()


def test_write_metrics_report_includes_retrieval_and_indexing_metrics(
    tmp_path: Path,
) -> None:
    run_manifest = {
        "run_version": "V0.4",
        "protocol": "fetchcore.baseline-v1",
        "status": "complete",
        "completed_at": "2026-10-01T18:00:00+00:00",
        "fetchcore_revision": "fetchcore-sha",
        "corpus": {
            "repository": "owner/repository",
            "revision": "corpus-sha",
        },
    }
    retrieval_artifacts = (
        {
            "arm": {"id": "solr-dense"},
            "aggregate": {
                "run_count": 400,
                "query_count": 20,
                "source_recall_at_k_mean": 0.9,
                "evidence_recall_at_k_mean": 0.816667,
                "span_coverage_at_k_mean": 0.848254,
                "evidence_mrr_mean": 0.746212,
                "evidence_ndcg_at_k_mean": 0.759794,
                "useful_context_line_ratio_mean": 0.046348,
                "evidence_ground_truth_recovered": 700,
                "duration_ms": {"p50": 33, "p95": 46, "p99": 54},
                "candidates_examined_mean": 80.0,
                "candidates_returned_mean": 20.0,
                "returned_chars_mean": 1000.5,
                "returned_line_count_mean": 120.25,
            },
        },
    )
    indexing_payload = {
        "canonical": {
            "files_discovered": 1189,
            "files_indexed": 894,
            "chunks_total": 9620,
            "duration_ms": 12359,
            "indexing_throughput_chunks_per_second": 778.38,
        },
        "backends": {
            "solr": {
                "mode": "full_rebuild",
                "duration_ms": 770005,
                "telemetry": {
                    "backend_writes": 9620,
                    "backend_deletes": 9620,
                    "embedding_duration_ms": 305202,
                    "embedding_vectors": 9620,
                    "embedding_throughput_vectors_per_second": 31.52,
                },
            }
        },
    }

    destination = RUN_MODULE._write_metrics_report(
        tmp_path,
        run_manifest=run_manifest,
        retrieval_artifacts=retrieval_artifacts,
        indexing_payload=indexing_payload,
    )

    assert destination == (tmp_path / "README.md").resolve()
    report = destination.read_text(encoding="utf-8")
    assert "# Baseline V0.4 metrics" in report
    assert "[solr-dense](solr-dense.json)" in report
    assert "0.816667" in report
    assert "33 ms | 46 ms | 54 ms" in report
    assert "| Arm | Observations | Queries |" in report
    assert "Files indexed: 894" in report
    assert "solr | full_rebuild | 770005 ms" in report
    assert "[run.json](run.json)" in report


def test_metrics_report_marks_indexing_as_not_requested() -> None:
    report = RUN_MODULE._render_metrics_report(
        run_manifest={
            "run_version": "V0.2",
            "protocol": "fetchcore.baseline-v1",
            "status": "complete",
            "completed_at": "2026-10-01T18:00:00+00:00",
            "fetchcore_revision": "fetchcore-sha",
            "corpus": {
                "repository": "owner/repository",
                "revision": "corpus-sha",
            },
        },
        retrieval_artifacts=(),
        indexing_payload=None,
    )

    assert "Index preparation was not requested for this run." in report
    assert "indexing.json" not in report
