"""Compare the corrective fixture on old and current source trees."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMINGS = (
    "resolution_p50_ms", "resolution_p95_ms", "s5_index_p50_ms",
    "s5_index_p95_ms", "baseline_index_p50_ms", "baseline_index_p95_ms",
    "large_s5_index_p50_ms", "large_s5_index_p95_ms",
    "large_baseline_index_p50_ms", "large_baseline_index_p95_ms",
)


def run_once(source: Path) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source)
    output = subprocess.check_output(
        [sys.executable, str(HERE / "run.py")], env=environment, text=True,
    )
    value = json.loads(output)
    if not isinstance(value, dict):
        raise ValueError("benchmark result must be an object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-src", type=Path, required=True)
    parser.add_argument("--after-src", type=Path, default=Path("src"))
    parser.add_argument("--pairs", type=int, default=3)
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error("--pairs must be positive")
    results: dict[str, list[dict[str, object]]] = {"before": [], "after": []}
    for index in range(args.pairs):
        for arm in (("before", "after") if index % 2 == 0 else ("after", "before")):
            source = args.before_src if arm == "before" else args.after_src
            results[arm].append(run_once(source.resolve()))
    summary = {
        arm: {
            key: {
                "median": round(statistics.median(float(sample[key]) for sample in samples), 3),
                "min": min(float(sample[key]) for sample in samples),
                "max": max(float(sample[key]) for sample in samples),
            }
            for key in TIMINGS
        }
        for arm, samples in results.items()
    }
    print(json.dumps({"pairs": args.pairs, "timing_summary_ms": summary,
                      "samples": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
