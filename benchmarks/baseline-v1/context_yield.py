#!/usr/bin/env python3
"""Re-evaluate frozen baseline-v1 ranked spans under deterministic line budgets."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from fetchcore.baseline import (
    BaselineConfigurationError,
    build_context_yield_artifact,
    load_baseline_suite,
    render_context_yield_report,
    write_context_yield_artifact,
)


ROOT = Path(__file__).resolve().parent
GROUND_TRUTH_PATH = ROOT / "ground-truth.json"


def main() -> int:
    args = _parser().parse_args()
    artifacts_dir = args.artifacts.resolve(strict=True)
    suite = load_baseline_suite(args.ground_truth.resolve(strict=True))
    run_manifest = _load_json(artifacts_dir / "run.json")

    raw_artifact_names = run_manifest.get("artifacts")
    if not isinstance(raw_artifact_names, list) or not raw_artifact_names:
        raise BaselineConfigurationError(
            "baseline run.json requires a non-empty artifacts list"
        )

    retrieval_artifacts: list[dict[str, object]] = []
    for raw_name in raw_artifact_names:
        name = str(raw_name).strip()
        if not name:
            raise BaselineConfigurationError(
                "baseline run.json contains an empty artifact name"
            )
        retrieval_artifacts.append(_load_json(artifacts_dir / name))

    derived = build_context_yield_artifact(
        suite=suite,
        retrieval_artifacts=retrieval_artifacts,
    )

    json_path = args.output or (artifacts_dir / "context-yield.json")
    markdown_path = args.markdown or (artifacts_dir / "context-yield.md")
    destination = write_context_yield_artifact(json_path, derived)
    markdown_path.resolve().write_text(
        render_context_yield_report(derived),
        encoding="utf-8",
    )

    print(f"[context-yield] json={destination}")
    print(f"[context-yield] markdown={markdown_path.resolve()}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts",
        type=Path,
        required=True,
        help="Versioned baseline run directory containing run.json and retrieval artifacts.",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=GROUND_TRUTH_PATH,
        help="Evidence-region Ground Truth JSON. Defaults to baseline-v1/ground-truth.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Derived JSON destination. Defaults to <artifacts>/context-yield.json.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Markdown destination. Defaults to <artifacts>/context-yield.md.",
    )
    return parser


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineConfigurationError(
            f"failed to load context-yield input from {path}"
        ) from exc
    if not isinstance(value, Mapping):
        raise BaselineConfigurationError(
            f"context-yield input {path} must contain a JSON object"
        )
    return {str(key): item for key, item in value.items()}


if __name__ == "__main__":
    raise SystemExit(main())
