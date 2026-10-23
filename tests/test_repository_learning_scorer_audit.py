from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
STUDY_DIR = ROOT / "studies" / "repository-learning"
sys.path.insert(0, str(STUDY_DIR))


def _load_module(name: str, path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


protocol = _load_module("repository_learning_protocol_audit", STUDY_DIR / "protocol.py")
scorer = _load_module("repository_learning_scorer", STUDY_DIR / "scorer.py")
audit = _load_module("repository_learning_scorer_audit", STUDY_DIR / "scorer_audit.py")

SCENARIO_PATH = STUDY_DIR / "scenarios" / "fetchcore-self.yaml"
RULER_PATH = STUDY_DIR / "scenarios" / "fetchcore-self-ruler-v2.yaml"
ANNOTATIONS_PATH = STUDY_DIR / "scenarios" / "fetchcore-self-v2-audit.yaml"
SOURCE_PATH = (
    STUDY_DIR
    / ".artifacts"
    / "repository-learning"
    / "codex-20261008-002-v2"
    / "run"
    / "run.json"
)


def _inputs() -> tuple[
    dict[str, object], dict[str, object], dict[str, object], dict[str, object]
]:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    scenario = protocol.load_scenario(SCENARIO_PATH)
    ruler = scorer.load_concept_ruler(RULER_PATH)
    annotations = audit._load_mapping(ANNOTATIONS_PATH, "audit annotations")
    return source, scenario, ruler, annotations


def test_original_scorer_baseline_is_reproduced_before_audit() -> None:
    source, scenario, ruler, annotations = _inputs()
    artifact = audit.audit_run(source, scenario, ruler, annotations)

    expected = {
        "direct": {"stored_memory": 5, "memory_recall": 5, "answer": 5},
        "fetchcore": {"stored_memory": 6, "memory_recall": 6, "answer": 5},
        "fetchcore_bounded": {"stored_memory": 5, "memory_recall": 5, "answer": 4},
    }
    arms = artifact["arms"]
    assert isinstance(arms, dict)
    for arm_name, stages in expected.items():
        arm = arms[arm_name]
        assert isinstance(arm, dict)
        for stage, questions_correct in stages.items():
            result = arm[stage]
            assert isinstance(result, dict)
            original = result["original"]
            assert isinstance(original, dict)
            assert original["questions_correct"] == questions_correct


def test_revised_ruler_repairs_only_audited_false_negatives() -> None:
    source, scenario, ruler, annotations = _inputs()
    artifact = audit.audit_run(source, scenario, ruler, annotations)

    cases = artifact["cases"]
    assert isinstance(cases, list)
    assert len(cases) == 4
    assert {case["root_cause"] for case in cases if isinstance(case, dict)} == {
        "ruler false negative"
    }
    for case in cases:
        assert isinstance(case, dict)
        stages = case["stages"]
        assert isinstance(stages, dict)
        answer = stages["answer"]
        assert isinstance(answer, dict)
        assert answer["expected_semantic_verdict"] is True
        assert answer["original_regex_verdict"] is False
        assert answer["original_classification"] == "false_negative"
        assert answer["revised_ruler_verdict"] is True
        assert answer["revised_classification"] == "true_positive"

    arms = artifact["arms"]
    assert isinstance(arms, dict)
    for arm in arms.values():
        assert isinstance(arm, dict)
        for stage in arm.values():
            assert isinstance(stage, dict)
            revised = stage["revised"]
            assert isinstance(revised, dict)
            assert revised["questions_correct"] == 6


def test_revised_ruler_accepts_explicit_equivalence_but_not_partial_concepts() -> None:
    ruler = scorer.load_concept_ruler(RULER_PATH)
    answers = {
        "q-intent-scope": (
            "SearchIntent expresses what the caller means to retrieve. "
            "SearchScope defines where FetchCore may look."
        )
    }
    score = scorer.score_with_concept_ruler(ruler, answers)
    rows = {
        row["question_id"]: row
        for row in score["per_question"]
        if isinstance(row, dict)
    }
    assert rows["q-intent-scope"]["passed"] is True

    partial = scorer.score_with_concept_ruler(
        ruler, {"q-intent-scope": "SearchIntent expresses what the caller means."}
    )
    partial_rows = {
        row["question_id"]: row
        for row in partial["per_question"]
        if isinstance(row, dict)
    }
    assert partial_rows["q-intent-scope"]["passed"] is False
    assert partial_rows["q-intent-scope"]["missing_requirements"] == [
        "scope-bounds-search"
    ]


def test_revised_ruler_remains_fail_closed_for_forbidden_claims() -> None:
    ruler = scorer.load_concept_ruler(RULER_PATH)
    result = scorer.score_with_concept_ruler(
        ruler,
        {
            "q-required-semantics": (
                "Required anchors are rejected when unsupported, but adapters silently "
                "flatten boolean structure."
            )
        },
    )
    rows = {
        row["question_id"]: row
        for row in result["per_question"]
        if isinstance(row, dict)
    }
    assert rows["q-required-semantics"]["passed"] is False
    assert rows["q-required-semantics"]["forbidden_pattern_hits"]


def test_audit_rejects_source_revision_drift() -> None:
    source, scenario, ruler, annotations = _inputs()
    source = dict(source)
    source["repository"] = {
        "identity": "oalangomes/fetchcore",
        "revision": "different",
    }
    with pytest.raises(audit.ProtocolError, match="repository identity/revision"):
        audit.audit_run(source, scenario, ruler, annotations)


def test_audit_generation_preserves_source_identity(tmp_path: Path) -> None:
    output = tmp_path / "audit"
    assert (
        audit.main(
            [
                "--source-run",
                str(SOURCE_PATH),
                "--scenario",
                str(SCENARIO_PATH),
                "--ruler",
                str(RULER_PATH),
                "--annotations",
                str(ANNOTATIONS_PATH),
                "--output",
                str(output),
                "--run-id",
                "audit-test",
            ]
        )
        == 0
    )
    generated = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert generated["source_run"] == {
        "path": str(SOURCE_PATH),
        "sha256": "29a1a3f3f852e0283f9a8037055f747f91456941bda4cc2a71198cec56958c35",
        "run_id": "codex-20261008-002-v2",
        "protocol": "fetchcore.repository-learning-v2",
        "repository": {
            "identity": "oalangomes/fetchcore",
            "revision": "e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c",
        },
    }
