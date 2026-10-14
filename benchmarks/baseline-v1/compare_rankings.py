#!/usr/bin/env python3
"""Compare one current pass with the frozen #18 result coordinates.

This checks retrieval/ranking equivalence without allocating a new benchmark
run or replacing the frozen 20-repetition latency measurements.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from fetchcore.application import BackendRegistry
from fetchcore.baseline import BaselineArm, load_baseline_suite
from fetchcore.retrieval import RetrievalConfig
from fetchcore.retrieval.contracts import SearchScope


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--arm", action="append", default=[])
    parser.add_argument("--reference", type=Path, default=ROOT / "artifacts" / "V0.1")
    args = parser.parse_args()

    if not args.workspace.strip():
        parser.error("--workspace is empty; set BENCH_REPO to the frozen corpus checkout")
    workspace = Path(args.workspace).resolve(strict=True)
    suite = load_baseline_suite(ROOT / "ground-truth.json")
    expected_revision = str(suite.corpus["revision"])
    revision = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(workspace), "status", "--porcelain"], text=True
    ).strip()
    if revision != expected_revision:
        raise ValueError(
            f"baseline corpus revision mismatch for {workspace}: "
            f"expected {expected_revision}, got {revision}"
        )
    if dirty:
        raise ValueError(f"baseline corpus workspace is dirty: {workspace}\n{dirty}")
    suite.validate_workspace_regions(workspace)

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    os.environ["OLLAMA_URL"] = manifest["environment"]["service_endpoints"]["ollama"]
    raw_arms = manifest.get("arms")
    if not isinstance(raw_arms, list):
        raise ValueError("baseline manifest requires an arms list")
    parsed_arms = []
    for item in raw_arms:
        if not isinstance(item, Mapping):
            raise ValueError("baseline manifest arms must be JSON objects")
        parsed_arms.append(BaselineArm.from_mapping(item))
    arms = {arm.id: arm for arm in parsed_arms}
    selected = args.arm or list(arms)
    unknown = set(selected) - arms.keys()
    if unknown:
        raise ValueError(f"unknown baseline arm(s): {sorted(unknown)}")

    all_equal = True
    for arm_id in selected:
        arm = arms[arm_id]
        artifact = json.loads(
            (args.reference / f"{arm_id}.json").read_text(encoding="utf-8")
        )
        scope_data = artifact["config"]["scope"]
        retrieval_data = scope_data.get("retrieval")
        scope = SearchScope(
            workspace=workspace,
            revision=expected_revision,
            repository=str(suite.corpus["repository"]),
            knowledge_types=tuple(scope_data["knowledge_types"]),
            max_results=int(scope_data["max_results"]),
            mode=scope_data["mode"],
            repository_paths=tuple(scope_data["repository_paths"]),
            result_namespace=scope_data["result_namespace"],
            settings=artifact["config"]["identity"]["backend_settings"],
            retrieval=(
                None
                if retrieval_data is None
                else RetrievalConfig.from_mapping(retrieval_data)
            ),
        )
        reference = {
            observation["query_id"]: observation["result_spans"]
            for observation in artifact["observations"]
            if observation["iteration"] == 1
        }
        backend = BackendRegistry().create(arm.backend)
        differences: list[str] = []
        for case in suite.cases:
            results = backend.search(case.intent, scope, ()).results
            coordinates = [
                {
                    "rank": rank,
                    "id": result.id,
                    "source_ref": result.source_ref,
                    "line_start": result.line_start,
                    "line_end": result.line_end,
                }
                for rank, result in enumerate(results, start=1)
            ]
            if coordinates != reference[case.query_id]:
                differences.append(case.query_id)
        matched = len(suite.cases) - len(differences)
        print(f"{arm_id}: {matched}/{len(suite.cases)} exact rankings", flush=True)
        if differences:
            print(f"  differing queries: {', '.join(differences)}", flush=True)
            all_equal = False
    return 0 if all_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
