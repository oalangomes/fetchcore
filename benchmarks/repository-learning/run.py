"""Repository Learning Benchmark orchestration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from memory import AiMemoryClient
from protocol import (
    DEFAULT_ARMS,
    PROTOCOL,
    SCHEMA_VERSION,
    ProtocolError,
    _required_mapping,
    file_sha256,
    git_value,
    load_scenario,
    render_evaluation_prompt,
    render_study_prompt,
    run_external,
    score_answers,
    sha256_text,
    validate_evaluation_result,
    validate_repository,
    validate_study_result,
    validate_unique_scopes,
)


def markdown_report(payload: dict[str, object]) -> str:
    lines = [
        "# Repository Learning Benchmark",
        "",
        f"- Protocol: `{payload['protocol']}`",
        f"- Scenario: `{payload['scenario_id']}`",
        f"- Repository: `{payload['repository']['identity']}`",
        f"- Revision: `{payload['repository']['revision']}`",
        "",
        (
            "| Arm | FactRecall | FactPrecision | Questions | Memory pages | Tool calls | "
            "Returned chars | Wall ms |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    arms = payload["arms"]
    assert isinstance(arms, dict)
    for name, raw in arms.items():
        assert isinstance(raw, dict)
        score = raw["score"]
        metrics = raw["study"]["metrics"]
        assert isinstance(score, dict) and isinstance(metrics, dict)
        lines.append(
            (
                "| {name} | {recall:.3f} | {precision:.3f} | {correct}/{total} | "
                "{pages} | {calls} | {chars} | {wall:.1f} |"
            ).format(
                name=name,
                recall=float(score["fact_recall"]),
                precision=float(score["fact_precision"]),
                correct=score["questions_correct"],
                total=score["questions_total"],
                pages=len(raw["study"]["memory_pages"]),
                calls=metrics["tool_calls"],
                chars=metrics["returned_chars"],
                wall=float(raw["duration_ms"]),
            )
        )
    lines.extend(
        [
            "",
            (
                "FactRecall/FactPrecision measure the durable-memory evaluation only. "
                "They are not retrieval authority scores."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def parse_arm_commands(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ProtocolError("--study-command must use ARM=COMMAND")
        arm, command = value.split("=", 1)
        arm = arm.strip()
        command = command.strip()
        if not arm or not command:
            raise ProtocolError("--study-command must use non-empty ARM=COMMAND")
        if arm in result:
            raise ProtocolError(f"duplicate study command for arm {arm}")
        result[arm] = command
    return result


def run(args: argparse.Namespace) -> dict[str, object]:
    scenario = load_scenario(args.scenario)
    selected = args.arm or list(DEFAULT_ARMS)
    unknown = sorted(set(selected) - set(DEFAULT_ARMS))
    if unknown:
        raise ProtocolError(f"unsupported arms: {unknown}")
    if len(selected) != len(set(selected)):
        raise ProtocolError("duplicate --arm values")
    scopes = validate_unique_scopes(args.run_id, str(scenario["id"]), selected)
    repository = validate_repository(args.workspace, scenario)
    study_commands = parse_arm_commands(args.study_command)
    missing_commands = sorted(set(selected) - set(study_commands))
    if missing_commands and not args.dry_run:
        raise ProtocolError(f"missing study commands for arms: {missing_commands}")
    if not args.evaluate_command and not args.dry_run:
        raise ProtocolError("--evaluate-command is required")

    study_template = args.study_prompt.read_text(encoding="utf-8")
    eval_template = args.evaluate_prompt.read_text(encoding="utf-8")
    scenario_sha = file_sha256(args.scenario)
    prompts_sha = {
        "study": file_sha256(args.study_prompt),
        "evaluate": file_sha256(args.evaluate_prompt),
    }
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        plan = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "scenario_id": scenario["id"],
            "repository": repository,
            "memory_workspace": args.memory_workspace,
            "memory_projects": scopes,
            "scenario_sha256": scenario_sha,
            "prompt_sha256": prompts_sha,
            "arms": {
                arm: {
                    "study_prompt": render_study_prompt(
                        study_template,
                        scenario,
                        arm_name=arm,
                        memory_workspace=args.memory_workspace,
                        memory_project_name=scopes[arm],
                    )
                }
                for arm in selected
            },
        }
        (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return plan

    memory = AiMemoryClient(args.memory_server_url, args.memory_auth_token)
    init = memory.initialize()
    server_info = init.get("serverInfo", {})
    started_run = time.perf_counter()
    arms_payload: dict[str, object] = {}
    arms_config = _required_mapping(scenario["arms"], "scenario.arms")
    question_ids = {
        str(question["id"])
        for question in scenario["questions"]  # type: ignore[index]
        if isinstance(question, dict)
    }

    for arm_name in selected:
        arm_started = time.perf_counter()
        project = scopes[arm_name]
        arm = _required_mapping(arms_config[arm_name], f"scenario.arms.{arm_name}")
        study_prompt = render_study_prompt(
            study_template,
            scenario,
            arm_name=arm_name,
            memory_workspace=args.memory_workspace,
            memory_project_name=project,
        )
        raw_study, study_exec = run_external(
            study_commands[arm_name],
            study_prompt,
            cwd=args.workspace,
            timeout_seconds=args.timeout_seconds,
            env={
                "FETCHCORE_RLB_ARM": arm_name,
                "FETCHCORE_RLB_MEMORY_WORKSPACE": args.memory_workspace,
                "FETCHCORE_RLB_MEMORY_PROJECT": project,
            },
        )
        study = validate_study_result(raw_study, arm)
        validate_repository(args.workspace, scenario)
        written: list[dict[str, object]] = []
        for page in study["memory_pages"]:  # type: ignore[index]
            assert isinstance(page, dict)
            result = memory.write_page(
                args.memory_workspace,
                project,
                str(page["path"]),
                str(page["body"]),
            )
            written.append({"path": page["path"], "result": result})

        recalled: dict[str, object] = {}
        questions = scenario["questions"]
        assert isinstance(questions, list)
        for raw_question in questions:
            assert isinstance(raw_question, dict)
            question_id = str(raw_question["id"])
            recalled[question_id] = memory.query(
                args.memory_workspace,
                project,
                str(raw_question["prompt"]),
                args.memory_query_limit,
            )

        eval_prompt = render_evaluation_prompt(
            eval_template,
            scenario,
            arm_name=arm_name,
            memory_workspace=args.memory_workspace,
            memory_project_name=project,
            recalled=recalled,
        )
        with tempfile.TemporaryDirectory(prefix="fetchcore-rlb-eval-") as eval_dir:
            raw_eval, eval_exec = run_external(
                args.evaluate_command,
                eval_prompt,
                cwd=Path(eval_dir),
                timeout_seconds=args.timeout_seconds,
                env={
                    "FETCHCORE_RLB_ARM": arm_name,
                    "FETCHCORE_RLB_MEMORY_WORKSPACE": args.memory_workspace,
                    "FETCHCORE_RLB_MEMORY_PROJECT": project,
                },
            )
        answers = validate_evaluation_result(raw_eval, question_ids)
        score = score_answers(scenario, answers)
        arm_payload = {
            "memory_scope": {"workspace": args.memory_workspace, "project": project},
            "tool_policy": arm["tool_policy"],
            "budget": arm.get("budget", {}),
            "study": study,
            "study_execution": study_exec,
            "memory_writes": written,
            "memory_recall": recalled,
            "evaluation": {"answers": answers},
            "evaluation_execution": eval_exec,
            "score": score,
            "duration_ms": round((time.perf_counter() - arm_started) * 1000, 3),
        }
        arms_payload[arm_name] = arm_payload
        (output / f"{arm_name}.json").write_text(
            json.dumps(arm_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "status": "complete",
        "run_id": args.run_id,
        "scenario_id": scenario["id"],
        "scenario_sha256": scenario_sha,
        "prompt_sha256": prompts_sha,
        "repository": repository,
        "fetchcore_revision": git_value(Path(__file__).resolve().parents[2], "rev-parse", "HEAD"),
        "harness": {
            "id": args.harness_id,
            "provider": args.provider_id,
            "model": args.model_id,
            "evaluation_command_sha256": sha256_text(args.evaluate_command),
        },
        "ai_memory": {
            "server_info": server_info,
            "workspace": args.memory_workspace,
            "fresh_server_required": True,
        },
        "arms": arms_payload,
        "duration_ms": round((time.perf_counter() - started_run) * 1000, 3),
    }
    (output / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "README.md").write_text(markdown_report(payload), encoding="utf-8")
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--arm", action="append", choices=DEFAULT_ARMS, default=[])
    parser.add_argument("--study-command", action="append", default=[], metavar="ARM=COMMAND")
    parser.add_argument("--evaluate-command")
    parser.add_argument("--study-prompt", type=Path, default=root / "prompts/study.md")
    parser.add_argument("--evaluate-prompt", type=Path, default=root / "prompts/evaluate.md")
    parser.add_argument("--memory-server-url", default=os.getenv("AI_MEMORY_SERVER_URL", ""))
    parser.add_argument("--memory-auth-token", default=os.getenv("AI_MEMORY_AUTH_TOKEN"))
    parser.add_argument("--memory-workspace", default="fetchcore-rlb")
    parser.add_argument("--memory-query-limit", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--harness-id", required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.memory_query_limit < 1:
        parser.error("--memory-query-limit must be positive")
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        payload = run(parse_args(argv))
    except ProtocolError as exc:
        print(f"repository-learning benchmark failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"protocol": PROTOCOL, "status": payload.get("status", "planned")},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
