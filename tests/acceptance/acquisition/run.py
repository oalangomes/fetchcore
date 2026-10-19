"""Run deterministic acquisition acceptance checks without live backends."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
TESTS = (
    "tests/unit/repository/test_api_artifact_safety.py",
    "tests/unit/repository/test_api_contracts.py",
    "tests/unit/repository/test_openapi_extraction.py",
    "tests/unit/repository/test_postman_extraction.py",
    "tests/unit/repository/test_markdown_evidence.py",
    "tests/unit/repository/test_local_relations.py",
)
CHECKS = (
    ("api_safety", "tests/acceptance/acquisition/api-safety/run.py"),
    ("openapi", "tests/acceptance/acquisition/openapi/run.py"),
    ("postman", "tests/acceptance/acquisition/postman/run.py"),
    ("markdown", "tests/acceptance/acquisition/markdown/run.py"),
    ("local_relations", "tests/acceptance/acquisition/local-relations/run.py"),
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


def _fixture_check(path: str, label: str) -> dict[str, Any]:
    raw = _execute([sys.executable, path], label)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateFailure(f"{label}: expected a single JSON acceptance result") from exc
    _require(isinstance(value, dict), f"{label}: acceptance result must be a JSON object")
    return value


def _validate(label: str, data: dict[str, Any]) -> dict[str, Any]:
    if label == "api_safety":
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

    if label == "openapi":
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

    if label == "postman":
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

    if label == "markdown":
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

    if label == "local_relations":
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

    raise GateFailure(f"unknown acquisition check {label}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON report destination")
    parser.add_argument("--corpus", type=Path,
                        help="Pinned real repository checkout for optional canonical validation")
    args = parser.parse_args()

    revision = _execute(["git", "rev-parse", "HEAD"], "git revision").strip()
    dirty = bool(_execute(["git", "status", "--porcelain"], "git status").strip())
    report: dict[str, Any] = {
        "protocol": "fetchcore.acquisition-acceptance-v1",
        "schema_version": 1,
        "fetchcore_revision": revision,
        "source_tree_dirty": dirty,
        "scope": "synthetic acquisition/security/construction; no live retrieval backend",
        "status": "FAIL",
        "tests": {},
        "checks": {},
    }
    try:
        _execute([sys.executable, "-m", "pytest", "-q", *TESTS], "acquisition contract tests")
        report["tests"] = {"status": "PASS", "files": list(TESTS)}
        for label, script in CHECKS:
            value = _fixture_check(script, label)
            summary = _validate(label, value)
            report["checks"][label] = {"status": "PASS", "script": script, "metrics": summary}
            print(f"[acquisition] {label}=PASS", file=sys.stderr)
        if args.corpus is not None:
            value = json.loads(_execute(
                [sys.executable, "tests/acceptance/acquisition/typed_knowledge.py",
                 "--corpus", str(args.corpus)], "typed_knowledge",
            ))
            _require(value.get("status") == "PASS", "real canonical validation failed")
            report["checks"]["typed_knowledge"] = value
        report["status"] = "PASS"
    except GateFailure as exc:
        report["failure"] = str(exc)
        print(f"[acquisition] FAIL: {exc}", file=sys.stderr)
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"[acquisition] report={args.output}", file=sys.stderr)
    else:
        print(serialized)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
