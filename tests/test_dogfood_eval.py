from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dogfood_mcp_eval.py"
SPEC = importlib.util.spec_from_file_location("dogfood_mcp_eval", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
dogfood = importlib.util.module_from_spec(SPEC)
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
