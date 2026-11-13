from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "dogfood_mcp_eval.py"
SPEC = importlib.util.spec_from_file_location("dogfood_mcp_eval", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
dogfood = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dogfood
SPEC.loader.exec_module(dogfood)


def test_self_dogfood_scenarios_are_valid() -> None:
    scenarios = dogfood.load_scenarios(
        ROOT / "dogfood" / "scenarios" / "fetchcore-self.yaml"
    )

    assert len(scenarios) >= 5
    assert any(item.verify_source for item in scenarios)
    assert len({item.id for item in scenarios}) == len(scenarios)


def test_result_path_matching_accepts_repository_source_refs() -> None:
    result = {
        "source": "src/fetchcore/application/service.py",
        "source_ref": (
            "repository://oalangomes/fetchcore/"
            "src/fetchcore/application/service.py"
        ),
    }

    assert dogfood.result_matches_path(
        result,
        "src/fetchcore/application/service.py",
    )
    assert not dogfood.result_matches_path(
        result,
        "src/fetchcore/application/composition.py",
    )


def test_first_expected_rank_is_one_based() -> None:
    results = [
        {"source": "README.md"},
        {"source": "src/fetchcore/repository/index_policy.py"},
    ]

    rank = dogfood.first_expected_rank(
        results,
        ("src/fetchcore/repository/index_policy.py",),
    )

    assert rank == 2


def test_evaluation_is_report_only_without_explicit_gate() -> None:
    class Args:
        enforce_scenario_gate = False

    payload = {"status": "failed"}
    assert dogfood._evaluation_exit_code(payload, Args.enforce_scenario_gate) == 0


def test_evaluation_can_be_promoted_to_gate_explicitly() -> None:
    payload = {"status": "failed"}
    assert dogfood._evaluation_exit_code(payload, True) == 1
    assert dogfood._evaluation_exit_code({"status": "passed"}, True) == 0


def test_mcp_child_receives_only_required_loader_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/runtime/python/lib")
    monkeypatch.setenv("GITHUB_TOKEN", "not-forwarded")

    assert dogfood._loader_environment() == {"LD_LIBRARY_PATH": "/runtime/python/lib"}


def test_percentiles_are_observed_and_missing_values_remain_absent() -> None:
    assert dogfood._percentile([], 95) is None
    assert dogfood._percentile([10, 20, 30, 40], 50) == 25.0
    assert dogfood._percentile([10, 20, 30, 40], 95) == 40.0


def test_v1_scenarios_keep_their_search_contract() -> None:
    scenarios = dogfood.load_scenarios(
        ROOT / "dogfood" / "scenarios" / "fetchcore-self.yaml"
    )

    assert scenarios
    assert all(isinstance(item, dogfood.Scenario) for item in scenarios)
    assert all(item.expected_paths for item in scenarios)
    assert all(item.min_expected_path_hits >= 1 for item in scenarios)


def test_v2_symbol_scenarios_parse_with_exact_expectations() -> None:
    scenarios = dogfood.load_scenarios(
        ROOT / "dogfood" / "scenarios" / "fetchcore-symbols.yaml"
    )

    assert scenarios
    assert all(isinstance(item, dogfood.SymbolScenario) for item in scenarios)
    targeted = next(
        item for item in scenarios
        if item.name == "TargetedGitAwareSolrSynchronizer"
    )
    assert targeted.action == "find_symbol"
    assert targeted.kind == "class"
    assert targeted.path == (
        "src/fetchcore/adapters/retrieval/solr/incremental_persistence.py"
    )
    assert targeted.revision == "32a288977fc03bf7b903ef38d258c91ac975fe42"
    assert targeted.verify_source
    expected = targeted.expected_symbols
    assert len(expected) == 1
    assert expected[0].name == "TargetedGitAwareSolrSynchronizer"
    assert expected[0].qualified_name == (
        "fetchcore.adapters.retrieval.solr.incremental_persistence."
        "TargetedGitAwareSolrSynchronizer"
    )
    assert expected[0].kind == "class"
    assert expected[0].path == targeted.path
    assert expected[0].line_start == 25
    assert expected[0].line_end == 181
    assert any(len(item.expected_symbols) > 1 for item in scenarios)


def test_v2_rejects_unsupported_actions_and_missing_expectations() -> None:
    with pytest.raises(ValueError, match="unsupported action"):
        dogfood._load_symbol_scenarios({
            "version": 2,
            "scenarios": [{
                "id": "bad-action", "action": "search", "name": "x",
                "expected_symbols": [_symbol_expectation()],
            }],
        })
    with pytest.raises(ValueError, match="expected_symbols"):
        dogfood._load_symbol_scenarios({
            "version": 2,
            "scenarios": [{"id": "no-expectations", "action": "find_symbol", "name": "x"}],
        })


def test_v2_rejects_invalid_symbol_spans() -> None:
    with pytest.raises(ValueError, match="line_end >= line_start"):
        dogfood._expected_symbol(_symbol_expectation(line_start=30, line_end=29), "s", 0)
    with pytest.raises(ValueError, match="positive integer line_start"):
        dogfood._expected_symbol(_symbol_expectation(line_start=0, line_end=9), "s", 0)
    with pytest.raises(ValueError, match="line_start"):
        dogfood._expected_symbol(
            _symbol_expectation(line_start=True, line_end=9), "s", 0
        )


def test_unknown_scenario_version_fails_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "scenarios.yaml"
    path.write_text("version: 3\nscenarios:\n  - id: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        dogfood.load_scenarios(path)


def _symbol_expectation(**overrides: object) -> dict[str, object]:
    expectation: dict[str, object] = {
        "name": "TargetedGitAwareSolrSynchronizer",
        "qualified_name": (
            "fetchcore.adapters.retrieval.solr.incremental_persistence."
            "TargetedGitAwareSolrSynchronizer"
        ),
        "kind": "class",
        "path": "src/fetchcore/adapters/retrieval/solr/incremental_persistence.py",
        "line_start": 25,
        "line_end": 181,
    }
    expectation.update(overrides)
    return expectation


def _observed_symbol(**overrides: object) -> dict[str, object]:
    symbol: dict[str, object] = {
        "identity": "symbol:v1:sha256:abc",
        "repository": "oalangomes/fetchcore",
        "revision": "32a288977fc03bf7b903ef38d258c91ac975fe42",
        "path": "src/fetchcore/adapters/retrieval/solr/incremental_persistence.py",
        "source_ref": (
            "repository://oalangomes/fetchcore/"
            "src/fetchcore/adapters/retrieval/solr/incremental_persistence.py"
        ),
        "checksum": "sha256:0" * 4,
        "name": "TargetedGitAwareSolrSynchronizer",
        "qualified_name": (
            "fetchcore.adapters.retrieval.solr.incremental_persistence."
            "TargetedGitAwareSolrSynchronizer"
        ),
        "kind": "class",
        "line_start": 25,
        "line_end": 181,
    }
    symbol.update(overrides)
    return symbol


def _expected(**overrides: object) -> object:
    return dogfood._expected_symbol(_symbol_expectation(**overrides), "s", 0)


def test_exact_matching_requires_every_field_to_be_equal() -> None:
    expected = _expected()

    assert dogfood.symbol_matches_expected(_observed_symbol(), expected)
    assert not dogfood.symbol_matches_expected(
        _observed_symbol(line_end=182), expected
    )
    assert not dogfood.symbol_matches_expected(
        _observed_symbol(line_start=24), expected
    )
    assert not dogfood.symbol_matches_expected(
        _observed_symbol(kind="method"), expected
    )
    assert not dogfood.symbol_matches_expected(
        _observed_symbol(qualified_name="other.TargetedGitAwareSolrSynchronizer"),
        expected,
    )
    assert not dogfood.symbol_matches_expected(
        _observed_symbol(path="src/other.py"), expected
    )
    assert not dogfood.symbol_matches_expected(
        _observed_symbol(line_start=True), expected
    )
    assert dogfood.symbol_matches_expected(
        _observed_symbol(path="./src/fetchcore/adapters/retrieval/solr/incremental_persistence.py"),
        expected,
    )


def test_homonyms_match_by_distinct_exact_identity() -> None:
    function = _observed_symbol(
        name="find_symbol",
        qualified_name="fetchcore.repository.navigation.find_symbol",
        kind="function",
        path="src/fetchcore/repository/navigation.py",
        line_start=160,
        line_end=233,
    )
    method = _observed_symbol(
        name="find_symbol",
        qualified_name=(
            "fetchcore.application.navigation."
            "RepositoryKnowledgeNavigationService.find_symbol"
        ),
        kind="method",
        path="src/fetchcore/application/navigation.py",
        line_start=129,
        line_end=147,
    )
    function_expected = dogfood._expected_symbol({
        "name": "find_symbol",
        "qualified_name": "fetchcore.repository.navigation.find_symbol",
        "kind": "function",
        "path": "src/fetchcore/repository/navigation.py",
        "line_start": 160,
        "line_end": 233,
    }, "homonyms", 0)
    method_expected = dogfood._expected_symbol({
        "name": "find_symbol",
        "qualified_name": (
            "fetchcore.application.navigation."
            "RepositoryKnowledgeNavigationService.find_symbol"
        ),
        "kind": "method",
        "path": "src/fetchcore/application/navigation.py",
        "line_start": 129,
        "line_end": 147,
    }, "homonyms", 1)

    matched, missing = dogfood.match_expected_symbols(
        [function, method], (function_expected, method_expected)
    )

    assert not missing
    assert {hit["kind"] for _, hit in matched} == {"function", "method"}
    assert dogfood.match_expected_symbols(
        [method], (function_expected,)
    )[1] == [function_expected]


def test_provenance_is_required_on_every_observed_symbol() -> None:
    complete = _observed_symbol()
    assert dogfood.symbol_provenance_errors(complete) == ()

    missing_fields = _observed_symbol(revision="", checksum=None)
    errors = dogfood.symbol_provenance_errors(missing_fields)
    assert "missing revision" in errors
    assert "missing checksum" in errors

    invalid_span = _observed_symbol(line_start=0, line_end=5)
    errors = dogfood.symbol_provenance_errors(invalid_span)
    assert "invalid line_start" in errors
    assert "invalid line_end" in errors

    reversed_span = _observed_symbol(line_start=30, line_end=29)
    assert "invalid line_end" in dogfood.symbol_provenance_errors(reversed_span)


def test_payload_contract_errors_fail_closed() -> None:
    valid = {"status": "success", "results": []}
    assert dogfood.symbol_payload_errors(valid) == ()

    broken = {"status": "invented", "results": None}
    errors = dogfood.symbol_payload_errors(broken)
    assert "invalid navigation status" in errors
    assert "invalid results collection" in errors


def test_tool_errors_explicitly_carry_their_category() -> None:
    error_result = SimpleNamespace(
        is_error=True,
        content=[SimpleNamespace(
            type="text",
            text="FetchCore symbol lookup failed: source_revision_mismatch",
        )],
    )
    assert dogfood.call_result_error(error_result) == (
        "FetchCore symbol lookup failed: source_revision_mismatch"
    )
    assert dogfood.parse_error_category(
        "FetchCore symbol lookup failed: source_revision_mismatch"
    ) == "source_revision_mismatch"
    assert dogfood.parse_error_category(
        "FetchCore source read failed: source_revision_mismatch"
    ) == "source_revision_mismatch"

    ok_result = SimpleNamespace(is_error=False, content=())
    assert dogfood.call_result_error(ok_result) is None

    empty_error = SimpleNamespace(is_error=True, content=[])
    assert dogfood.call_result_error(empty_error) == "tool error without text content"


def test_source_verification_flags_revision_divergence_and_bad_content() -> None:
    symbol = _observed_symbol()
    valid_payload = {
        "repository": "oalangomes/fetchcore",
        "source_ref": symbol["source_ref"],
        "path": symbol["path"],
        "revision": symbol["revision"],
        "content": "\n".join(
            f"line {number}" if number != 25
            else "class TargetedGitAwareSolrSynchronizer(DocumentAwareGitSolrSynchronizer):"
            for number in range(25, 182)
        ),
        "line_start": 25,
        "line_end": 181,
        "total_lines": 475,
        "truncated": False,
    }
    assert dogfood.source_read_errors(valid_payload, symbol, "oalangomes/fetchcore") == ()
    assert "checksum" not in valid_payload

    diverged = dict(valid_payload, revision="older-revision")
    assert "get_source revision mismatch" in dogfood.source_read_errors(
        diverged, symbol, "oalangomes/fetchcore"
    )
    wrong_repo = dict(valid_payload, repository="other/repo")
    assert "get_source repository mismatch" in dogfood.source_read_errors(
        wrong_repo, symbol, "oalangomes/fetchcore"
    )
    short_window = dict(valid_payload, line_start=26, line_end=181)
    assert "get_source line_start mismatch" in dogfood.source_read_errors(
        short_window, symbol, "oalangomes/fetchcore"
    )
    short_content = dict(
        valid_payload, content="\n".join(f"l{number}" for number in range(25, 100))
    )
    errors = dogfood.source_read_errors(short_content, symbol, "oalangomes/fetchcore")
    assert "get_source content does not cover the returned line range" in errors
    empty_content = dict(valid_payload, content="")
    assert "get_source returned empty content" in dogfood.source_read_errors(
        empty_content, symbol, "oalangomes/fetchcore"
    )
    missing_name = dict(valid_payload, content="\n".join(
        f"line {number}" for number in range(25, 182)
    ))
    errors = dogfood.source_read_errors(missing_name, symbol, "oalangomes/fetchcore")
    assert "get_source content does not contain the symbol name" in errors


def test_structural_metrics_stay_separate_from_search_metrics() -> None:
    search_reports: list[dict[str, object]] = [
        {
            "passed": True,
            "reciprocal_rank": 1.0,
            "search_duration_ms": 12,
            "retrieval_telemetry": {"time_to_first_candidate_ms": 5},
        }
    ]
    search_summary = dogfood.search_summary(search_reports)
    assert "mrr" in search_summary
    assert "expected_path_hit_rate" in search_summary
    assert "symbol_match_rate" not in search_summary
    assert "matched_symbols" not in search_summary

    symbol_reports: list[dict[str, object]] = [
        {
            "outcome": "passed",
            "expected_symbols": [_symbol_expectation()],
            "matched_symbols": [{"result": _observed_symbol()}],
            "find_symbol_duration_ms": 7,
            "verify_source": {"requested": True, "verified": True},
        },
        {
            "outcome": "not_found",
            "expected_symbols": [_symbol_expectation(name="absent")],
            "matched_symbols": [],
            "find_symbol_duration_ms": 3,
            "verify_source": {"requested": False, "verified": False},
        },
        {
            "outcome": "unsupported",
            "expected_symbols": [_symbol_expectation()],
            "matched_symbols": [],
            "find_symbol_duration_ms": 2,
            "verify_source": {"requested": False, "verified": False},
        },
    ]
    symbol_summary = dogfood.symbol_summary(symbol_reports)
    assert "symbol_match_rate" in symbol_summary
    assert "provenance_valid_rate" in symbol_summary
    assert "mrr" not in symbol_summary
    assert "expected_path_hit_rate" not in symbol_summary
    assert "reciprocal_rank" not in symbol_summary

    assert symbol_summary["total"] == 3
    assert symbol_summary["passed"] == 1
    assert symbol_summary["failed"] == 1
    assert symbol_summary["not_found"] == 1
    assert symbol_summary["unsupported"] == 1
    assert symbol_summary["tool_errors"] == 0
    assert symbol_summary["contract_errors"] == 0
    assert symbol_summary["expected_symbols"] == 3
    assert symbol_summary["matched_symbols"] == 1
    assert symbol_summary["symbol_match_rate"] == pytest.approx(1 / 3)
    assert symbol_summary["provenance_valid_rate"] == 1.0
    assert symbol_summary["find_symbol_duration_ms_p50"] == 3.0
    assert symbol_summary["source_verified"] == 1


def test_contract_and_tool_errors_are_counted_apart_from_misses() -> None:
    reports: list[dict[str, object]] = [
        {"outcome": "tool_error", "expected_symbols": [], "matched_symbols": [],
         "verify_source": {"requested": False, "verified": False}},
        {"outcome": "contract_error", "expected_symbols": [], "matched_symbols": [],
         "verify_source": {"requested": False, "verified": False}},
        {"outcome": "failed", "expected_symbols": [], "matched_symbols": [],
         "verify_source": {"requested": False, "verified": False}},
    ]
    summary = dogfood.symbol_summary(reports)
    assert summary["total"] == 3
    assert summary["failed"] == 3
    assert summary["tool_errors"] == 1
    assert summary["contract_errors"] == 1
    assert summary["not_found"] == 0
    assert summary["passed"] == 0


def test_each_matched_symbol_is_verified_independently() -> None:
    scenarios = dogfood.load_scenarios(
        ROOT / "dogfood" / "scenarios" / "fetchcore-symbols.yaml"
    )
    scenario = next(
        item for item in scenarios if item.id == "find-symbol-homonyms"
    )
    function, method = scenario.expected_symbols
    function_symbol = _observed_symbol(
        name=function.name,
        qualified_name=function.qualified_name,
        kind=function.kind,
        path=function.path,
        source_ref=(
            "repository://oalangomes/fetchcore/"
            "src/fetchcore/repository/navigation.py"
        ),
        line_start=function.line_start,
        line_end=function.line_end,
    )
    method_symbol = _observed_symbol(
        name=method.name,
        qualified_name=method.qualified_name,
        kind=method.kind,
        path=method.path,
        source_ref=(
            "repository://oalangomes/fetchcore/"
            "src/fetchcore/application/navigation.py"
        ),
        line_start=method.line_start,
        line_end=method.line_end,
    )
    find_payload = {
        "repository": "oalangomes/fetchcore",
        "revision": "32a288977fc03bf7b903ef38d258c91ac975fe42",
        "results": [function_symbol, method_symbol],
        "coverage": [],
        "status": "success",
        "limit_reached": False,
        "scan_complete": True,
        "scan_diagnostics": [],
    }
    valid_source = {
        "repository": "oalangomes/fetchcore",
        "source_ref": function_symbol["source_ref"],
        "path": function_symbol["path"],
        "revision": function_symbol["revision"],
        "content": "\n".join(
            "def find_symbol(" if number == 160 else f"line {number}"
            for number in range(160, 234)
        ),
        "line_start": 160,
        "line_end": 233,
        "total_lines": 547,
        "truncated": False,
    }
    diverged_source = dict(
        valid_source,
        source_ref=method_symbol["source_ref"],
        path=method_symbol["path"],
        revision="older-revision",
        content="\n".join(
            "    def find_symbol(" if number == 129 else f"line {number}"
            for number in range(129, 148)
        ),
        line_start=129,
        line_end=147,
    )

    class FakeSession:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            if name == "find_symbol":
                return SimpleNamespace(
                    is_error=False, structured_content=find_payload
                )
            if arguments["source_ref"] == method_symbol["source_ref"]:
                return SimpleNamespace(
                    is_error=False, structured_content=diverged_source
                )
            return SimpleNamespace(
                is_error=False, structured_content=valid_source
            )

    report = asyncio.run(dogfood._evaluate_symbol_scenario(
        FakeSession(),
        SimpleNamespace(repository="oalangomes/fetchcore"),
        scenario,
    ))

    assert len(report["matched_symbols"]) == 2
    assert report["outcome"] == "failed"
    assert report["passed"] is False

    verification = report["verify_source"]
    assert verification["requested"] is True
    assert verification["requested_symbols"] == 2
    assert verification["verified_symbols"] == 1
    assert verification["verified"] is False

    per_symbol = verification["per_symbol"]
    assert len(per_symbol) == 2
    assert per_symbol[0]["verified"] is True
    assert per_symbol[0]["symbol"]["qualified_name"] == function.qualified_name
    assert per_symbol[0]["get_source_arguments"]["checksum"] == function_symbol["checksum"]
    assert per_symbol[1]["verified"] is False
    assert per_symbol[1]["symbol"]["qualified_name"] == method.qualified_name
    assert per_symbol[1]["get_source_arguments"]["checksum"] == method_symbol["checksum"]
    assert "get_source revision mismatch" in per_symbol[1]["reasons"]

    summary = dogfood.symbol_summary([report])
    assert summary["verify_source_requested"] == 1
    assert summary["source_verified"] == 0
    assert summary["verify_source_symbols_requested"] == 2
    assert summary["verify_source_symbols_passed"] == 1


def _plain_symbol_scenario(verify_source: bool = False) -> object:
    return dogfood.SymbolScenario(
        id="plain",
        action="find_symbol",
        name="TargetedGitAwareSolrSynchronizer",
        kind="class",
        path="src/fetchcore/adapters/retrieval/solr/incremental_persistence.py",
        revision="32a288977fc03bf7b903ef38d258c91ac975fe42",
        limit=10,
        expected_symbols=(_expected(),),
        verify_source=verify_source,
    )


def _find_symbol_session(payload: dict[str, object]) -> object:
    class FakeSession:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            assert name == "find_symbol"
            return SimpleNamespace(is_error=False, structured_content=payload)

    return FakeSession()


def test_results_are_validated_against_the_requested_scope() -> None:
    diverged = _observed_symbol(repository="other/repo")
    payload = {
        "repository": "oalangomes/fetchcore",
        "revision": "32a288977fc03bf7b903ef38d258c91ac975fe42",
        "results": [diverged],
        "coverage": [],
        "status": "success",
        "limit_reached": False,
        "scan_complete": True,
        "scan_diagnostics": [],
    }

    report = asyncio.run(dogfood._evaluate_symbol_scenario(
        _find_symbol_session(payload),
        SimpleNamespace(repository="oalangomes/fetchcore"),
        _plain_symbol_scenario(),
    ))

    assert report["outcome"] == "contract_error"
    assert "result 0: unexpected repository" in report["contract_errors"]
    assert report["passed"] is False

    assert dogfood.symbol_scope_errors(
        _observed_symbol(revision="older-revision"),
        "oalangomes/fetchcore",
        "32a288977fc03bf7b903ef38d258c91ac975fe42",
    ) == ("unexpected revision",)
    assert dogfood.symbol_scope_errors(
        _observed_symbol(),
        "oalangomes/fetchcore",
        "32a288977fc03bf7b903ef38d258c91ac975fe42",
    ) == ()


def test_incomplete_or_truncated_source_windows_fail_verification() -> None:
    symbol = _observed_symbol()
    complete = {
        "repository": "oalangomes/fetchcore",
        "source_ref": symbol["source_ref"],
        "path": symbol["path"],
        "revision": symbol["revision"],
        "content": "\n".join(
            "class TargetedGitAwareSolrSynchronizer(DocumentAwareGitSolrSynchronizer):"
            if number == 25 else f"line {number}"
            for number in range(25, 182)
        ),
        "line_start": 25,
        "line_end": 181,
        "total_lines": 475,
        "truncated": False,
    }

    truncated = dict(complete, truncated=True)
    assert "get_source returned a truncated window" in dogfood.source_read_errors(
        truncated, symbol, "oalangomes/fetchcore"
    )

    incomplete = dict(
        complete,
        line_end=200,
        content="\n".join(f"line {number}" for number in range(25, 201)),
    )
    assert "get_source line_end mismatch" in dogfood.source_read_errors(
        incomplete, symbol, "oalangomes/fetchcore"
    )


def test_malformed_results_are_contract_errors_without_silent_filtering() -> None:
    payload = {
        "repository": "oalangomes/fetchcore",
        "revision": "32a288977fc03bf7b903ef38d258c91ac975fe42",
        "results": [_observed_symbol(), "garbage"],
        "coverage": [],
        "status": "success",
        "limit_reached": False,
        "scan_complete": True,
        "scan_diagnostics": [],
    }

    report = asyncio.run(dogfood._evaluate_symbol_scenario(
        _find_symbol_session(payload),
        SimpleNamespace(repository="oalangomes/fetchcore"),
        _plain_symbol_scenario(),
    ))

    assert report["outcome"] == "contract_error"
    assert "result 1: not an object" in report["contract_errors"]
    assert report["result_count"] == 2
    assert report["passed"] is False


def test_duplicate_expectations_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate expected symbols"):
        dogfood._load_symbol_scenarios({
            "version": 2,
            "scenarios": [{
                "id": "dup",
                "action": "find_symbol",
                "name": "x",
                "expected_symbols": [_symbol_expectation(), _symbol_expectation()],
            }],
        })


def test_get_source_is_not_required_without_source_verification() -> None:
    plain = _plain_symbol_scenario(verify_source=False)
    verifying = _plain_symbol_scenario(verify_source=True)

    assert dogfood.symbol_required_tools((plain,)) == frozenset({"find_symbol"})
    assert dogfood.symbol_required_tools((verifying,)) == frozenset(
        {"find_symbol", "get_source"}
    )


def test_smoke_failure_is_reported_without_blocking_the_evaluation() -> None:
    args = SimpleNamespace(repository="oalangomes/fetchcore")

    class FailingSource:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            assert name == "get_source"
            return SimpleNamespace(
                is_error=True,
                content=[SimpleNamespace(
                    type="text",
                    text="FetchCore source read failed: source_revision_mismatch",
                )],
            )

    smoke, ok = asyncio.run(dogfood._symbol_smoke(
        FailingSource(),
        args,
        (_plain_symbol_scenario(verify_source=True),),
        frozenset({"find_symbol", "get_source"}),
    ))
    assert ok is False
    assert smoke["get_source_verified"] is False
    assert smoke["error_category"] == "source_revision_mismatch"

    class ForbiddenSession:
        async def call_tool(self, *call_args: object, **call_kwargs: object) -> object:
            raise AssertionError("get_source must not be called")

    smoke, ok = asyncio.run(dogfood._symbol_smoke(
        ForbiddenSession(),
        args,
        (_plain_symbol_scenario(verify_source=False),),
        dogfood.symbol_required_tools((_plain_symbol_scenario(verify_source=False),)),
    ))
    assert ok is True
    assert smoke["get_source_required"] is False
    assert smoke["get_source_verified"] is None


def test_verify_source_and_limit_types_are_strict() -> None:
    base = {
        "id": "s",
        "action": "find_symbol",
        "name": "x",
        "expected_symbols": [_symbol_expectation()],
    }
    with pytest.raises(ValueError, match="boolean verify_source"):
        dogfood._load_symbol_scenarios({
            "version": 2,
            "scenarios": [dict(base, verify_source="yes")],
        })
    with pytest.raises(ValueError, match="integer limit"):
        dogfood._load_symbol_scenarios({
            "version": 2,
            "scenarios": [dict(base, limit="20")],
        })
    with pytest.raises(ValueError, match="integer limit"):
        dogfood._load_symbol_scenarios({
            "version": 2,
            "scenarios": [dict(base, limit=True)],
        })
    with pytest.raises(ValueError, match="between 1 and 100"):
        dogfood._load_symbol_scenarios({
            "version": 2,
            "scenarios": [dict(base, limit=101)],
        })
