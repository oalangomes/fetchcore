"""Benchmark catalog excludes synthetic acceptance tests and issue-shaped suites."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_catalog_has_only_declared_measurement_suites() -> None:
    root = ROOT / "benchmarks"
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 1
    declared = catalog["benchmarks"]
    names = [item["id"] for item in declared]
    actual = {item.name for item in root.iterdir() if item.is_dir() and item.name != "__pycache__"}
    assert len(names) == len(set(names))
    assert set(names) == actual
    assert all(item["classification"] in {"official_frozen", "controlled_experiment"} for item in declared)
    for name in names:
        assert not re.search(r"(?:^|[-_])(?:s[0-9]+|issue[-_]?[0-9]+)(?:$|[-_])", name)
        assert (root / name / "README.md").is_file()


def test_acquisition_acceptance_is_outside_benchmark_catalog() -> None:
    assert (ROOT / "tests/acceptance/acquisition/run.py").is_file()
    assert (ROOT / "studies/repository-learning/run.py").is_file()
    assert not (ROOT / "benchmarks/typed-extraction-s1-s5").exists()
    assert not (ROOT / "benchmarks/repository-learning").exists()
