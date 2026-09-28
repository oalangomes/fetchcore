"""Path-derived SAP Commerce ``hybris/config/customize`` overlay evidence.

The customization directory mirrors targets inside ``HYBRIS_BIN_DIR``. This module
records only the observed target path and path structure; it does not resolve the
target to a concrete SAP product baseline, extension pack, or repository source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath


SAP_COMMERCE_OVERLAY_METADATA_KEY = "sap_commerce_overlay"
_CUSTOMIZE_MARKER = ("hybris", "config", "customize")


@dataclass(frozen=True)
class SapCommerceOverlayEvidence:
    """One source file copied into the corresponding SAP Commerce bin target."""

    target_bin_path: str
    target_tree: str
    target_kind: str
    target_segments: tuple[str, ...]
    module_group: str | None = None
    module_extension: str | None = None
    operation: str = "copy-to-bin"

    def to_mapping(self) -> dict[str, object]:
        """Serialize path-derived overlay evidence."""

        return {
            "target_bin_path": self.target_bin_path,
            "target_tree": self.target_tree,
            "target_kind": self.target_kind,
            "target_segments": list(self.target_segments),
            "module_group": self.module_group,
            "module_extension": self.module_extension,
            "operation": self.operation,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceOverlayEvidence:
        """Hydrate path-derived overlay evidence from a canonical mapping."""

        return cls(
            target_bin_path=str(value.get("target_bin_path", "")),
            target_tree=str(value.get("target_tree", "")),
            target_kind=str(value.get("target_kind", "unknown")),
            target_segments=_strings(value.get("target_segments")),
            module_group=_optional_string(value.get("module_group")),
            module_extension=_optional_string(value.get("module_extension")),
            operation=str(value.get("operation", "copy-to-bin")),
        )


def extract_sap_commerce_overlay_evidence(path: str) -> SapCommerceOverlayEvidence | None:
    """Return path-derived overlay evidence for canonical config/customize sources."""

    parts = PurePosixPath(path).parts
    lowered = tuple(item.casefold() for item in parts)
    marker_index = _find_marker(lowered, _CUSTOMIZE_MARKER)
    if marker_index is None:
        return None
    target_segments = tuple(parts[marker_index + len(_CUSTOMIZE_MARKER) :])
    if len(target_segments) < 2:
        return None

    target_tree = target_segments[0]
    target_kind = _target_kind(target_tree)
    module_group: str | None = None
    module_extension: str | None = None
    if target_tree.casefold() == "modules" and len(target_segments) >= 4:
        module_group = target_segments[1]
        module_extension = target_segments[2]

    return SapCommerceOverlayEvidence(
        target_bin_path="/".join(target_segments),
        target_tree=target_tree,
        target_kind=target_kind,
        target_segments=target_segments,
        module_group=module_group,
        module_extension=module_extension,
    )


def _target_kind(target_tree: str) -> str:
    normalized = target_tree.casefold()
    if normalized == "modules":
        return "module-tree"
    if normalized == "custom":
        return "custom-tree"
    if normalized == "platform":
        return "platform-tree"
    return "bin-tree"


def _find_marker(parts: tuple[str, ...], marker: tuple[str, ...]) -> int | None:
    limit = len(parts) - len(marker) + 1
    for index in range(max(0, limit)):
        if parts[index : index + len(marker)] == marker:
            return index
    return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))
