"""Validate and compare two frozen baseline-v1 runs without contacting services."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from statistics import mean
from typing import Any, cast

from fetchcore.baseline import (
    BASELINE_CONTEXT_YIELD_METRICS_SPEC,
    BASELINE_V1_METRICS_SPEC,
    BASELINE_V1_ARMS,
    build_context_yield_artifact,
    load_baseline_suite,
    mapping_fingerprint,
    percentile,
)

ROOT = Path(__file__).resolve().parent
METRICS = (
    "source_recall_at_k",
    "evidence_recall_at_k",
    "span_coverage_at_k",
    "evidence_reciprocal_rank",
    "evidence_ndcg_at_k",
    "useful_context_line_ratio",
    "candidates_examined",
    "candidates_returned",
    "returned_line_count",
    "returned_chars",
)
BUDGETS = (100, 250, 500, 1000)


class ComparisonError(ValueError):
    """A frozen run is incomplete or incompatible with this comparison."""


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"{path} must contain a JSON object")
    return value


def _require(value: bool, message: str) -> None:
    if not value:
        raise ComparisonError(message)


def _observations(
    artifact: Mapping[str, Any], queries: set[str], label: str
) -> dict[str, list[dict[str, Any]]]:
    raw = artifact.get("observations")
    _require(isinstance(raw, list), f"{label}: missing observations")
    raw = cast(list[Any], raw)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for item in raw:
        _require(isinstance(item, dict), f"{label}: malformed observation")
        query, iteration = item.get("query_id"), item.get("iteration")
        _require(
            query in queries and type(iteration) is int and iteration > 0,
            f"{label}: invalid query/iteration",
        )
        _require((query, iteration) not in seen, f"{label}: repeated query/iteration")
        seen.add((query, iteration))
        for key in (*METRICS, "duration_ms", "result_spans", "telemetry"):
            _require(key in item, f"{label}: observation missing {key}")
        spans = item["result_spans"]
        _require(isinstance(spans, list), f"{label}: malformed result_spans")
        for rank, span in enumerate(spans, 1):
            _require(
                isinstance(span, dict)
                and span.get("rank") == rank
                and isinstance(span.get("id"), str)
                and isinstance(span.get("source_ref"), str)
                and type(span.get("line_start")) is int
                and type(span.get("line_end")) is int
                and 1 <= span["line_start"] <= span["line_end"],
                f"{label}: invalid result span",
            )
        grouped[query].append(item)
    repetitions = artifact["config"].get("repetitions")
    _require(type(repetitions) is int and repetitions > 0, f"{label}: invalid repetitions")
    repetitions = cast(int, repetitions)
    for query in queries:
        iterations = {item["iteration"] for item in grouped[query]}
        _require(
            iterations == set(range(1, repetitions + 1)),
            f"{label}: incomplete repetitions for {query}",
        )
        grouped[query].sort(key=lambda item: item["iteration"])
    _require(len(raw) == len(queries) * repetitions, f"{label}: observation count mismatch")
    return grouped


def _ranking(observation: Mapping[str, Any]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (s["id"], s["source_ref"], s["line_start"], s["line_end"])
        for s in observation["result_spans"]
    )


def _modal(group: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    counts = Counter(_ranking(item) for item in group)
    return min(counts, key=lambda item: (-counts[item], repr(item)))


def _recovered(ranking: tuple[tuple[Any, ...], ...], truth: Any) -> set[str]:
    recovered: set[str] = set()
    for requirement in truth.requirements:
        for alternative in requirement.alternatives:
            if all(
                all(
                    any(
                        source == region.source_ref and start <= line <= end
                        for _, source, start, end in ranking
                    )
                    for line in range(region.line_start, region.line_end + 1)
                )
                for region in alternative.regions
            ):
                recovered.add(requirement.id)
                break
    return recovered


def _evidence_positions(ranking: tuple[tuple[Any, ...], ...], truth: Any) -> list[int]:
    regions = [
        region
        for requirement in truth.requirements
        for alternative in requirement.alternatives
        for region in alternative.regions
    ]
    return [
        rank
        for rank, (_, source, start, end) in enumerate(ranking, 1)
        if any(
            source == region.source_ref and start <= region.line_end and end >= region.line_start
            for region in regions
        )
    ]


def _validate_run(directory: Path, suite: Any) -> dict[str, Any]:
    run = _read(directory / "run.json")
    manifest_fingerprint = mapping_fingerprint(_read(ROOT / "manifest.json"))
    _require(
        run.get("schema_version") == 1
        and run.get("protocol") == "fetchcore.baseline-v1"
        and run.get("status") == "complete",
        f"{directory}: incompatible run",
    )
    _require(run.get("corpus") == suite.corpus, f"{directory}: corpus mismatch")
    _require(bool(run.get("fetchcore_revision")), f"{directory}: missing revision")
    indexing_file = run.get("indexing_artifact")
    if run.get("prepare_index") is True:
        _require(indexing_file == "indexing.json", f"{directory}: missing indexing artifact")
        indexing = _read(directory / "indexing.json")
        _require(
            indexing.get("schema_version") == 1
            and indexing.get("protocol") == "fetchcore.baseline-v1"
            and indexing.get("fetchcore_revision") == run["fetchcore_revision"]
            and indexing.get("corpus", {}).get("repository") == suite.corpus["repository"]
            and indexing.get("corpus", {}).get("revision") == suite.corpus["revision"]
            and isinstance(indexing.get("indexing_fingerprint"), str),
            f"{directory}: incompatible indexing artifact",
        )
    else:
        _require(
            run.get("prepare_index") is False
            and indexing_file is None
            and not (directory / "indexing.json").exists(),
            f"{directory}: inconsistent index preparation state",
        )
    arms = run.get("arms")
    _require(isinstance(arms, list) and len(arms) == 9, f"{directory}: expected nine arms")
    arms = cast(list[dict[str, Any]], arms)
    arm_ids = [item.get("id") for item in arms]
    _require(
        set(arm_ids) == {arm.id for arm in BASELINE_V1_ARMS}
        and set(run.get("artifacts", [])) == {f"{arm}.json" for arm in arm_ids},
        f"{directory}: arm inventory mismatch",
    )
    queries = {case.query_id for case in suite.cases}
    artifacts: dict[str, Any] = {}
    for arm in arms:
        arm_id = arm["id"]
        item = _read(directory / f"{arm_id}.json")
        label = f"{directory}/{arm_id}"
        for key, expected in (
            ("schema_version", 2),
            ("protocol", "fetchcore.baseline-v1"),
            ("ground_truth_schema_version", suite.schema_version),
            ("ground_truth_fingerprint", suite.fingerprint),
            ("metrics_schema_version", 1),
            ("metrics_fingerprint", mapping_fingerprint(BASELINE_V1_METRICS_SPEC)),
            ("corpus", suite.corpus),
            ("arm", arm),
        ):
            _require(item.get(key) == expected, f"{label}: incompatible {key}")
        config = item.get("config")
        _require(
            isinstance(config, dict)
            and item.get("config_fingerprint") == mapping_fingerprint(config),
            f"{label}: config fingerprint mismatch",
        )
        config = cast(dict[str, Any], config)
        identity = config.get("identity", {})
        _require(
            identity.get("fetchcore_revision") == run["fetchcore_revision"]
            and identity.get("corpus_revision") == suite.corpus["revision"]
            and identity.get("manifest_fingerprint") == manifest_fingerprint
            and config.get("k") == 20
            and config.get("warmups") == 1
            and config.get("repetitions") == 20,
            f"{label}: measurement identity mismatch",
        )
        grouped = _observations(item, queries, label)
        aggregate = item.get("aggregate", {})
        flat = [obs for group in grouped.values() for obs in group]
        observed_fingerprints: dict[str, set[str]] = defaultdict(set)
        for observation in flat:
            fingerprints = observation["telemetry"].get("fingerprints", {})
            _require(isinstance(fingerprints, dict), f"{label}: malformed fingerprints")
            for name, fingerprint in fingerprints.items():
                _require(
                    isinstance(fingerprint, str) and bool(fingerprint),
                    f"{label}: invalid {name} fingerprint",
                )
                observed_fingerprints[name].add(fingerprint)
        expected_fingerprints = {
            name: sorted(values) for name, values in observed_fingerprints.items()
        }
        _require(
            aggregate.get("fingerprints") == expected_fingerprints,
            f"{label}: aggregate fingerprints differ from observations",
        )
        required_fingerprints = set() if arm_id == "local-lexical" else {"projection"}
        if arm["mode"] in ("dense", "hybrid"):
            required_fingerprints.add("embedding_serializer")
        if arm["mode"] == "hybrid":
            required_fingerprints.update(("fusion_input", "fusion_output"))
        if arm_id == "qdrant-hybrid":
            required_fingerprints.add("sparse_analyzer")
        _require(
            required_fingerprints <= expected_fingerprints.keys(),
            f"{label}: required retrieval fingerprints missing",
        )
        _require(
            aggregate.get("run_count") == len(flat)
            and aggregate.get("query_count") == len(queries),
            f"{label}: aggregate count mismatch",
        )
        for metric, key in (
            ("source_recall_at_k", "source_recall_at_k_mean"),
            ("evidence_recall_at_k", "evidence_recall_at_k_mean"),
            ("span_coverage_at_k", "span_coverage_at_k_mean"),
            ("evidence_reciprocal_rank", "evidence_mrr_mean"),
            ("evidence_ndcg_at_k", "evidence_ndcg_at_k_mean"),
            ("useful_context_line_ratio", "useful_context_line_ratio_mean"),
            ("candidates_examined", "candidates_examined_mean"),
            ("candidates_returned", "candidates_returned_mean"),
            ("returned_line_count", "returned_line_count_mean"),
            ("returned_chars", "returned_chars_mean"),
        ):
            _require(
                abs(mean(obs[metric] for obs in flat) - aggregate.get(key, float("inf")))
                < 0.000001,
                f"{label}: aggregate {key} differs from observations",
            )
        durations = [obs["duration_ms"] for obs in flat]
        _require(
            aggregate.get("duration_ms")
            == {
                p: percentile(durations, q) for p, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
            },
            f"{label}: duration percentiles differ from observations",
        )
        artifacts[arm_id] = (item, grouped)
    context = _read(directory / "context-yield.json")
    for key, expected in (
        ("schema_version", 1),
        ("protocol", "fetchcore.baseline-v1-context-yield"),
        ("source_protocol", "fetchcore.baseline-v1"),
        ("source_artifact_schema_version", 2),
        ("source_metrics_schema_version", 1),
        ("source_metrics_fingerprint", mapping_fingerprint(BASELINE_V1_METRICS_SPEC)),
        ("metrics_fingerprint", mapping_fingerprint(BASELINE_CONTEXT_YIELD_METRICS_SPEC)),
        ("ground_truth_schema_version", suite.schema_version),
        ("ground_truth_fingerprint", suite.fingerprint),
    ):
        _require(context.get(key) == expected, f"{directory}: incompatible context {key}")
    _require(
        context.get("budget_config_fingerprint")
        == mapping_fingerprint(context.get("budget_config", {})),
        f"{directory}: context budget fingerprint mismatch",
    )
    _require(
        context.get("budget_config", {}).get("budgets") == list(BUDGETS),
        f"{directory}: incompatible budgets",
    )
    context_arms: dict[str, Any] = {}
    for entry in context.get("arms", []):
        arm_id = entry["arm"]["id"]
        _require(
            arm_id in artifacts and arm_id not in context_arms,
            f"{directory}: context arm inventory mismatch",
        )
        _require(
            entry.get("source_config_fingerprint") == artifacts[arm_id][0]["config_fingerprint"],
            f"{directory}/{arm_id}: context source fingerprint mismatch",
        )
        _require(
            entry.get("observation_count") == len(artifacts[arm_id][0]["observations"]),
            f"{directory}/{arm_id}: context observation count mismatch",
        )
        found = {(obs["query_id"], obs["iteration"]): obs for obs in entry["observations"]}
        expected = {
            (obs["query_id"], obs["iteration"])
            for group in artifacts[arm_id][1].values()
            for obs in group
        }
        _require(
            set(found) == expected and len(entry["observations"]) == len(expected),
            f"{directory}/{arm_id}: context observations incomplete",
        )
        for obs in found.values():
            _require(
                {budget["line_budget"] for budget in obs["budgets"]} == set(BUDGETS),
                f"{directory}/{arm_id}: context budgets incomplete",
            )
        context_arms[arm_id] = found
    _require(set(context_arms) == set(artifacts), f"{directory}: context arms incomplete")
    recalculated = build_context_yield_artifact(
        suite=suite,
        retrieval_artifacts=[artifacts[arm["id"]][0] for arm in arms],
        budgets=BUDGETS,
    )
    _require(context == recalculated, f"{directory}: context yield differs from frozen spans")
    return {"run": run, "artifacts": artifacts, "context": context, "context_arms": context_arms}


def _core_config(config: Mapping[str, Any]) -> dict[str, Any]:
    copy = cast(dict[str, Any], json.loads(json.dumps(config)))
    copy["identity"].pop("fetchcore_revision", None)
    return copy


def _yield_values(run: dict[str, Any], arm_id: str, query: str, budget: int) -> list[float]:
    return [
        next(
            item["context_yield"]
            for item in run["context_arms"][arm_id][(query, iteration)]["budgets"]
            if item["line_budget"] == budget
        )
        for iteration in range(1, 21)
    ]


def _aggregate(run: dict[str, Any], arm_id: str) -> dict[str, Any]:
    item = run["artifacts"][arm_id][0]["aggregate"]
    context = next(x for x in run["context"]["arms"] if x["arm"]["id"] == arm_id)
    return {
        "source_recall_at_20": item["source_recall_at_k_mean"],
        "evidence_recall_at_20": item["evidence_recall_at_k_mean"],
        "span_coverage_at_20": item["span_coverage_at_k_mean"],
        "evidence_mrr": item["evidence_mrr_mean"],
        "evidence_ndcg_at_20": item["evidence_ndcg_at_k_mean"],
        "useful_context_line_ratio": item["useful_context_line_ratio_mean"],
        "candidates_examined": item["candidates_examined_mean"],
        "candidates_returned": item["candidates_returned_mean"],
        "returned_lines": item["returned_line_count_mean"],
        "returned_chars": item["returned_chars_mean"],
        "latency_ms": item["duration_ms"],
        "context_yield": {
            str(k): context["aggregate"][str(k)]["context_yield_mean"] for k in BUDGETS
        },
    }


def compare(baseline: Path, candidate: Path) -> dict[str, Any]:
    """Return a deterministic, JSON-serializable comparison of frozen observations."""
    suite = load_baseline_suite(ROOT / "ground-truth.json")
    left, right = _validate_run(baseline, suite), _validate_run(candidate, suite)
    _require(
        left["context"]["metrics_fingerprint"] == right["context"]["metrics_fingerprint"]
        and left["context"]["budget_config_fingerprint"]
        == right["context"]["budget_config_fingerprint"],
        "context metric or budget fingerprint mismatch",
    )
    _require(set(left["artifacts"]) == set(right["artifacts"]), "run arms differ")
    cases = {case.query_id: case for case in suite.cases}
    output: dict[str, Any] = {
        "protocol": "fetchcore.baseline-v1-offline-comparison-v1",
        "baseline_revision": left["run"]["fetchcore_revision"],
        "candidate_revision": right["run"]["fetchcore_revision"],
        "corpus": suite.corpus,
        "ground_truth_fingerprint": suite.fingerprint,
        "metrics_fingerprint": mapping_fingerprint(BASELINE_V1_METRICS_SPEC),
        "context_metrics_fingerprint": left["context"]["metrics_fingerprint"],
        "budget_config_fingerprint": left["context"]["budget_config_fingerprint"],
        "arms": {},
    }
    for arm_id in sorted(left["artifacts"]):
        a, ag = left["artifacts"][arm_id]
        b, bg = right["artifacts"][arm_id]
        _require(
            _core_config(a["config"]) == _core_config(b["config"]),
            f"{arm_id}: configuration differs beyond FetchCore revision",
        )
        stable_fingerprints = ("projection", "embedding_serializer", "sparse_analyzer")
        for name in stable_fingerprints:
            _require(
                a["aggregate"]["fingerprints"].get(name)
                == b["aggregate"]["fingerprints"].get(name),
                f"{arm_id}: {name} fingerprint mismatch",
            )
        queries: dict[str, Any] = {}
        for query in sorted(cases):
            aa, bb = ag[query], bg[query]
            ar, br = _modal(aa), _modal(bb)
            ac, bc = Counter(_ranking(item) for item in aa), Counter(_ranking(item) for item in bb)
            old_evidence = _recovered(ar, cases[query])
            new_evidence = _recovered(br, cases[query])
            metrics = {
                name: {
                    "baseline_mean": round(mean(obs[name] for obs in aa), 6),
                    "candidate_mean": round(mean(obs[name] for obs in bb), 6),
                    "delta": round(
                        mean(obs[name] for obs in bb) - mean(obs[name] for obs in aa), 6
                    ),
                    "baseline_range": [min(obs[name] for obs in aa), max(obs[name] for obs in aa)],
                    "candidate_range": [min(obs[name] for obs in bb), max(obs[name] for obs in bb)],
                }
                for name in METRICS
            }
            latency = {
                "baseline_ms": {
                    p: percentile([obs["duration_ms"] for obs in aa], q)
                    for p, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
                },
                "candidate_ms": {
                    p: percentile([obs["duration_ms"] for obs in bb], q)
                    for p, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
                },
                "baseline_range_ms": [
                    min(obs["duration_ms"] for obs in aa),
                    max(obs["duration_ms"] for obs in aa),
                ],
                "candidate_range_ms": [
                    min(obs["duration_ms"] for obs in bb),
                    max(obs["duration_ms"] for obs in bb),
                ],
            }
            yields: dict[str, Any] = {}
            for budget in BUDGETS:
                av = _yield_values(left, arm_id, query, budget)
                bv = _yield_values(right, arm_id, query, budget)
                yields[str(budget)] = {
                    "baseline_mean": round(mean(av), 6),
                    "candidate_mean": round(mean(bv), 6),
                    "delta": round(mean(bv) - mean(av), 6),
                    "baseline_range": [min(av), max(av)],
                    "candidate_range": [min(bv), max(bv)],
                }
            queries[query] = {
                "metrics": metrics,
                "latency": latency,
                "context_yield": yields,
                "ranking": {
                    "exact_rankings_changed": ar != br,
                    "top_k_membership_changed": set(ar) != set(br),
                    "ordering_only": ar != br and set(ar) == set(br),
                    "baseline_unique_rankings": len(ac),
                    "candidate_unique_rankings": len(bc),
                    "matching_repetition_pairs": sum(
                        _ranking(x) == _ranking(y) for x, y in zip(aa, bb, strict=True)
                    ),
                    "baseline_evidence_positions": _evidence_positions(ar, cases[query]),
                    "candidate_evidence_positions": _evidence_positions(br, cases[query]),
                    "newly_recovered_evidence": sorted(new_evidence - old_evidence),
                    "lost_evidence": sorted(old_evidence - new_evidence),
                },
            }
        output["arms"][arm_id] = {
            "config_fingerprints": {
                "baseline": a["config_fingerprint"],
                "candidate": b["config_fingerprint"],
            },
            "manifest_fingerprint": a["config"]["identity"]["manifest_fingerprint"],
            "observed_fingerprints": {
                "baseline": a["aggregate"]["fingerprints"],
                "candidate": b["aggregate"]["fingerprints"],
            },
            "environment_difference": a["environment"] != b["environment"],
            "baseline_environment": a["environment"],
            "candidate_environment": b["environment"],
            "baseline": _aggregate(left, arm_id),
            "candidate": _aggregate(right, arm_id),
            "queries": queries,
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.baseline, args.candidate)
    except (ComparisonError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"comparison failed: {exc}\n")
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
