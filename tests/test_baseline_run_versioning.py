from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "benchmarks" / "baseline-v1" / "run.py"
SPEC = importlib.util.spec_from_file_location("fetchcore_baseline_v1_run", RUN_PATH)
assert SPEC is not None
assert SPEC.loader is not None
RUN_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN_MODULE)


def test_allocate_run_output_dir_advances_monotonically(tmp_path: Path) -> None:
    (tmp_path / "V0.3").mkdir()
    (tmp_path / "V0.invalid").mkdir()
    (tmp_path / "legacy.json").write_text("{}", encoding="utf-8")

    version, output = RUN_MODULE._allocate_run_output_dir(tmp_path)

    assert version == "V0.4"
    assert output == tmp_path / "V0.4"
    assert output.is_dir()

    next_version, next_output = RUN_MODULE._allocate_run_output_dir(tmp_path)

    assert next_version == "V0.5"
    assert next_output == tmp_path / "V0.5"
    assert next_output.is_dir()
