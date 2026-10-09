"""Audit an existing Repository Learning run without reacquisition or evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import yaml

from protocol import ProtocolError, file_sha256, load_scenario, score_answers
from scorer import load_concept_ruler, score_with_concept_ruler

AUDIT_PROTOCOL = "fetchcore.repository-learning-scorer-audit-v1"
STAGES = ("stored_memory", "memory_recall", "answer")


def classify_verdict(*, expected: bool, actual: bool) -> str:
    if expected and actual:
        return "true_positive"
    if expected:
        return "false_negative"
    if actual:
        return "false_positive"
    return "true_negative"


def _load_mapping(path: Path, label: str) -> dict[str, object]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProtocolError(f"{label} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ProtocolError(f"{label} must be an object")
    return dict(raw)


def _question_ids(scenario: dict[str, object]) -> list[str]:
    questions = scenario["questions"]
    assert isinstance(questions, list)
    return [str(question["id"]) for question in questions if isinstance(question, dict)]


def _stored_text(arm: dict[str, object]) -> str:
    study = arm["study"]
    assert isinstance(study, dict)
    pages = study["memory_pages"]
    assert isinstance(pages, list)
    return "\n\n".join(
        str(page["body"])
        for page in pages
        if isinstance(page, dict) and isinstance(page.get("body"), str)
    )


def _stage_answers(
    arm: dict[str, object], question_ids: list[str]
) -> dict[str, dict[str, str]]:
    recalled = arm["memory_recall"]
    evaluation = arm["evaluation"]
    assert isinstance(recalled, dict)
    assert isinstance(evaluation, dict)
    final_answers = evaluation["answers"]
    assert isinstance(final_answers, dict)
    stored = _stored_text(arm)
    return {
        "stored_memory": {question_id: stored for question_id in question_ids},
        "memory_recall": {
            question_id: json.dumps(
                recalled.get(question_id, {}), ensure_ascii=False, sort_keys=True
            )
            for question_id in question_ids
        },
        "answer": {
            question_id: str(final_answers.get(question_id, ""))
            for question_id in question_ids
        },
    }


def _per_question(score: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = score["per_question"]
    assert isinstance(rows, list)
    return {
        str(row["question_id"]): row
        for row in rows
        if isinstance(row, dict)
    }


def _annotations_by_case(annotations: dict[str, object]) -> dict[tuple[str, str], dict[str, object]]:
    cases = annotations.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProtocolError("audit annotations require non-empty cases")
    result: dict[tuple[str, str], dict[str, object]] = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ProtocolError(f"audit annotations case {index} must be an object")
        arm = case.get("arm")
        question_id = case.get("question_id")
        expected = case.get("expected_semantic")
        if not isinstance(arm, str) or not isinstance(question_id, str):
            raise ProtocolError(f"audit annotations case {index} requires arm/question_id")
        if not isinstance(expected, dict) or set(expected) != set(STAGES) or any(
            not isinstance(value, bool) for value in expected.values()
        ):
            raise ProtocolError(
                f"audit annotations case {arm}/{question_id} requires boolean stage verdicts"
            )
        key = (arm, question_id)
        if key in result:
            raise ProtocolError(f"duplicate audit annotations case {arm}/{question_id}")
        result[key] = dict(case)
    return result


def _root_cause(expected: dict[str, bool], original_answer_passed: bool) -> str:
    if not expected["stored_memory"]:
        return "acquisition/learning loss"
    if not expected["memory_recall"]:
        return "memory retrieval loss"
    if not expected["answer"]:
        return "evaluator omission"
    if not original_answer_passed:
        return "ruler false negative"
    return "actual incorrect answer"


def audit_run(
    source: dict[str, object],
    scenario: dict[str, object],
    ruler: dict[str, object],
    annotations: dict[str, object],
) -> dict[str, object]:
    if source.get("status") != "complete" or source.get("protocol") != "fetchcore.repository-learning-v2":
        raise ProtocolError("scorer audit source must be a complete protocol v2 run")
    if source.get("scenario_id") != scenario.get("id"):
        raise ProtocolError("scorer audit source scenario mismatch")
    if source.get("scenario_sha256") != ruler.get("source_scenario_sha256"):
        raise ProtocolError("scorer audit source scenario checksum mismatch")
    if source.get("repository") != scenario.get("repository"):
        raise ProtocolError("scorer audit source repository identity/revision mismatch")
    if ruler.get("source_scenario_id") != scenario.get("id"):
        raise ProtocolError("concept ruler scenario mismatch")
    annotation_cases = _annotations_by_case(annotations)
    question_ids = _question_ids(scenario)
    arms = source.get("arms")
    if not isinstance(arms, dict):
        raise ProtocolError("scorer audit source must contain arms")

    arm_results: dict[str, object] = {}
    all_original_failures: set[tuple[str, str]] = set()
    stage_cache: dict[str, dict[str, dict[str, object]]] = {}
    text_cache: dict[str, dict[str, dict[str, str]]] = {}
    for arm_name, raw_arm in arms.items():
        if not isinstance(arm_name, str) or not isinstance(raw_arm, dict):
            raise ProtocolError("scorer audit source arm is invalid")
        texts = _stage_answers(raw_arm, question_ids)
        text_cache[arm_name] = texts
        scores: dict[str, dict[str, object]] = {}
        for stage in STAGES:
            original = score_answers(scenario, texts[stage])
            revised = score_with_concept_ruler(ruler, texts[stage])
            artifact_stages = raw_arm.get("stages")
            if not isinstance(artifact_stages, dict) or original != artifact_stages.get(stage):
                raise ProtocolError(
                    f"source artifact original score drift for {arm_name}/{stage}"
                )
            scores[stage] = {"original": original, "revised": revised}
            for question_id, row in _per_question(original).items():
                if not bool(row["passed"]):
                    all_original_failures.add((arm_name, question_id))
        stage_cache[arm_name] = scores
        arm_results[arm_name] = scores

    if set(annotation_cases) != all_original_failures:
        missing = sorted(all_original_failures - set(annotation_cases))
        extra = sorted(set(annotation_cases) - all_original_failures)
        raise ProtocolError(
            f"audit annotations must cover every original stage failure; missing={missing}, extra={extra}"
        )

    case_results: list[dict[str, object]] = []
    for (arm_name, question_id), annotation in annotation_cases.items():
        expected_raw = annotation["expected_semantic"]
        assert isinstance(expected_raw, dict)
        expected = {stage: bool(expected_raw[stage]) for stage in STAGES}
        stages: dict[str, object] = {}
        for stage in STAGES:
            scores = stage_cache[arm_name][stage]
            original = _per_question(scores["original"])[question_id]
            revised = _per_question(scores["revised"])[question_id]
            text = text_cache[arm_name][stage][question_id]
            stages[stage] = {
                "expected_semantic_verdict": expected[stage],
                "original_regex_verdict": bool(original["passed"]),
                "original_classification": classify_verdict(
                    expected=expected[stage], actual=bool(original["passed"])
                ),
                "revised_ruler_verdict": bool(revised["passed"]),
                "revised_classification": classify_verdict(
                    expected=expected[stage], actual=bool(revised["passed"])
                ),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            if stage == "answer":
                stages[stage]["known_answer_text"] = text  # type: ignore[index]
        original_answer = stages["answer"]
        assert isinstance(original_answer, dict)
        case_results.append(
            {
                "arm": arm_name,
                "question_id": question_id,
                "root_cause": _root_cause(
                    expected, bool(original_answer["original_regex_verdict"])
                ),
                "rationale": str(annotation.get("rationale", "")),
                "stages": stages,
            }
        )

    return {
        "protocol": AUDIT_PROTOCOL,
        "status": "complete",
        "original_scorer": "scenario-required-regex-v1",
        "revised_scorer": str(ruler["id"]),
        "arms": arm_results,
        "cases": case_results,
    }


def markdown_report(artifact: dict[str, object]) -> str:
    lines = [
        "# Repository Learning Scorer Audit",
        "",
        f"- Protocol: `{artifact['protocol']}`",
        f"- Source run: `{artifact['source_run']['run_id']}`",  # type: ignore[index]
        f"- Original scorer: `{artifact['original_scorer']}`",
        f"- Revised scorer: `{artifact['revised_scorer']}`",
        "",
        "| Arm | Question | Stored | Recalled | Answer | Original ruler | Root cause |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    cases = artifact["cases"]
    assert isinstance(cases, list)
    for case in cases:
        assert isinstance(case, dict)
        stages = case["stages"]
        assert isinstance(stages, dict)
        verdicts = []
        for stage in STAGES:
            value = stages[stage]
            assert isinstance(value, dict)
            verdicts.append("present" if value["expected_semantic_verdict"] else "absent")
        answer = stages["answer"]
        assert isinstance(answer, dict)
        original = "PASS" if answer["original_regex_verdict"] else "FAIL"
        lines.append(
            f"| {case['arm']} | {case['question_id']} | {verdicts[0]} | "
            f"{verdicts[1]} | {verdicts[2]} | {original} | {case['root_cause']} |"
        )
    lines.extend(
        [
            "",
            "Semantic verdicts are frozen human-reviewed audit inputs. The harness",
            "deterministically verifies the source artifact, reproduces the original",
            "regex results, classifies confusion outcomes, and applies the separate",
            "versioned ruler. It does not use an LLM judge and does not rewrite history.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--ruler", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    source = json.loads(args.source_run.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ProtocolError("source run must be an object")
    scenario = load_scenario(args.scenario)
    ruler = load_concept_ruler(args.ruler)
    annotations = _load_mapping(args.annotations, "audit annotations")
    expected_source_sha = annotations.get("source_run_sha256")
    actual_source_sha = file_sha256(args.source_run)
    if expected_source_sha != actual_source_sha:
        raise ProtocolError(
            f"audit source checksum mismatch: expected {expected_source_sha}, got {actual_source_sha}"
        )
    if ruler.get("source_scenario_sha256") != file_sha256(args.scenario):
        raise ProtocolError("concept ruler source scenario checksum mismatch")

    artifact = audit_run(source, scenario, ruler, annotations)
    artifact.update(
        {
            "run_id": args.run_id,
            "mode": "scorer_audit",
            "source_run": {
                "path": args.source_run.as_posix(),
                "sha256": actual_source_sha,
                "run_id": source.get("run_id"),
                "protocol": source.get("protocol"),
                "repository": source.get("repository"),
            },
            "scenario": {
                "path": args.scenario.as_posix(),
                "sha256": file_sha256(args.scenario),
            },
            "ruler": {
                "path": args.ruler.as_posix(),
                "sha256": file_sha256(args.ruler),
            },
            "annotations": {
                "path": args.annotations.as_posix(),
                "sha256": file_sha256(args.annotations),
            },
        }
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "run.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output / "README.md").write_text(markdown_report(artifact), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProtocolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

