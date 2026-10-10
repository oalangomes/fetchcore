from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_coverage_policy_has_one_nonzero_project_threshold() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    report = pyproject["tool"]["coverage"]["report"]

    assert report["fail_under"] == 48
    assert report["precision"] == 2
    assert (ROOT / "pyproject.toml").read_text(encoding="utf-8").count("fail_under") == 1


def test_ci_and_makefile_do_not_override_project_coverage_threshold() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "python -m coverage report --fail-under" not in ci
    assert "$(PYTHON) -m coverage report --fail-under" not in makefile
    assert "run: python -m coverage report" in ci
    assert "\t$(PYTHON) -m coverage report\n" in makefile
