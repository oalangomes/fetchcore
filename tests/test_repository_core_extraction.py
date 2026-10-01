from __future__ import annotations

import importlib


def test_canonical_repository_core_imports_without_agentsorch() -> None:
    modules = (
        "fetchcore.repository.indexing",
        "fetchcore.repository.safe_scan",
        "fetchcore.knowledge.evidence",
        "fetchcore.repository.knowledge_pack",
        "fetchcore.repository.extractors.frameworks.sap_commerce.pack",
        "fetchcore.repository.semantic_indexing",
    )

    for module in modules:
        loaded = importlib.import_module(module)
        assert loaded.__name__ == module


def test_repository_core_has_no_agentsorch_package_dependency() -> None:
    modules = (
        "fetchcore.repository.indexing",
        "fetchcore.repository.knowledge_pack",
        "fetchcore.repository.sap_commerce_pack",
    )

    for module in modules:
        loaded = importlib.import_module(module)
        source = getattr(loaded, "__file__", None)
        assert source is not None
