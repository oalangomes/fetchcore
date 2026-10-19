"""Run existing S0-S5 extraction gates on synthetic fixtures, without live backends."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TESTS = (
    "tests/unit/repository/test_api_artifact_s0.py",
    "tests/unit/repository/test_api_contracts_s1.py",
    "tests/unit/repository/test_openapi_s2.py",
    "tests/unit/repository/test_postman_s3.py",
    "tests/unit/repository/test_markdown_extraction_s4.py",
    "tests/unit/repository/test_local_references_s5.py",
)
BENCHMARKS = (
    ("s0", "benchmarks/api-extraction-s0/run.py"),
    ("s2", "benchmarks/api-extraction-s2/run.py"),
    ("s3", "benchmarks/api-extraction-s3/run.py"),
    ("s4", "benchmarks/markdown-extraction-s4/run.py"),
    ("s5", "benchmarks/local-reference-s5/run.py"),
)


class GateFailure(RuntimeError):
    """A slice benchmark failed to execute or did not meet its frozen assertions."""


def _require(ok: bool, description: str) -> None:
    if not ok:
        raise GateFailure(description)


def _execute(command: list[str], label: str) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    if result.returncode:
        raise GateFailure(
            f"{label}: exit={result.returncode}; output:\n"
            + (result.stdout + "\n" + result.stderr)[-2500:]
        )
    return result.stdout


def _benchmark(path: str, label: str) -> dict[str, Any]:
    raw = _execute([sys.executable, path], label)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateFailure(f"{label}: expected a single JSON benchmark result") from exc
    _require(isinstance(value, dict), f"{label}: benchmark result must be a JSON object")
    return value


def _validate(label: str, data: dict[str, Any]) -> dict[str, Any]:
    if label == "s0":
        _require(data.get("protocol") == "fetchcore.api-extraction-s0", "S0 protocol")
        _require(data.get("secret_in_canonical") is False, "S0 leaked sentinel to canonical")
        _require(len(data.get("source_access", [])) == 4, "S0 source guard coverage")
        _require(all(r.get("blocked") is True and r.get("secret_exposed") is False
                     for r in data["source_access"]), "S0 source guard")
        _require(all(r.get("secret_in_results") is False
                     for r in data.get("queries", [])), "S0 retrieval sentinel")
        return {
            "canonical_document_count": data["canonical_document_count"],
            "source_access_blocked": 4,
            "sentinel_exposed": False,
            "indexing_fingerprint": data["indexing_fingerprint"],
        }

    if label == "s2":
        _require(data.get("protocol") == "fetchcore.api-extraction-s2", "S2 protocol")
        _require(data.get("mode") == "explicit-opt-in", "S2 opt-in boundary")
        _require(data.get("expected_operations") == 3, "S2 expected operations")
        _require(data.get("recovered_operations") == 3, "S2 operation recovery")
        _require(data.get("additional_operations") == 0, "S2 extra operations")
        _require(data.get("postman_quarantined") is True, "S2 Postman quarantine")
        _require(data.get("secret_sentinel_present") is False, "S2 sentinel leakage")
        _require(data.get("distinct_indexing_fingerprint") is True, "S2 fingerprint")
        return {
            "operations_recovered": 3,
            "additional_operations": 0,
            "sentinel_exposed": False,
            "mode": "explicit-opt-in",
        }

    if label == "s3":
        _require(data.get("protocol") == "fetchcore.api-extraction-s3", "S3 protocol")
        _require(data.get("mode") == "explicit-opt-in", "S3 opt-in boundary")
        _require(data.get("postman_requests_expected") == 2, "S3 expected requests")
        _require(data.get("postman_requests_recovered") == 2, "S3 request recovery")
        _require(data.get("postman_requests_extra") == 0, "S3 extra requests")
        _require(data.get("openapi_operations_preserved") == 3, "S3 OpenAPI preservation")
        _require(data.get("environment_quarantined") is True, "S3 environment quarantine")
        _require(data.get("secret_sentinel_present") is False, "S3 sentinel leakage")
        _require(data.get("changed_indexing_fingerprint") is True, "S3 fingerprint")
        return {
            "postman_requests_recovered": 2,
            "openapi_operations_preserved": 3,
            "sentinel_exposed": False,
            "mode": "explicit-opt-in",
        }

    if label == "s4":
        _require(data.get("protocol") == "fetchcore.markdown-extraction-s4", "S4 protocol")
        _require(data.get("ground_truth_recovered") == 6, "S4 ground-truth recovery")
        _require(data.get("exact_source_locators") == 6, "S4 exact locators")
        for field in (
            "references_with_incorrect_provenance",
            "references_missing_from_retained_content",
            "unjustified_duplicate_reference_observations",
            "code_languages_missing_from_retained_content",
            "observations_from_discarded_regions",
        ):
            _require(data.get(field) == 0, f"S4 evidence violation: {field}")
        _require(
            data.get("maximum_section_evidence_bytes", 1e9)
            <= data.get("section_evidence_bytes_limit", -1),
            "S4 section evidence budget",
        )
        incremental = data.get("incremental", {})
        _require(isinstance(incremental, dict), "S4 incremental diagnostics")
        _require(all(incremental.get(key) == 0
                     for key in ("unchanged_added", "unchanged_updated", "unchanged_removed")),
                 "S4 unchanged reindex was not a no-op")
        return {
            "ground_truth_recovered": 6,
            "exact_source_locators": 6,
            "evidence_provenance_violations": 0,
            "unchanged_reindex_noop": True,
        }

    if label == "s5":
        _require(data.get("ground_truth_positive") == 3, "S5 positive ground truth")
        _require(data.get("ground_truth_recovered") == 3, "S5 positive relations")
        _require(data.get("false_positive_contract_to_code_links") == 0,
                 "S5 false-positive contract/code links")
        _require(data.get("relation_precision") == 1, "S5 link precision")
        _require(data.get("resolution_complete") is True, "S5 default resolution")
        _require(data.get("budget_pressure_omitted", 0) > 0, "S5 budget-pressure diagnostics")
        _require(data.get("deletion_removes_link") is True, "S5 deletion relation removal")
        return {
            "positive_relations_recovered": 3,
            "false_positive_contract_to_code_links": 0,
            "resolution_complete": True,
            "budget_pressure_observed": True,
        }

    raise GateFailure(f"unknown slice {label}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON report destination")
    args = parser.parse_args()

    revision = _execute(["git", "rev-parse", "HEAD"], "git revision").strip()
    dirty = bool(_execute(["git", "status", "--porcelain"], "git status").strip())
    report: dict[str, Any] = {
        "protocol": "fetchcore.typed-extraction-s1-s5-synthetic-gate",
        "schema_version": 1,
        "fetchcore_revision": revision,
        "source_tree_dirty": dirty,
        "scope": "synthetic acquisition/security/construction; no live retrieval backend",
        "status": "FAIL",
        "tests": {},
        "slices": {},
    }
    try:
        _execute([sys.executable, "-m", "pytest", "-q", *TESTS], "S0-S5 contract tests")
        report["tests"] = {"status": "PASS", "files": list(TESTS)}
        for label, script in BENCHMARKS:
            value = _benchmark(script, label)
            summary = _validate(label, value)
            report["slices"][label] = {"status": "PASS", "script": script, "metrics": summary}
            print(f"[s1-s5] {label}=PASS", file=sys.stderr)
        report["status"] = "PASS"
    except GateFailure as exc:
        report["failure"] = str(exc)
        print(f"[s1-s5] FAIL: {exc}", file=sys.stderr)
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"[s1-s5] report={args.output}", file=sys.stderr)
    else:
        print(serialized)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
