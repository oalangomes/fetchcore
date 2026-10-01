"""Fail-closed SAP Commerce workspace detection without reading sensitive config."""

from __future__ import annotations

import json
from pathlib import Path


_MAX_MANIFEST_BYTES = 1_000_000


def is_sap_commerce_workspace(workspace: Path) -> bool:
    """Return whether explicit workspace evidence identifies SAP Commerce.

    Detection intentionally uses only product manifest/config path signatures and
    never opens `local.properties`, keystores, keys, or other sensitive inputs.
    """

    root = workspace.resolve(strict=True)
    manifest = root / "manifest.json"
    if _manifest_declares_sap_commerce(manifest):
        return True

    config = root / "hybris" / "config"
    if (config / "localextensions.xml").is_file():
        return True
    if (config / "cloudextensions.xml").is_file():
        return True

    return (root / "hybris" / "bin" / "custom").is_dir()


def _manifest_declares_sap_commerce(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            return False
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(decoded, dict)
        and isinstance(decoded.get("commerceSuiteVersion"), str)
        and bool(decoded["commerceSuiteVersion"].strip())
    )
