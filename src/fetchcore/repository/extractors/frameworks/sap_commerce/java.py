"""Typed SAP Commerce Java role and extension-point evidence.

The v1 surface intentionally models only SAP-specific Java role specialization.
Generic architectural roles and HTTP routes remain authoritative in the existing
repository contracts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from fetchcore.knowledge.evidence import SymbolEvidence


SAP_COMMERCE_OCC_CONTROLLER_ROLE = "occ-controller"
SAP_COMMERCE_JAVA_ROLE_SIGNALS = (
    "api-version",
    "request-mapping-override",
    "site-channel-restriction",
)

SAP_COMMERCE_JAVA_EXTENSION_CONTRACTS = {
    "de.hybris.platform.converters.Populator": "populator",
    "de.hybris.platform.servicelayer.dto.converter.Converter": "converter",
    "de.hybris.platform.servicelayer.interceptor.ValidateInterceptor": "validate-interceptor",
    "de.hybris.platform.servicelayer.interceptor.PrepareInterceptor": "prepare-interceptor",
    "de.hybris.platform.servicelayer.interceptor.InitDefaultsInterceptor": "init-defaults-interceptor",
    "de.hybris.platform.servicelayer.interceptor.RemoveInterceptor": "remove-interceptor",
    "de.hybris.platform.servicelayer.interceptor.LoadInterceptor": "load-interceptor",
    "de.hybris.platform.servicelayer.event.impl.AbstractEventListener": "event-listener",
}


@dataclass(frozen=True)
class SapCommerceJavaRoleEvidence:
    """One SAP-specific Java role specialization proven from canonical evidence."""

    symbol: str
    role: str
    signals: tuple[str, ...]
    api_version: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize one SAP Commerce Java role observation."""

        return {
            "symbol": self.symbol,
            "role": self.role,
            "signals": list(self.signals),
            "api_version": self.api_version,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceJavaRoleEvidence:
        """Hydrate one SAP Commerce Java role observation."""

        raw_signals = value.get("signals")
        signals = (
            tuple(item for item in raw_signals if isinstance(item, str))
            if isinstance(raw_signals, (list, tuple))
            else ()
        )
        raw_version = value.get("api_version")
        api_version = raw_version if isinstance(raw_version, str) else None
        return cls(
            symbol=str(value.get("symbol", "")),
            role=str(value.get("role", "")),
            signals=signals,
            api_version=api_version,
        )


@dataclass(frozen=True)
class SapCommerceJavaRoleUnit:
    """One SAP Java role observation with exact declaration-line provenance."""

    evidence: SapCommerceJavaRoleEvidence
    line_start: int
    line_end: int


@dataclass(frozen=True)
class SapCommerceJavaExtensionPointEvidence:
    """One directly declared SAP Commerce Java extension point."""

    symbol: str
    role: str
    contract: str
    declaration: str
    type_arguments: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        """Serialize the exact extension-point declaration."""

        return {
            "symbol": self.symbol,
            "role": self.role,
            "contract": self.contract,
            "declaration": self.declaration,
            "type_arguments": list(self.type_arguments),
        }


@dataclass(frozen=True)
class SapCommerceJavaExtensionPointUnit:
    """One extension point with source declaration-line provenance."""

    evidence: SapCommerceJavaExtensionPointEvidence
    line_start: int
    line_end: int


def extract_sap_commerce_java_extension_points(
    *,
    symbol: SymbolEvidence,
    imports: tuple[str, ...],
) -> tuple[SapCommerceJavaExtensionPointEvidence, ...]:
    """Extract directly declared allowlisted contracts from Java evidence."""

    if symbol.kind not in {"class", "interface"} or not symbol.signature:
        return ()
    clauses = _java_declaration_clauses(symbol.signature)
    if clauses is None:
        return ()
    points: list[SapCommerceJavaExtensionPointEvidence] = []
    for declaration, raw_type in clauses:
        parsed = _java_type_reference(raw_type)
        if parsed is None:
            continue
        type_name, type_arguments = parsed
        contract = _resolve_sap_contract(type_name, imports)
        if (
            contract is None
            or not _supports_direct_declaration(
                symbol_kind=symbol.kind,
                contract=contract,
                declaration=declaration,
            )
            or not _supports_contract_arity(contract, type_arguments)
        ):
            continue
        points.append(
            SapCommerceJavaExtensionPointEvidence(
                symbol=symbol.qualified_name or symbol.name,
                role=SAP_COMMERCE_JAVA_EXTENSION_CONTRACTS[contract],
                contract=contract,
                declaration=declaration,
                type_arguments=type_arguments,
            )
        )
    return tuple(points)


def _java_declaration_clauses(
    signature: str,
) -> tuple[tuple[str, str], ...] | None:
    if not _balanced_java_angles(signature):
        return None
    match = next(
        (
            candidate
            for candidate in re.finditer(r"\b(implements|extends)\b", signature)
            if _balanced_java_angles(signature[: candidate.start()])
        ),
        None,
    )
    if match is None:
        return ()
    tail = signature[match.start() :].strip()
    clauses: list[tuple[str, str]] = []
    index = 0
    while index < len(tail):
        keyword_match = re.match(r"(implements|extends)\b", tail[index:])
        if keyword_match is None:
            return None
        declaration = keyword_match.group(1)
        index += keyword_match.end()
        end = _java_clause_end(tail, index)
        if end is None:
            return None
        for item in _split_java_types(tail[index:end]):
            if not item:
                return None
            clauses.append((declaration, item))
        index = end
    return tuple(clauses)


def _java_clause_end(value: str, start: int) -> int | None:
    remainder = value[start:]
    for match in re.finditer(r"\s+(implements|extends)\s+", remainder):
        prefix = remainder[: match.start()]
        if _balanced_java_angles(prefix):
            return start + match.start(1)
    return len(value) if _balanced_java_angles(remainder) else None


def _balanced_java_angles(value: str) -> bool:
    depth = 0
    for character in value:
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _split_java_types(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth < 0:
                return ()
        elif character == "," and depth == 0:
            parts.append(" ".join(value[start:index].split()))
            start = index + 1
    if depth != 0:
        return ()
    parts.append(" ".join(value[start:].split()))
    return tuple(parts)


def _java_type_reference(value: str) -> tuple[str, tuple[str, ...]] | None:
    value = " ".join(value.split())
    if not value:
        return None
    opening = value.find("<")
    if opening < 0:
        return value, ()
    if not value.endswith(">"):
        return None
    base = value[:opening].strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", base):
        return None
    arguments = value[opening + 1 : -1]
    parsed = _split_java_types(arguments)
    if not parsed or any(not item for item in parsed):
        return None
    return base, parsed


def is_well_formed_java_type_argument(value: str) -> bool:
    """Return whether one preserved generic argument has balanced delimiters."""

    normalized = " ".join(value.split())
    if not normalized or normalized != value:
        return False
    if _split_java_types(normalized) != (normalized,):
        return False
    angle = square = 0
    for character in normalized:
        if character == "<":
            angle += 1
        elif character == ">":
            angle -= 1
        elif character == "[":
            square += 1
        elif character == "]":
            square -= 1
        if angle < 0 or square < 0:
            return False
    return angle == 0 and square == 0


def _resolve_sap_contract(type_name: str, imports: tuple[str, ...]) -> str | None:
    if type_name in SAP_COMMERCE_JAVA_EXTENSION_CONTRACTS:
        return type_name
    for contract in SAP_COMMERCE_JAVA_EXTENSION_CONTRACTS:
        simple = contract.rsplit(".", 1)[-1]
        package_wildcard = contract.rsplit(".", 1)[0] + ".*"
        if type_name == simple and (contract in imports or package_wildcard in imports):
            return contract
    return None


def _supports_direct_declaration(
    *,
    symbol_kind: str,
    contract: str,
    declaration: str,
) -> bool:
    event_listener = (
        "de.hybris.platform.servicelayer.event.impl.AbstractEventListener"
    )
    if symbol_kind == "interface":
        return declaration == "extends" and contract != event_listener
    if symbol_kind == "class":
        expected = "extends" if contract == event_listener else "implements"
        return declaration == expected
    return False


def _supports_contract_arity(
    contract: str,
    type_arguments: tuple[str, ...],
) -> bool:
    role = SAP_COMMERCE_JAVA_EXTENSION_CONTRACTS[contract]
    expected = 2 if role in {"populator", "converter"} else 1
    return len(type_arguments) in {0, expected}


# Persisted knowledge-pack fingerprints retain historical class paths.
SapCommerceJavaRoleEvidence.__module__ = "fetchcore.repository.sap_commerce_java"
SapCommerceJavaRoleUnit.__module__ = "fetchcore.repository.sap_commerce_java"
SapCommerceJavaExtensionPointEvidence.__module__ = "fetchcore.repository.sap_commerce_java"
SapCommerceJavaExtensionPointUnit.__module__ = "fetchcore.repository.sap_commerce_java"
