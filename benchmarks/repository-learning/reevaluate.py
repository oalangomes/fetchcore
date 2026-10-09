"""Re-evaluate existing Repository Learning study artifacts with the current recall protocol."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time

from memory import AiMemoryClient
from protocol import (
    DEFAULT_ARMS,
    PROTOCOL,
    ProtocolError,
    _required_mapping,
    file_sha256,
    load_scenario,
    render_evaluation_prompt,
    run_external,
    score_answers,
    sha256_text,
    validate_evaluation_result,
    validate_study_result,
    validate_unique_scopes,
)
from run import (
    _question_ids,
    expand_memory_recall,
    markdown_report,
    memory_recall_metrics,
    score_memory_recall,
    score_stored_memory,
)


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be a JSON object")
    return {str(key): item for key, item in value.items()}


def validate_source_run(
    source: dict[str, object],
    scenario: dict[str, object],
    *,
    scenario_sha256: str,
) -> None:
    if source.get("status") != "complete":
        raise ProtocolError("source run must have status=complete")
    if source.get("scenario_id") != scenario.get("id"):
        raise ProtocolError("source run scenario does not match current scenario")
    source_sha = source.get("scenario_sha256")
    if source_sha is not None and source_sha != scenario_sha256:
        raise ProtocolError("source run scenario fingerprint does not match current scenario")

    source_repository = _required_mapping(source.get("repository"), "source.repository")
    repository = _required_mapping(scenario.get("repository"), "scenario.repository")
    for key in ("identity", "revision"):
        if source_repository.get(key) != repository.get(key):
            raise ProtocolError(f"source run repository {key} does not match current scenario")

    _required_mapping(source.get("arms"), "source.arms")


def resolve_source_identity(
    source: dict[str, object],
    *,
    harness_id: str | None,
    provider_id: str | None,
    model_id: str | None,
) -> tuple[str, str, str, str]:
    source_harness = _required_mapping(source.get("harness"), "source.harness")
    repository = _required_mapping(source.get("repository"), "source.repository")

    resolved_harness = str(source_harness.get("id", "")).strip()
    resolved_provider = str(source_harness.get("provider", "")).strip()
    resolved_model = str(source_harness.get("model", "")).strip()
    resolved_revision = str(repository.get("revision", "")).strip()

    for label, value in (
        ("source.harness.id", resolved_harness),
        ("source.harness.provider", resolved_provider),
        ("source.harness.model", resolved_model),
        ("source.repository.revision", resolved_revision),
    ):
        if not value:
            raise ProtocolError(f"{label} must be a non-empty string")

    for label, override, expected in (
        ("--harness-id", harness_id, resolved_harness),
        ("--provider-id", provider_id, resolved_provider),
        ("--model-id", model_id, resolved_model),
    ):
        if override is not None and override != expected:
            raise ProtocolError(
                f"{label} override {override!r} does not match source run {expected!r}"
            )

    return resolved_harness, resolved_provider, resolved_model, resolved_revision


def resolve_evaluate_command(
    command: str | None,
    *,
    source_harness_id: str,
    source_model_id: str,
    source_revision: str,
    invocation_cwd: Path,
) -> str:
    if command is not None:
        argv = shlex.split(command)
        if not argv:
            raise ProtocolError("--evaluate-command must not be empty")
        resolved: list[str] = []
        for token in argv:
            candidate = invocation_cwd / token
            if (
                not token.startswith("-")
                and not Path(token).is_absolute()
                and ("/" in token or token.endswith(".py"))
                and candidate.exists()
            ):
                resolved.append(str(candidate.resolve()))
            else:
                resolved.append(token)
        return shlex.join(resolved)

    if not source_harness_id.startswith("codex-cli-"):
        raise ProtocolError(
            "--evaluate-command is required when the source harness is not codex-cli"
        )
    wrapper = Path(__file__).resolve().parent / "wrappers" / "codex_harness.py"
    return shlex.join(
        [
            sys.executable,
            str(wrapper),
            "evaluate",
            "--model",
            source_model_id,
            "--revision",
            source_revision,
        ]
    )


def reevaluate(args: argparse.Namespace) -> dict[str, object]:
    scenario = load_scenario(args.scenario)
    scenario_sha = file_sha256(args.scenario)
    source = _load_json(args.source_run, "source run")
    validate_source_run(source, scenario, scenario_sha256=scenario_sha)
    harness_id, provider_id, model_id, source_revision = resolve_source_identity(
        source,
        harness_id=args.harness_id,
        provider_id=args.provider_id,
        model_id=args.model_id,
    )
    evaluate_command = resolve_evaluate_command(
        args.evaluate_command,
        source_harness_id=harness_id,
        source_model_id=model_id,
        source_revision=source_revision,
        invocation_cwd=Path.cwd(),
    )

    source_arms = _required_mapping(source["arms"], "source.arms")
    selected = args.arm or list(DEFAULT_ARMS)
    unknown = sorted(set(selected) - set(DEFAULT_ARMS))
    if unknown:
        raise ProtocolError(f"unsupported arms: {unknown}")
    if len(selected) != len(set(selected)):
        raise ProtocolError("duplicate --arm values")
    missing = sorted(set(selected) - set(source_arms))
    if missing:
        raise ProtocolError(f"source run omitted arms: {missing}")

    scopes = validate_unique_scopes(args.run_id, str(scenario["id"]), selected)
    arms_config = _required_mapping(scenario["arms"], "scenario.arms")
    question_ids = _question_ids(scenario)
    eval_template = args.evaluate_prompt.read_text(encoding="utf-8")
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    memory = AiMemoryClient(args.memory_server_url, args.memory_auth_token)
    init = memory.initialize()
    server_info = init.get("serverInfo", {})
    started_run = time.perf_counter()
    arms_payload: dict[str, object] = {}

    for arm_name in selected:
        arm_started = time.perf_counter()
        project = scopes[arm_name]
        source_arm = _required_mapping(source_arms[arm_name], f"source.arms.{arm_name}")
        arm = _required_mapping(arms_config[arm_name], f"scenario.arms.{arm_name}")
        raw_study = _required_mapping(source_arm.get("study"), f"source.arms.{arm_name}.study")
        study = validate_study_result(raw_study, arm)

        written: list[dict[str, object]] = []
        pages = study["memory_pages"]
        assert isinstance(pages, list)
        for page in pages:
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
            query_result = memory.query(
                args.memory_workspace,
                project,
                str(raw_question["prompt"]),
                args.memory_query_limit,
            )
            recalled[question_id] = expand_memory_recall(
                memory,
                workspace=args.memory_workspace,
                project=project,
                query_result=query_result,
                read_page_limit=args.memory_read_page_limit,
            )

        eval_prompt = render_evaluation_prompt(
            eval_template,
            scenario,
            arm_name=arm_name,
            memory_workspace=args.memory_workspace,
            memory_project_name=project,
            recalled=recalled,
        )
        with tempfile.TemporaryDirectory(prefix="fetchcore-rlb-reeval-") as eval_dir:
            raw_eval, eval_exec = run_external(
                evaluate_command,
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
        answer_score = score_answers(scenario, answers)
        stored_score = score_stored_memory(scenario, study)
        recall_score = score_memory_recall(scenario, recalled)
        arm_payload = {
            "memory_scope": {"workspace": args.memory_workspace, "project": project},
            "tool_policy": arm["tool_policy"],
            "budget": arm.get("budget", {}),
            "study": study,
            "study_reused": True,
            "source_study_execution": source_arm.get("study_execution", {}),
            "source_duration_ms": source_arm.get("duration_ms"),
            "memory_writes": written,
            "memory_recall": recalled,
            "memory_recall_metrics": memory_recall_metrics(recalled),
            "evaluation": {"answers": answers},
            "evaluation_execution": eval_exec,
            "stages": {
                "stored_memory": stored_score,
                "memory_recall": recall_score,
                "answer": answer_score,
            },
            "score": answer_score,
            "duration_ms": round((time.perf_counter() - arm_started) * 1000, 3),
        }
        arms_payload[arm_name] = arm_payload
        (output / f"{arm_name}.json").write_text(
            json.dumps(arm_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    repository = _required_mapping(source["repository"], "source.repository")
    payload: dict[str, object] = {
        "schema_version": source.get("schema_version", 1),
        "protocol": PROTOCOL,
        "status": "complete",
        "mode": "reevaluation",
        "run_id": args.run_id,
        "scenario_id": scenario["id"],
        "scenario_sha256": scenario_sha,
        "repository": repository,
        "source_run": {
            "path": str(args.source_run),
            "run_id": source.get("run_id"),
            "protocol": source.get("protocol"),
            "fetchcore_revision": source.get("fetchcore_revision"),
            "harness": source.get("harness"),
        },
        "harness": {
            "id": harness_id,
            "provider": provider_id,
            "model": model_id,
            "evaluation_command_sha256": sha256_text(evaluate_command),
        },
        "prompt_sha256": {
            "study": source.get("prompt_sha256", {}).get("study")
            if isinstance(source.get("prompt_sha256"), dict)
            else None,
            "evaluate": file_sha256(args.evaluate_prompt),
        },
        "ai_memory": {
            "server_info": server_info,
            "workspace": args.memory_workspace,
            "fresh_server_required": True,
            "recall_strategy": "memory_query_then_memory_read_page",
            "query_limit": args.memory_query_limit,
            "read_page_limit": args.memory_read_page_limit,
        },
        "arms": arms_payload,
        "duration_ms": round((time.perf_counter() - started_run) * 1000, 3),
    }
    (output / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = markdown_report(payload)
    report += (
        "\n> Re-evaluation mode: study/acquisition artifacts were reused from the source "
        "run. Wall time covers memory replay and fresh evaluation only.\n"
    )
    (output / "README.md").write_text(report, encoding="utf-8")
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--arm", action="append", choices=DEFAULT_ARMS, default=[])
    parser.add_argument("--evaluate-command")
    parser.add_argument("--evaluate-prompt", type=Path, default=root / "prompts/evaluate.md")
    parser.add_argument("--memory-server-url", default=os.getenv("AI_MEMORY_SERVER_URL", ""))
    parser.add_argument("--memory-auth-token", default=os.getenv("AI_MEMORY_AUTH_TOKEN"))
    parser.add_argument("--memory-workspace", default="fetchcore-rlb")
    parser.add_argument("--memory-query-limit", type=int, default=8)
    parser.add_argument("--memory-read-page-limit", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--harness-id")
    parser.add_argument("--provider-id")
    parser.add_argument("--model-id")
    args = parser.parse_args(argv)
    if args.memory_query_limit < 1:
        parser.error("--memory-query-limit must be positive")
    if args.memory_read_page_limit < 1:
        parser.error("--memory-read-page-limit must be positive")
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        payload = reevaluate(parse_args(argv))
    except ProtocolError as exc:
        print(f"repository-learning re-evaluation failed: {exc}", file=os.sys.stderr)
        return 2
    print(
        json.dumps(
            {"protocol": PROTOCOL, "status": payload.get("status"), "mode": "reevaluation"},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
