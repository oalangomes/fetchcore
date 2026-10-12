"""Versioned backend-neutral API facts for future sanitized source interpreters.

This module does not parse API files or authorize raw source admission. Facts are
created only after format-specific extractors prove and sanitize input in S2/S3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from fetchcore.repository.indexing import KnowledgeDocument
from pathlib import PurePosixPath
from typing import Literal

from fetchcore.repository.semantic import SemanticEvidenceEnvelope

ApiFactKind = Literal["api_operation", "api_request", "api_schema", "api_reference"]
ApiFormat = Literal["openapi", "swagger", "postman_collection", "postman_v3"]
_KINDS = frozenset({"api_operation", "api_request", "api_schema", "api_reference"})
_FORMATS = frozenset({"openapi", "swagger", "postman_collection", "postman_v3"})
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"})
_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_POINTER_SEGMENT = re.compile(r"^(?:[^~/]|~[01])*$")
_PAYLOAD_KEYS = frozenset({
    "format", "format_version", "path", "pointer", "line_start", "line_end",
    "method", "route", "name", "target",
})


def _nonempty(value: object, field: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or _CONTROL.search(value):
        raise ValueError(f"API evidence {field} is invalid")
    return value


def _optional(value: object, field: str, *, max_length: int = 512) -> str | None:
    if value is None:
        return None
    return _nonempty(value, field, max_length=max_length)


def _pointer(value: str) -> str:
    """Accept RFC 6901 JSON Pointers; never substitute an invented line span."""
    if value != "#" and not (
        value.startswith("#/")
        and all(_POINTER_SEGMENT.fullmatch(x) for x in value[2:].split("/"))
    ):
        raise ValueError("API evidence pointer is invalid")
    return value


@dataclass(frozen=True)
class ApiSourceLocatorV1:
    """Source-local locator; revision/checksum are owned by KnowledgeDocument."""

    path: str
    pointer: str
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        path = _nonempty(self.path, "path", max_length=2048)
        parsed = PurePosixPath(path)
        if (parsed.is_absolute() or parsed.as_posix() != path or ".." in parsed.parts
                or "\\" in path or ":" in path or path.startswith("./")):
            raise ValueError("API evidence path escapes repository")
        _pointer(_nonempty(self.pointer, "pointer", max_length=2048))
        if self.line_start is None and self.line_end is not None:
            raise ValueError("API evidence line_end requires line_start")
        if self.line_start is not None and (type(self.line_start) is not int
                                            or self.line_start < 1):
            raise ValueError("API evidence line_start is invalid")
        if self.line_end is not None and (type(self.line_end) is not int
                                          or self.line_end < (self.line_start or 1)):
            raise ValueError("API evidence line_end is invalid")


@dataclass(frozen=True)
class ApiEvidenceV1:
    """A single strictly shaped fact, not consumer authority or source truth."""

    kind: ApiFactKind
    format: ApiFormat
    format_version: str
    locator: ApiSourceLocatorV1
    method: str | None = None
    route: str | None = None
    name: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError("API evidence kind is unsupported")
        if self.format not in _FORMATS:
            raise ValueError("API evidence format is unsupported")
        if _VERSION.fullmatch(_nonempty(self.format_version, "format_version")) is None:
            raise ValueError("API evidence format_version is invalid")
        supported = (
            bool(re.fullmatch(r"3\.(?:0|1)\.\d+", self.format_version))
            if self.format == "openapi"
            else self.format_version == {
                "swagger": "2.0",
                "postman_collection": "2.1.0",
                "postman_v3": "3.0.0",
            }.get(self.format)
        )
        if not supported:
            raise ValueError("API evidence format/version is incompatible")
        if not isinstance(self.locator, ApiSourceLocatorV1):
            raise ValueError("API evidence locator is invalid")
        method = _optional(self.method, "method", max_length=12)
        if method is not None and method not in _METHODS:
            raise ValueError("API evidence method is unsupported")
        route = _optional(self.route, "route", max_length=2048)
        if route is not None and (not route.startswith("/") or "?" in route
                                   or "#" in route or "://" in route or "@" in route):
            raise ValueError("API evidence route is not a sanitized path")
        _optional(self.name, "name", max_length=256)
        target = _optional(self.target, "target", max_length=2048)
        if target is not None:
            # S1 stores only safe local reference declarations, never remote URLs.
            if "://" in target or target.startswith(("/", "\\")):
                raise ValueError("API evidence reference must be repository-local")
            reference_path, _, pointer = target.partition("#")
            if reference_path:
                path = PurePosixPath(reference_path)
                if (".." in path.parts or "\\" in reference_path
                        or ":" in reference_path or path.as_posix() != reference_path):
                    raise ValueError("API evidence reference path is invalid")
            if "#" in target:
                _pointer("#" + pointer)
        if self.kind in {"api_operation", "api_request"} and (method is None or route is None):
            raise ValueError("API operation/request requires method and route")
        if self.kind == "api_schema" and self.name is None:
            raise ValueError("API schema requires a name")
        if self.kind == "api_reference" and target is None:
            raise ValueError("API reference requires a target")

    @property
    def discriminator(self) -> str:
        """Stable source-local identity; repeated names cannot collide."""
        return f"{self.kind}:{self.locator.pointer}"

    def to_payload(self) -> dict[str, object]:
        """Closed, bounded schema; raw bodies, tokens and examples have no slot."""
        return {
            "format": self.format,
            "format_version": self.format_version,
            "path": self.locator.path,
            "pointer": self.locator.pointer,
            "line_start": self.locator.line_start,
            "line_end": self.locator.line_end,
            "method": self.method,
            "route": self.route,
            "name": self.name,
            "target": self.target,
        }

    def to_envelope(self) -> SemanticEvidenceEnvelope:
        return SemanticEvidenceEnvelope.from_payload(
            namespace="api_contract", kind=self.kind, version="1",
            payload=self.to_payload(),
        )

    @classmethod
    def from_envelope(cls, value: SemanticEvidenceEnvelope) -> ApiEvidenceV1:
        if (value.namespace != "api_contract" or value.version != "1"
                or value.kind not in _KINDS):
            raise ValueError("API evidence envelope schema is incompatible")
        payload = value.payload()
        if set(payload) != _PAYLOAD_KEYS:
            raise ValueError("API evidence payload has missing or unknown fields")
        path = _nonempty(payload["path"], "path", max_length=2048)
        pointer = _nonempty(payload["pointer"], "pointer", max_length=2048)
        return cls(
            kind=value.kind,  # type: ignore[arg-type]
            format=_nonempty(payload["format"], "format"),  # type: ignore[arg-type]
            format_version=_nonempty(payload["format_version"], "format_version"),
            locator=ApiSourceLocatorV1(
                path=path, pointer=pointer,
                line_start=_optional_line(payload["line_start"]),
                line_end=_optional_line(payload["line_end"]),
            ),
            method=_optional(payload["method"], "method", max_length=12),
            route=_optional(payload["route"], "route", max_length=2048),
            name=_optional(payload["name"], "name", max_length=256),
            target=_optional(payload["target"], "target", max_length=2048),
        )


def _optional_line(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("API evidence line value is invalid")
    return value



def attach_api_evidence(document: KnowledgeDocument, observation: ApiEvidenceV1) -> KnowledgeDocument:
    """Enrich canonical evidence only; never change source identity or content.

    Intended for a future sanitizing KnowledgeDocumentEnricher. This method does
    not read raw source, grant API-file admission or infer implementation authority.
    """

    if (document.path != observation.locator.path
            or document.provenance.path != observation.locator.path):
        raise ValueError("API evidence locator does not match canonical source provenance")
    if observation.locator.line_start is not None:
        if (observation.locator.line_start < document.line_start
                or (observation.locator.line_end or observation.locator.line_start)
                > document.line_end):
            raise ValueError("API evidence span lies outside canonical source unit")
    envelope = observation.to_envelope()
    for prior in document.evidence.semantic:
        if (prior.namespace == "api_contract" and prior.kind == envelope.kind
                and prior.version == envelope.version
                and prior.payload().get("pointer") == observation.locator.pointer):
            raise ValueError("duplicate source-local API evidence identity")
    evidence = replace(
        document.evidence,
        semantic=(*document.evidence.semantic, envelope),
    )
    return replace(document, evidence=evidence)
