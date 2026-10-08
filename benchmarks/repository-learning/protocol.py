#!/usr/bin/env python3
"""Repository Learning Benchmark runner.

External study/evaluator commands remain consumers. This harness owns protocol,
ai-memory isolation, deterministic scoring, provenance, and artifacts only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

import yaml

PROTOCOL = "fetchcore.repository-learning-v1"
SCHEMA_VERSION = 1
DEFAULT_ARMS = ("direct", "fetchcore", "fetchcore_bounded")


class ProtocolError(ValueError):
    """Raised when a scenario or external harness violates the experiment contract."""


def _required_mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ProtocolError(f"{path} must be an object")
    return dict(value)


def _required_text(mapping: dict[str, object], key: str, path: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{path}.{key} must be a non-empty string")
    return value.strip()


def _non_negative_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{path} must be a non-negative integer")
    return value


def load_scenario(path: Path) -> dict[str, object]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = _required_mapping(raw, "scenario")
    if scenario.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError(f"scenario.schema_version must be {SCHEMA_VERSION}")
    _required_text(scenario, "id", "scenario")
    repository = _required_mapping(scenario.get("repository"), "scenario.repository")
    _required_text(repository, "identity", "scenario.repository")
    _required_text(repository, "revision", "scenario.repository")
    study = _required_mapping(scenario.get("study"), "scenario.study")
    _required_text(study, "objective", "scenario.study")

    arms = _required_mapping(scenario.get("arms"), "scenario.arms")
    for name in DEFAULT_ARMS:
        arm = _required_mapping(arms.get(name), f"scenario.arms.{name}")
        _required_text(arm, "tool_policy", f"scenario.arms.{name}")
        budget = arm.get("budget", {})
        if budget is not None:
            budget_map = _required_mapping(budget, f"scenario.arms.{name}.budget")
            for key in ("max_tool_calls", "max_returned_chars"):
                if key in budget_map:
                    _non_negative_int(budget_map[key], f"scenario.arms.{name}.budget.{key}")

    facts = scenario.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ProtocolError("scenario.facts must be a non-empty list")
    fact_ids: set[str] = set()
    for index, raw_fact in enumerate(facts):
        fact = _required_mapping(raw_fact, f"scenario.facts[{index}]")
        fact_id = _required_text(fact, "id", f"scenario.facts[{index}]")
        if fact_id in fact_ids:
            raise ProtocolError(f"duplicate fact id: {fact_id}")
        fact_ids.add(fact_id)
        evidence = fact.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ProtocolError(f"scenario.facts[{index}].evidence must be non-empty")
        for eindex, raw_evidence in enumerate(evidence):
            ev = _required_mapping(raw_evidence, f"scenario.facts[{index}].evidence[{eindex}]")
            _required_text(ev, "path", f"scenario.facts[{index}].evidence[{eindex}]")

    questions = scenario.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ProtocolError("scenario.questions must be a non-empty list")
    question_ids: set[str] = set()
    for index, raw_question in enumerate(questions):
        question = _required_mapping(raw_question, f"scenario.questions[{index}]")
        question_id = _required_text(question, "id", f"scenario.questions[{index}]")
        if question_id in question_ids:
            raise ProtocolError(f"duplicate question id: {question_id}")
        question_ids.add(question_id)
        fact_id = _required_text(question, "fact_id", f"scenario.questions[{index}]")
        if fact_id not in fact_ids:
            raise ProtocolError(f"question {question_id} references unknown fact {fact_id}")
        _required_text(question, "prompt", f"scenario.questions[{index}]")
        required = question.get("required_patterns")
        if not isinstance(required, list) or not required or any(
            not isinstance(item, str) or not item for item in required
        ):
            raise ProtocolError(f"question {question_id} requires non-empty required_patterns")
        forbidden = question.get("forbidden_patterns", [])
        if not isinstance(forbidden, list) or any(not isinstance(item, str) for item in forbidden):
            raise ProtocolError(f"question {question_id} forbidden_patterns must be strings")
        for pattern in [*required, *forbidden]:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ProtocolError(
                    f"question {question_id} has invalid regex {pattern!r}"
                ) from exc
    return scenario


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-._")
    return normalized or "run"


def memory_project(run_id: str, scenario_id: str, arm: str) -> str:
    return _slug(f"rlb-{run_id}-{scenario_id}-{arm}")[:96]


def validate_unique_scopes(run_id: str, scenario_id: str, arms: list[str]) -> dict[str, str]:
    scopes = {arm: memory_project(run_id, scenario_id, arm) for arm in arms}
    if len(scopes) != len(set(scopes.values())):
        raise ProtocolError("derived memory projects are not unique across arms")
    return scopes


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_value(workspace: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(workspace), *args], text=True, stderr=subprocess.STDOUT
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProtocolError(f"git {' '.join(args)} failed for {workspace}: {exc}") from exc


def validate_repository(workspace: Path, scenario: dict[str, object]) -> dict[str, str]:
    repo = _required_mapping(scenario["repository"], "scenario.repository")
    expected = str(repo["revision"])
    actual = git_value(workspace, "rev-parse", "HEAD")
    if actual != expected:
        raise ProtocolError(f"repository revision mismatch: expected {expected}, got {actual}")
    if git_value(workspace, "status", "--porcelain"):
        raise ProtocolError("repository workspace must be clean")
    return {"identity": str(repo["identity"]), "revision": actual}


def render_template(template: str, values: dict[str, str]) -> str:
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ProtocolError(f"prompt template references unknown field {exc.args[0]!r}") from exc


def render_study_prompt(
    template: str,
    scenario: dict[str, object],
    *,
    arm_name: str,
    memory_workspace: str,
    memory_project_name: str,
) -> str:
    study = _required_mapping(scenario["study"], "scenario.study")
    arms = _required_mapping(scenario["arms"], "scenario.arms")
    arm = _required_mapping(arms[arm_name], f"scenario.arms.{arm_name}")
    budget = _required_mapping(arm.get("budget", {}), f"scenario.arms.{arm_name}.budget")
    budget_text = json.dumps(budget, sort_keys=True) if budget else "{}"
    return render_template(
        template,
        {
            "scenario_id": str(scenario["id"]),
            "study_objective": str(study["objective"]),
            "arm": arm_name,
            "tool_policy": str(arm["tool_policy"]),
            "budget_json": budget_text,
            "memory_workspace": memory_workspace,
            "memory_project": memory_project_name,
        },
    )


def render_evaluation_prompt(
    template: str,
    scenario: dict[str, object],
    *,
    arm_name: str,
    memory_workspace: str,
    memory_project_name: str,
    recalled: dict[str, object],
) -> str:
    questions = [
        {"id": q["id"], "prompt": q["prompt"]}
        for q in scenario["questions"]  # type: ignore[index]
        if isinstance(q, dict)
    ]
    return render_template(
        template,
        {
            "scenario_id": str(scenario["id"]),
            "arm": arm_name,
            "memory_workspace": memory_workspace,
            "memory_project": memory_project_name,
            "questions_json": json.dumps(questions, ensure_ascii=False, indent=2),
            "memory_context_json": json.dumps(recalled, ensure_ascii=False, indent=2),
        },
    )


def parse_json_output(stdout: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{label} must emit exactly one JSON object on stdout") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} output must be a JSON object")
    return {str(k): v for k, v in value.items()}


def run_external(
    command: str,
    prompt: str,
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> tuple[dict[str, object], dict[str, object]]:
    argv = shlex.split(command)
    if not argv:
        raise ProtocolError("external command must not be empty")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, **env},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProtocolError(f"external command failed: {exc}") from exc
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    if completed.returncode != 0:
        raise ProtocolError(
            f"external command exited {completed.returncode}: {completed.stderr[-2000:]}"
        )
    payload = parse_json_output(completed.stdout, "external harness")
    execution = {
        "command_sha256": sha256_text(command),
        "duration_ms": duration_ms,
        "stderr_tail": completed.stderr[-2000:],
    }
    return payload, execution


def validate_study_result(
    payload: dict[str, object], arm: dict[str, object]
) -> dict[str, object]:
    if payload.get("status") != "success":
        raise ProtocolError("study result status must be 'success'")
    pages = payload.get("memory_pages")
    if not isinstance(pages, list) or not pages:
        raise ProtocolError("study result must contain memory_pages")
    seen: set[str] = set()
    normalized_pages: list[dict[str, str]] = []
    for index, raw_page in enumerate(pages):
        page = _required_mapping(raw_page, f"study.memory_pages[{index}]")
        path = _required_text(page, "path", f"study.memory_pages[{index}]")
        body = _required_text(page, "body", f"study.memory_pages[{index}]")
        if path in seen:
            raise ProtocolError(f"duplicate memory page path: {path}")
        if path.startswith("/") or ".." in Path(path).parts:
            raise ProtocolError(f"unsafe memory page path: {path}")
        if not body.lstrip().startswith("# "):
            raise ProtocolError(f"memory page {path} must start with an H1")
        seen.add(path)
        normalized_pages.append({"path": path, "body": body})

    metrics = _required_mapping(payload.get("metrics", {}), "study.metrics")
    normalized_metrics: dict[str, int] = {}
    for key in (
        "tool_calls",
        "files_read",
        "evidence_items",
        "returned_chars",
        "input_tokens",
        "output_tokens",
    ):
        normalized_metrics[key] = _non_negative_int(metrics.get(key, 0), f"study.metrics.{key}")

    budget = _required_mapping(arm.get("budget", {}), "arm.budget")
    if (
        "max_tool_calls" in budget
        and normalized_metrics["tool_calls"] > int(budget["max_tool_calls"])
    ):
        raise ProtocolError("study exceeded max_tool_calls budget")
    if "max_returned_chars" in budget and normalized_metrics["returned_chars"] > int(
        budget["max_returned_chars"]
    ):
        raise ProtocolError("study exceeded max_returned_chars budget")

    return {
        "status": "success",
        "memory_pages": normalized_pages,
        "metrics": normalized_metrics,
        "tool_events_verified": payload.get("tool_events_verified") is True,
        "tool_events": payload.get("tool_events", []),
    }


def validate_evaluation_result(
    payload: dict[str, object], question_ids: set[str]
) -> dict[str, str]:
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise ProtocolError("evaluation result must contain answers")
    result: dict[str, str] = {}
    for index, raw_answer in enumerate(answers):
        answer = _required_mapping(raw_answer, f"evaluation.answers[{index}]")
        question_id = _required_text(answer, "question_id", f"evaluation.answers[{index}]")
        text = _required_text(answer, "answer", f"evaluation.answers[{index}]")
        if question_id not in question_ids:
            raise ProtocolError(f"evaluation returned unknown question {question_id}")
        if question_id in result:
            raise ProtocolError(f"evaluation returned duplicate question {question_id}")
        result[question_id] = text
    missing = sorted(question_ids - set(result))
    if missing:
        raise ProtocolError(f"evaluation omitted questions: {missing}")
    return result


def score_answers(scenario: dict[str, object], answers: dict[str, str]) -> dict[str, object]:
    questions = scenario["questions"]
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
        required = [str(x) for x in raw_question["required_patterns"]]
        forbidden = [str(x) for x in raw_question.get("forbidden_patterns", [])]
        missing_patterns = [pattern for pattern in required if re.search(pattern, answer) is None]
        forbidden_hits = [
            pattern for pattern in forbidden if re.search(pattern, answer) is not None
        ]
        passed = bool(answer.strip()) and not missing_patterns and not forbidden_hits
        correct += int(passed)
        unsupported += int(bool(answer.strip()) and not passed)
        facts.setdefault(fact_id, []).append(passed)
        rows.append(
            {
                "question_id": question_id,
                "fact_id": fact_id,
                "passed": passed,
                "missing_required_patterns": missing_patterns,
                "forbidden_pattern_hits": forbidden_hits,
            }
        )
    recovered_facts = sum(1 for outcomes in facts.values() if any(outcomes))
    return {
        "fact_recall": recovered_facts / len(facts),
        "fact_precision": 0.0 if answered == 0 else correct / answered,
        "questions_correct": correct,
        "questions_total": len(rows),
        "unsupported_or_incorrect_answers": unsupported,
        "per_question": rows,
    }
