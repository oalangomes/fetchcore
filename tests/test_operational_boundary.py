from __future__ import annotations

import ast
from pathlib import Path


def test_operational_modules_do_not_import_agentsorch() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "fetchcore" / "repository"
    for relative in (
        "index_contract.py",
        "index_operations.py",
        "incremental.py",
        "incremental_persistence.py",
        "document_incremental.py",
        "orchestration.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            name == "agentsorch" or name.startswith("agentsorch.")
            for name in imported
        ), relative
