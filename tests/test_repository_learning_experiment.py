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

WRAPPER = RUNNER.parent / "wrappers" / "codex_harness.py"
WRAPPER_SPEC = importlib.util.spec_from_file_location("repository_learning_codex", WRAPPER)
assert WRAPPER_SPEC is not None and WRAPPER_SPEC.loader is not None
wrapper_module = importlib.util.module_from_spec(WRAPPER_SPEC)
WRAPPER_SPEC.loader.exec_module(wrapper_module)

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


def test_checked_in_prompt_templates_render_literal_json_examples() -> None:
    root = RUNNER.parent
    scenario = minimal_scenario()
    study = module.render_study_prompt(
        (root / "prompts" / "study.md").read_text(encoding="utf-8"),
        scenario,
        arm_name="direct",
        memory_workspace="w",
        memory_project_name="p",
    )
    evaluation = module.render_evaluation_prompt(
        (root / "prompts" / "evaluate.md").read_text(encoding="utf-8"),
        scenario,
        arm_name="direct",
        memory_workspace="w",
        memory_project_name="p",
        recalled={"q1": {"content": []}},
    )

    assert '"status": "success"' in study
    assert '"answers": [' in evaluation


def test_codex_wrapper_uses_fetchcore_evidence_character_telemetry() -> None:
    item = {
        "type": "mcp_tool_call",
        "result": {
            "structuredContent": {
                "results": [{"content": "small"}],
                "telemetry": {"returned_chars": 123},
                "diagnostics": "x" * 1000,
            }
        },
    }
    assert wrapper_module._returned_chars([item]) == 123


def test_codex_wrapper_rejects_non_exploration_direct_command() -> None:
    with pytest.raises(wrapper_module.HarnessError, match="forbidden command"):
        wrapper_module._audit_direct_commands(
            [{"type": "command_execution", "command": "python -m fetchcore.cli search x"}]
        )


def test_codex_wrapper_audits_commands_inside_shell_wrapper() -> None:
    wrapper_module._audit_direct_commands(
        [
            {
                "type": "command_execution",
                "command": '/bin/bash -lc "rg --files | sed -n \'1,20p\'"',
            }
        ]
    )


def test_codex_wrapper_does_not_split_quoted_regex_alternation() -> None:
    wrapper_module._audit_direct_commands(
        [
            {
                "type": "command_execution",
                "command": 'rg -n "SearchIntent|SearchScope|RRF" . | sed -n \'1,20p\'',
            }
        ]
    )


def test_codex_wrapper_ignores_long_prose_when_counting_paths(tmp_path: Path) -> None:
    items = [
        {
            "type": "command_execution",
            "command": "rg canonical",
            "aggregated_output": "prose " * 200,
        }
    ]
    assert wrapper_module._direct_paths(items, tmp_path) == set()


def test_repository_learning_protocol_v2_expands_memory_pages() -> None:
    assert module.PROTOCOL == "fetchcore.repository-learning-v2"


def test_ai_memory_client_reads_exact_page_after_query() -> None:
    client = module.AiMemoryClient("http://memory.invalid", None)
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
        calls.append((name, arguments))
        return {"path": "concepts/retrieval.md", "body": "# Retrieval\n\nRRF"}

    client.call_tool = fake_call
    result = client.read_page("workspace", "project", "concepts/retrieval.md")

    assert result["body"] == "# Retrieval\n\nRRF"
    assert calls == [
        (
            "memory_read_page",
            {
                "workspace": "workspace",
                "project": "project",
                "path": "concepts/retrieval.md",
            },
        )
    ]


def test_memory_recall_expands_ranked_unique_hits_only() -> None:
    client = module.AiMemoryClient("http://memory.invalid", None)
    paths: list[str] = []

    def fake_read_page(workspace: str, project: str, path: str) -> dict[str, object]:
        assert workspace == "w"
        assert project == "p"
        paths.append(path)
        return {"path": path, "body": f"# {path}\n\nfull body"}

    client.read_page = fake_read_page
    query_result = {
        "hits": [
            {"path": "concepts/a.md", "snippet": "a"},
            {"path": "concepts/a.md", "snippet": "duplicate"},
            {"path": "concepts/b.md", "snippet": "b"},
            {"path": "concepts/c.md", "snippet": "c"},
        ]
    }

    recalled = module.expand_memory_recall(
        client,
        workspace="w",
        project="p",
        query_result=query_result,
        read_page_limit=2,
    )

    assert paths == ["concepts/a.md", "concepts/b.md"]
    assert recalled["discovery"] == query_result
    assert [page["path"] for page in recalled["expanded_pages"]] == paths


def test_stage_scores_distinguish_stored_recalled_and_answered_facts() -> None:
    scenario = minimal_scenario()
    study = {
        "memory_pages": [
            {
                "path": "concepts/boundary.md",
                "body": "# Boundary\n\nCanonical Repository Knowledge is canonical.",
            }
        ]
    }
    stored = module.score_stored_memory(scenario, study)
    assert stored["fact_recall"] == 1.0

    recalled = {
        "q1": {
            "discovery": {"hits": [{"path": "concepts/boundary.md"}]},
            "expanded_pages": [
                {
                    "path": "concepts/boundary.md",
                    "page": {
                        "path": "concepts/boundary.md",
                        "body": "# Boundary\n\nCanonical Repository Knowledge is canonical.",
                    },
                }
            ],
        }
    }
    memory_score = module.score_memory_recall(scenario, recalled)
    assert memory_score["fact_recall"] == 1.0

    answer_score = module.score_answers(scenario, {"q1": "UNKNOWN"})
    assert answer_score["fact_recall"] == 0.0

    metrics = module.memory_recall_metrics(recalled)
    assert metrics["queries"] == 1
    assert metrics["full_pages_read"] == 1
    assert metrics["distinct_pages_read"] == 1
    assert metrics["context_chars"] > 0
