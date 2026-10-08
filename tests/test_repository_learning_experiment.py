from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


RUNNER = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "repository-learning"
    / "run.py"
)
sys.path.insert(0, str(RUNNER.parent))
SPEC = importlib.util.spec_from_file_location("repository_learning_run", RUNNER)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

import protocol as protocol_module


def minimal_scenario() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "demo",
        "repository": {"identity": "owner/repo", "revision": "abc"},
        "study": {"objective": "Learn the repository boundary."},
        "arms": {
            "direct": {"tool_policy": "repo tools", "budget": {}},
            "fetchcore": {"tool_policy": "FetchCore only", "budget": {}},
            "fetchcore_bounded": {
                "tool_policy": "FetchCore only with budget",
                "budget": {"max_tool_calls": 2, "max_returned_chars": 100},
            },
        },
        "facts": [{"id": "boundary", "evidence": [{"path": "README.md"}]}],
        "questions": [
            {
                "id": "q1",
                "fact_id": "boundary",
                "prompt": "What is canonical?",
                "required_patterns": ["(?i)canonical"],
                "forbidden_patterns": ["(?i)vector score is authority"],
            }
        ],
    }


def test_memory_scope_is_unique_per_arm() -> None:
    scopes = module.validate_unique_scopes(
        "run-1", "demo", ["direct", "fetchcore", "fetchcore_bounded"]
    )
    assert len(set(scopes.values())) == 3
    assert all(value.startswith("rlb-run-1-demo-") for value in scopes.values())


def test_score_answers_is_deterministic() -> None:
    scenario = minimal_scenario()
    score = module.score_answers(scenario, {"q1": "Canonical Repository Knowledge is canonical."})
    assert score["fact_recall"] == 1.0
    assert score["fact_precision"] == 1.0
    assert score["unsupported_or_incorrect_answers"] == 0

    failed = module.score_answers(scenario, {"q1": "A vector score is authority."})
    assert failed["fact_recall"] == 0.0
    assert failed["fact_precision"] == 0.0
    assert failed["unsupported_or_incorrect_answers"] == 1


def test_study_result_enforces_bounded_arm_budget() -> None:
    arm = minimal_scenario()["arms"]["fetchcore_bounded"]
    assert isinstance(arm, dict)
    payload = {
        "status": "success",
        "memory_pages": [{"path": "knowledge/x.md", "body": "# X\n\nFact"}],
        "metrics": {"tool_calls": 3, "returned_chars": 90},
    }
    with pytest.raises(module.ProtocolError, match="max_tool_calls"):
        module.validate_study_result(payload, arm)


def test_study_result_rejects_unsafe_memory_path() -> None:
    arm = minimal_scenario()["arms"]["direct"]
    assert isinstance(arm, dict)
    payload = {
        "status": "success",
        "memory_pages": [{"path": "../escape.md", "body": "# X\n\nFact"}],
        "metrics": {},
    }
    with pytest.raises(module.ProtocolError, match="unsafe memory page path"):
        module.validate_study_result(payload, arm)


def test_evaluation_must_cover_exact_question_set() -> None:
    with pytest.raises(module.ProtocolError, match="omitted questions"):
        module.validate_evaluation_result({"answers": []}, {"q1"})
    with pytest.raises(module.ProtocolError, match="unknown question"):
        module.validate_evaluation_result(
            {"answers": [{"question_id": "q2", "answer": "x"}]}, {"q1"}
        )


def test_ai_memory_response_decoder_supports_json_and_sse() -> None:
    json_payload = '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    assert module.AiMemoryClient._decode_response(json_payload)["result"] == {"ok": True}
    sse = "event: message\ndata: " + json_payload + "\n\n"
    assert module.AiMemoryClient._decode_response(sse)["result"] == {"ok": True}


def test_external_output_is_fail_closed() -> None:
    assert protocol_module.parse_json_output('{"status":"success"}', "study") == {
        "status": "success"
    }
    with pytest.raises(module.ProtocolError, match="exactly one JSON object"):
        protocol_module.parse_json_output("log line\n{}", "study")


def test_rendered_study_prompt_does_not_include_ground_truth_patterns() -> None:
    scenario = minimal_scenario()
    prompt = module.render_study_prompt(
        "{study_objective}\n{tool_policy}\n{budget_json}\n{memory_workspace}/{memory_project}",
        scenario,
        arm_name="fetchcore",
        memory_workspace="w",
        memory_project_name="p",
    )
    assert "required_patterns" not in prompt
    assert "vector score is authority" not in prompt
