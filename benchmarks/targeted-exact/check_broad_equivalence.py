"""Recheck original legacy queries against frozen spans and deterministic metrics."""

import argparse
import json
import subprocess
from pathlib import Path
from fetchcore.baseline import load_baseline_suite, evaluate_retrieval
from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
from fetchcore.retrieval.contracts import SearchScope

parser = argparse.ArgumentParser(description="Check unanchored broad retrieval against frozen V0.1")
parser.add_argument("--workspace", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
x = json.loads((root / "benchmarks/baseline-v1/artifacts/V0.1/local-lexical.json").read_text())
suite = load_baseline_suite(root / "benchmarks/baseline-v1/ground-truth.json")
revision = subprocess.check_output(
    ["git", "-C", str(args.workspace), "rev-parse", "HEAD"], text=True
).strip()
dirty = subprocess.check_output(
    ["git", "-C", str(args.workspace), "status", "--porcelain"], text=True
).strip()
if revision != suite.corpus["revision"] or dirty:
    parser.error("workspace must be clean and at the frozen corpus revision")
scope_data = x["config"]["scope"].copy()
scope_data.pop("retrieval", None)
scope_data.update(workspace=args.workspace, settings=x["config"]["identity"]["backend_settings"])
scope_data["knowledge_types"] = tuple(scope_data["knowledge_types"])
scope_data["repository_paths"] = tuple(scope_data["repository_paths"])
scope = SearchScope(**scope_data)
old = {o["query_id"]: o for o in x["observations"]}
checks = []
for case in suite.cases:
    response = LocalSemanticBackend().search(case.intent, scope, ())
    observed = evaluate_retrieval(response, case.truth, iteration=1, k=20).to_mapping()
    fields = [
        "result_spans",
        "evidence_recall_at_k",
        "source_recall_at_k",
        "candidates_examined",
        "returned_chars",
        "returned_line_count",
    ]
    deltas = [key for key in fields if old[case.query_id][key] != observed[key]]
    checks.append({"query_id": case.query_id, "equivalent": not deltas, "different_fields": deltas})
    print(case.query_id, not deltas, flush=True)
payload = {
    "protocol": "fetchcore.targeted-exact-v1.broad-equivalence",
    "baseline": "benchmarks/baseline-v1/artifacts/V0.1/local-lexical.json",
    "corpus": suite.corpus,
    "checks": checks,
    "all_equivalent": all(x["equivalent"] for x in checks),
}
(root / "benchmarks/targeted-exact/artifacts/broad-equivalence.json").write_text(
    json.dumps(payload, indent=2) + "\n"
)
assert payload["all_equivalent"], payload
