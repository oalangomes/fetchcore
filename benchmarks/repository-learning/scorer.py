"""Versioned deterministic scorers for Repository Learning artifacts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from protocol import ProtocolError, _required_mapping, _required_text

CONCEPT_RULER_SCHEMA_VERSION = 1


def _string_list(value: object, path: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ProtocolError(f"{path} must be a non-empty list of strings")
    return [str(item) for item in value]


def load_concept_ruler(path: Path) -> dict[str, object]:
    """Load the explicit alternatives/conjunctions ruler without changing V1 regexes."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    ruler = _required_mapping(raw, "ruler")
    if ruler.get("schema_version") != CONCEPT_RULER_SCHEMA_VERSION:
        raise ProtocolError(
            f"ruler.schema_version must be {CONCEPT_RULER_SCHEMA_VERSION}"
        )
    _required_text(ruler, "id", "ruler")
    _required_text(ruler, "source_scenario_id", "ruler")
    _required_text(ruler, "source_scenario_sha256", "ruler")
    questions = ruler.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ProtocolError("ruler.questions must be a non-empty list")

    question_ids: set[str] = set()
    for qindex, raw_question in enumerate(questions):
        question = _required_mapping(raw_question, f"ruler.questions[{qindex}]")
        question_id = _required_text(question, "id", f"ruler.questions[{qindex}]")
        if question_id in question_ids:
            raise ProtocolError(f"duplicate ruler question id: {question_id}")
        question_ids.add(question_id)
        _required_text(question, "fact_id", f"ruler.questions[{qindex}]")
        requirements = question.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ProtocolError(f"ruler question {question_id} requires requirements")
        requirement_ids: set[str] = set()
        for rindex, raw_requirement in enumerate(requirements):
            requirement = _required_mapping(
                raw_requirement,
                f"ruler.questions[{qindex}].requirements[{rindex}]",
            )
            requirement_id = _required_text(
                requirement,
                "id",
                f"ruler.questions[{qindex}].requirements[{rindex}]",
            )
            if requirement_id in requirement_ids:
                raise ProtocolError(
                    f"duplicate requirement id {requirement_id!r} for {question_id}"
                )
            requirement_ids.add(requirement_id)
            kind = _required_text(
                requirement,
                "type",
                f"ruler.questions[{qindex}].requirements[{rindex}]",
            )
            if kind == "any_pattern":
                patterns = _string_list(
                    requirement.get("alternatives"),
                    f"ruler.questions[{qindex}].requirements[{rindex}].alternatives",
                )
                for pattern in patterns:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        raise ProtocolError(
                            f"requirement {requirement_id} has invalid regex {pattern!r}"
                        ) from exc
            elif kind == "all_identifiers":
                _string_list(
                    requirement.get("identifiers"),
                    f"ruler.questions[{qindex}].requirements[{rindex}].identifiers",
                )
            else:
                raise ProtocolError(
                    f"requirement {requirement_id} has unsupported type {kind!r}"
                )
        forbidden = question.get("forbidden_patterns", [])
        if not isinstance(forbidden, list) or any(
            not isinstance(pattern, str) for pattern in forbidden
        ):
            raise ProtocolError(
                f"ruler question {question_id} forbidden_patterns must be strings"
            )
        for pattern in forbidden:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ProtocolError(
                    f"ruler question {question_id} has invalid forbidden regex {pattern!r}"
                ) from exc
    return ruler


def _requirement_matches(requirement: dict[str, object], answer: str) -> bool:
    kind = str(requirement["type"])
    if kind == "any_pattern":
        alternatives = requirement["alternatives"]
        assert isinstance(alternatives, list)
        return any(re.search(str(pattern), answer) is not None for pattern in alternatives)
    identifiers = requirement["identifiers"]
    assert isinstance(identifiers, list)
    folded = answer.casefold()
    return all(str(identifier).casefold() in folded for identifier in identifiers)


def score_with_concept_ruler(
    ruler: dict[str, object], answers: dict[str, str]
) -> dict[str, object]:
    """Score explicit required concepts; requirements are ANDed, alternatives are ORed."""
    questions = ruler["questions"]
    assert isinstance(questions, list)
    rows: list[dict[str, object]] = []
    facts: dict[str, list[bool]] = {}
    answered = 0
    correct = 0
    unsupported = 0
    for raw_question in questions:
        assert isinstance(raw_question, dict)
        question_id = str(raw_question["id"])
        fact_id = str(raw_question["fact_id"])
        answer = answers.get(question_id, "")
        if answer.strip():
            answered += 1
        requirements = raw_question["requirements"]
        assert isinstance(requirements, list)
        missing_requirements = [
            str(requirement["id"])
            for requirement in requirements
            if isinstance(requirement, dict)
            and not _requirement_matches(requirement, answer)
        ]
        forbidden = [str(x) for x in raw_question.get("forbidden_patterns", [])]
        forbidden_hits = [
            pattern for pattern in forbidden if re.search(pattern, answer) is not None
        ]
        passed = bool(answer.strip()) and not missing_requirements and not forbidden_hits
        correct += int(passed)
        unsupported += int(bool(answer.strip()) and not passed)
        facts.setdefault(fact_id, []).append(passed)
        rows.append(
            {
                "question_id": question_id,
                "fact_id": fact_id,
                "passed": passed,
                "missing_requirements": missing_requirements,
                "forbidden_pattern_hits": forbidden_hits,
            }
        )
    recovered_facts = sum(1 for outcomes in facts.values() if any(outcomes))
    return {
        "ruler_id": str(ruler["id"]),
        "fact_recall": recovered_facts / len(facts),
        "fact_precision": 0.0 if answered == 0 else correct / answered,
        "questions_correct": correct,
        "questions_total": len(rows),
        "unsupported_or_incorrect_answers": unsupported,
        "per_question": rows,
    }

