"""Typed SAP Commerce Solr facet-search evidence extracted from bounded ImpEx rows.

S7 intentionally models only a small allowlist of SAP Commerce facet-search
records. Raw ImpEx rows remain non-canonical content; only exact structural
literals needed for repository knowledge are retained.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from fetchcore.repository.sap_commerce_impex import (
    SapCommerceImpexUnit,
    extract_sap_commerce_impex_units,
)

_MACRO_DEFINITION = re.compile(r"^\s*\$([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)$")
_MACRO_REFERENCE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_.-]*)$")
_SAFE_ATOM = re.compile(r"^[A-Za-z0-9_.:-]+$")
_DEFAULT_VALUE = re.compile(r"\bdefault\s*=\s*([^,\]]+)", re.IGNORECASE)
_MAX_MACRO_DEPTH = 8
_SUPPORTED_OPERATIONS = frozenset({"INSERT_UPDATE", "INSERT"})
_SUPPORTED_ITEM_TYPES = frozenset(
    {"SolrIndexedType", "SolrIndexedProperty", "SolrFacetSearchConfig"}
)


@dataclass(frozen=True)
class SapCommerceSolrIndexedTypeEvidence:
    """One SAP Commerce indexed-type declaration."""

    identifier: str
    item_type: str | None = None
    variant: bool | None = None
    default_field_value_provider: str | None = None
    identity_provider: str | None = None
    config_set: str | None = None
    index_name: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize the indexed-type declaration."""

        return {
            "identifier": self.identifier,
            "item_type": self.item_type,
            "variant": self.variant,
            "default_field_value_provider": self.default_field_value_provider,
            "identity_provider": self.identity_provider,
            "config_set": self.config_set,
            "index_name": self.index_name,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSolrIndexedTypeEvidence:
        """Hydrate one indexed-type declaration."""

        return cls(
            identifier=str(value.get("identifier", "")),
            item_type=_optional_string(value.get("item_type")),
            variant=_optional_bool(value.get("variant")),
            default_field_value_provider=_optional_string(
                value.get("default_field_value_provider")
            ),
            identity_provider=_optional_string(value.get("identity_provider")),
            config_set=_optional_string(value.get("config_set")),
            index_name=_optional_string(value.get("index_name")),
        )


@dataclass(frozen=True)
class SapCommerceSolrIndexedPropertyEvidence:
    """One SAP Commerce indexed-property declaration."""

    name: str
    indexed_type: str | None = None
    value_type: str | None = None
    sortable_type: str | None = None
    field_value_provider: str | None = None
    localized: bool | None = None
    currency: bool | None = None
    multi_value: bool | None = None
    facet: bool | None = None
    use_for_spellchecking: bool | None = None
    use_for_autocomplete: bool | None = None
    include_in_response: bool | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize the indexed-property declaration."""

        return {
            "name": self.name,
            "indexed_type": self.indexed_type,
            "value_type": self.value_type,
            "sortable_type": self.sortable_type,
            "field_value_provider": self.field_value_provider,
            "localized": self.localized,
            "currency": self.currency,
            "multi_value": self.multi_value,
            "facet": self.facet,
            "use_for_spellchecking": self.use_for_spellchecking,
            "use_for_autocomplete": self.use_for_autocomplete,
            "include_in_response": self.include_in_response,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSolrIndexedPropertyEvidence:
        """Hydrate one indexed-property declaration."""

        return cls(
            name=str(value.get("name", "")),
            indexed_type=_optional_string(value.get("indexed_type")),
            value_type=_optional_string(value.get("value_type")),
            sortable_type=_optional_string(value.get("sortable_type")),
            field_value_provider=_optional_string(value.get("field_value_provider")),
            localized=_optional_bool(value.get("localized")),
            currency=_optional_bool(value.get("currency")),
            multi_value=_optional_bool(value.get("multi_value")),
            facet=_optional_bool(value.get("facet")),
            use_for_spellchecking=_optional_bool(value.get("use_for_spellchecking")),
            use_for_autocomplete=_optional_bool(value.get("use_for_autocomplete")),
            include_in_response=_optional_bool(value.get("include_in_response")),
        )


@dataclass(frozen=True)
class SapCommerceSolrFacetSearchConfigEvidence:
    """One SAP Commerce facet-search configuration declaration."""

    name: str
    indexed_types: tuple[str, ...] = ()
    index_name_prefix: str | None = None
    search_config: str | None = None
    index_config: str | None = None
    server_config: str | None = None
    languages: tuple[str, ...] = ()
    currencies: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize the facet-search configuration."""

        return {
            "name": self.name,
            "indexed_types": list(self.indexed_types),
            "index_name_prefix": self.index_name_prefix,
            "search_config": self.search_config,
            "index_config": self.index_config,
            "server_config": self.server_config,
            "languages": list(self.languages),
            "currencies": list(self.currencies),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSolrFacetSearchConfigEvidence:
        """Hydrate one facet-search configuration."""

        return cls(
            name=str(value.get("name", "")),
            indexed_types=_strings(value.get("indexed_types")),
            index_name_prefix=_optional_string(value.get("index_name_prefix")),
            search_config=_optional_string(value.get("search_config")),
            index_config=_optional_string(value.get("index_config")),
            server_config=_optional_string(value.get("server_config")),
            languages=_strings(value.get("languages")),
            currencies=_strings(value.get("currencies")),
        )


@dataclass(frozen=True)
class SapCommerceSolrEvidence:
    """One allowlisted SAP Commerce facet-search semantic observation."""

    kind: str
    operation: str
    indexed_type: SapCommerceSolrIndexedTypeEvidence | None = None
    indexed_property: SapCommerceSolrIndexedPropertyEvidence | None = None
    facet_search_config: SapCommerceSolrFacetSearchConfigEvidence | None = None

    @property
    def symbol_name(self) -> str:
        """Return the stable declaration identity represented by this evidence."""

        if self.indexed_type is not None:
            return self.indexed_type.identifier
        if self.indexed_property is not None:
            owner = self.indexed_property.indexed_type or "unknown"
            return f"{owner}.{self.indexed_property.name}"
        if self.facet_search_config is not None:
            return self.facet_search_config.name
        return "unknown"

    def to_mapping(self) -> dict[str, object]:
        """Serialize one S7 semantic observation."""

        return {
            "kind": self.kind,
            "operation": self.operation,
            "indexed_type": (
                None if self.indexed_type is None else self.indexed_type.to_mapping()
            ),
            "indexed_property": (
                None
                if self.indexed_property is None
                else self.indexed_property.to_mapping()
            ),
            "facet_search_config": (
                None
                if self.facet_search_config is None
                else self.facet_search_config.to_mapping()
            ),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceSolrEvidence:
        """Hydrate one S7 semantic observation."""

        raw_indexed_type = value.get("indexed_type")
        raw_property = value.get("indexed_property")
        raw_config = value.get("facet_search_config")
        return cls(
            kind=str(value.get("kind", "")),
            operation=str(value.get("operation", "")),
            indexed_type=(
                SapCommerceSolrIndexedTypeEvidence.from_mapping(raw_indexed_type)
                if isinstance(raw_indexed_type, Mapping)
                else None
            ),
            indexed_property=(
                SapCommerceSolrIndexedPropertyEvidence.from_mapping(raw_property)
                if isinstance(raw_property, Mapping)
                else None
            ),
            facet_search_config=(
                SapCommerceSolrFacetSearchConfigEvidence.from_mapping(raw_config)
                if isinstance(raw_config, Mapping)
                else None
            ),
        )


@dataclass(frozen=True)
class SapCommerceSolrUnit:
    """One S7 observation with header and row provenance."""

    evidence: SapCommerceSolrEvidence
    header_line: int
    line_start: int
    line_end: int


@dataclass(frozen=True)
class _ColumnSpec:
    name: str
    default: str | None = None


@dataclass(frozen=True)
class _Header:
    unit: SapCommerceImpexUnit
    columns: tuple[_ColumnSpec, ...]


def render_sap_commerce_solr_unit(evidence: SapCommerceSolrEvidence) -> str:
    """Render only allowlisted S7 facts as safe searchable canonical content."""

    if evidence.indexed_type is not None:
        indexed_type = evidence.indexed_type
        lines = [f"SAP Commerce Solr indexed type: {indexed_type.identifier}"]
        for label, value in (
            ("item_type", indexed_type.item_type),
            ("default_field_value_provider", indexed_type.default_field_value_provider),
            ("identity_provider", indexed_type.identity_provider),
            ("config_set", indexed_type.config_set),
            ("index_name", indexed_type.index_name),
        ):
            if value is not None:
                lines.append(f"{label}: {value}")
        if indexed_type.variant is not None:
            lines.append(f"variant: {str(indexed_type.variant).lower()}")
        return "\n".join(lines) + "\n"

    if evidence.indexed_property is not None:
        indexed_property = evidence.indexed_property
        lines = [f"SAP Commerce Solr indexed property: {indexed_property.name}"]
        for label, text_value in (
            ("indexed_type", indexed_property.indexed_type),
            ("value_type", indexed_property.value_type),
            ("sortable_type", indexed_property.sortable_type),
            ("field_value_provider", indexed_property.field_value_provider),
        ):
            if text_value is not None:
                lines.append(f"{label}: {text_value}")
        for label, flag_value in (
            ("localized", indexed_property.localized),
            ("currency", indexed_property.currency),
            ("multi_value", indexed_property.multi_value),
            ("facet", indexed_property.facet),
            ("use_for_spellchecking", indexed_property.use_for_spellchecking),
            ("use_for_autocomplete", indexed_property.use_for_autocomplete),
            ("include_in_response", indexed_property.include_in_response),
        ):
            if flag_value is not None:
                lines.append(f"{label}: {str(flag_value).lower()}")
        return "\n".join(lines) + "\n"

    if evidence.facet_search_config is not None:
        facet_search_config = evidence.facet_search_config
        lines = [f"SAP Commerce Solr facet search config: {facet_search_config.name}"]
        if facet_search_config.indexed_types:
            lines.append("indexed_types: " + ", ".join(facet_search_config.indexed_types))
        for label, value in (
            ("index_name_prefix", facet_search_config.index_name_prefix),
            ("search_config", facet_search_config.search_config),
            ("index_config", facet_search_config.index_config),
            ("server_config", facet_search_config.server_config),
        ):
            if value is not None:
                lines.append(f"{label}: {value}")
        if facet_search_config.languages:
            lines.append("languages: " + ", ".join(facet_search_config.languages))
        if facet_search_config.currencies:
            lines.append("currencies: " + ", ".join(facet_search_config.currencies))
        return "\n".join(lines) + "\n"

    raise ValueError("SAP Commerce Solr evidence has no renderable v1 payload")


def extract_sap_commerce_solr_units(content: str) -> tuple[SapCommerceSolrUnit, ...]:
    """Extract allowlisted SAP Solr declarations without retaining arbitrary rows."""

    lines = content.splitlines()
    impex_units = {
        unit.line_start: unit
        for unit in extract_sap_commerce_impex_units(content)
        if unit.evidence.item_type in _SUPPORTED_ITEM_TYPES
    }
    macros: dict[str, str] = {}
    active: _Header | None = None
    result: list[SapCommerceSolrUnit] = []

    for line_number, line in enumerate(lines, start=1):
        macro = _MACRO_DEFINITION.match(line)
        if macro is not None:
            macros[macro.group(1)] = macro.group(2).strip()
            continue

        header_unit = impex_units.get(line_number)
        if header_unit is not None:
            active = _build_header(header_unit, line, macros)
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if active is None:
            continue
        if not line.lstrip().startswith(";"):
            active = None
            continue

        values = _row_values(line)
        evidence = _row_evidence(active, values, macros)
        if evidence is None:
            continue
        result.append(
            SapCommerceSolrUnit(
                evidence=evidence,
                header_line=active.unit.line_start,
                line_start=line_number,
                line_end=line_number,
            )
        )
    return tuple(result)


def _build_header(
    unit: SapCommerceImpexUnit,
    raw_line: str,
    macros: Mapping[str, str],
) -> _Header | None:
    if unit.evidence.operation not in _SUPPORTED_OPERATIONS:
        return None
    columns = unit.evidence.columns
    raw_fields = _expand_header_fields(
        tuple(_split_top_level(raw_line, ";"))[1:],
        macros,
        stack=(),
    )
    while raw_fields and not raw_fields[-1].strip():
        raw_fields = raw_fields[:-1]
    if len(raw_fields) != len(columns):
        return None

    specs = tuple(
        _ColumnSpec(
            name=column.name.casefold(),
            default=_default_literal(raw.strip(), macros),
        )
        for column, raw in zip(columns, raw_fields, strict=True)
    )
    return _Header(unit=unit, columns=specs)


def _expand_header_fields(
    fields: tuple[str, ...],
    macros: Mapping[str, str],
    *,
    stack: tuple[str, ...],
) -> tuple[str, ...]:
    """Expand whole-field header macros while preserving raw modifier defaults."""

    expanded: list[str] = []
    for raw_field in fields:
        field = raw_field.strip()
        macro = _MACRO_REFERENCE.fullmatch(field)
        if macro is None:
            expanded.append(raw_field)
            continue

        name = macro.group(1)
        if name in stack or len(stack) >= _MAX_MACRO_DEPTH:
            expanded.append(raw_field)
            continue
        raw_value = macros.get(name)
        if raw_value is None:
            expanded.append(raw_field)
            continue

        nested = tuple(_split_top_level(raw_value.rstrip(";"), ";"))
        expanded.extend(
            _expand_header_fields(
                nested,
                macros,
                stack=(*stack, name),
            )
        )
    return tuple(expanded)


def _row_values(line: str) -> tuple[str, ...]:
    fields = list(_split_top_level(line, ";"))
    if fields and not fields[0].strip():
        fields = fields[1:]
    while fields and not fields[-1].strip():
        fields.pop()
    return tuple(field.strip() for field in fields)


def _row_evidence(
    header: _Header,
    values: tuple[str, ...],
    macros: Mapping[str, str],
) -> SapCommerceSolrEvidence | None:
    if len(values) > len(header.columns):
        if any(value.strip() for value in values[len(header.columns) :]):
            return None
        values = values[: len(header.columns)]
    cells: dict[str, str | None] = {}
    for index, column in enumerate(header.columns):
        raw = values[index] if index < len(values) else ""
        cells[column.name] = _cell_value(raw, column.default, macros)

    item_type = header.unit.evidence.item_type
    operation = header.unit.evidence.operation
    if item_type == "SolrIndexedType":
        identifier = _atom(cells.get("identifier"))
        if identifier is None:
            return None
        return SapCommerceSolrEvidence(
            kind="indexed-type",
            operation=operation,
            indexed_type=SapCommerceSolrIndexedTypeEvidence(
                identifier=identifier,
                item_type=_atom(cells.get("type")),
                variant=_bool(cells.get("variant")),
                default_field_value_provider=_atom(
                    cells.get("defaultfieldvalueprovider")
                ),
                identity_provider=_atom(cells.get("identityprovider")),
                config_set=_atom(cells.get("configset")),
                index_name=_atom(cells.get("indexname")),
            ),
        )

    if item_type == "SolrIndexedProperty":
        name = _atom(cells.get("name"))
        if name is None:
            return None
        return SapCommerceSolrEvidence(
            kind="indexed-property",
            operation=operation,
            indexed_property=SapCommerceSolrIndexedPropertyEvidence(
                name=name,
                indexed_type=_atom(cells.get("solrindexedtype")),
                value_type=_atom(cells.get("type")),
                sortable_type=_atom(cells.get("sortabletype")),
                field_value_provider=_atom(cells.get("fieldvalueprovider")),
                localized=_bool(cells.get("localized")),
                currency=_bool(cells.get("currency")),
                multi_value=_bool(cells.get("multivalue")),
                facet=_bool(cells.get("facet")),
                use_for_spellchecking=_bool(cells.get("useforspellchecking")),
                use_for_autocomplete=_bool(cells.get("useforautocomplete")),
                include_in_response=_bool(cells.get("includeinresponse")),
            ),
        )

    if item_type == "SolrFacetSearchConfig":
        name = _atom(cells.get("name"))
        if name is None:
            return None
        return SapCommerceSolrEvidence(
            kind="facet-search-config",
            operation=operation,
            facet_search_config=SapCommerceSolrFacetSearchConfigEvidence(
                name=name,
                indexed_types=_atom_list(cells.get("solrindexedtypes")),
                index_name_prefix=_atom(cells.get("indexnameprefix")),
                search_config=_atom(cells.get("solrsearchconfig")),
                index_config=_atom(cells.get("solrindexconfig")),
                server_config=_atom(cells.get("solrserverconfig")),
                languages=_atom_list(cells.get("languages")),
                currencies=_atom_list(cells.get("currencies")),
            ),
        )
    return None


def _cell_value(
    raw: str,
    default: str | None,
    macros: Mapping[str, str],
) -> str | None:
    value = raw.strip() or (default or "")
    if not value:
        return None
    return _resolve_value(value, macros, stack=())


def _resolve_value(
    value: str,
    macros: Mapping[str, str],
    *,
    stack: tuple[str, ...],
) -> str | None:
    stripped = value.strip().rstrip(";").strip()
    macro = _MACRO_REFERENCE.fullmatch(stripped)
    if macro is not None:
        name = macro.group(1)
        if name in stack or len(stack) >= _MAX_MACRO_DEPTH:
            return None
        raw = macros.get(name)
        if raw is None:
            return None
        return _resolve_value(raw, macros, stack=(*stack, name))
    if (
        len(stripped) >= 2
        and stripped[0] == stripped[-1]
        and stripped[0] in {"'", '"'}
    ):
        stripped = stripped[1:-1].strip()
    parts = tuple(item.strip() for item in stripped.split(",") if item.strip())
    if not parts or any(_SAFE_ATOM.fullmatch(item) is None for item in parts):
        return None
    return ",".join(parts)


def _default_literal(raw: str, macros: Mapping[str, str]) -> str | None:
    match = _DEFAULT_VALUE.search(raw)
    if match is None:
        return None
    return _resolve_value(match.group(1).strip(), macros, stack=())


def _atom(value: str | None) -> str | None:
    if value is None or _SAFE_ATOM.fullmatch(value) is None:
        return None
    return value


def _atom_list(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if any(_SAFE_ATOM.fullmatch(item) is None for item in items):
        return ()
    return tuple(dict.fromkeys(items))


def _bool(value: str | None) -> bool | None:
    if value is None:
        return None
    folded = value.casefold()
    if folded == "true":
        return True
    if folded == "false":
        return False
    return None


def _split_top_level(value: str, separator: str) -> tuple[str, ...]:
    fields: list[str] = []
    current: list[str] = []
    square_depth = 0
    paren_depth = 0
    quote: str | None = None
    for character in value:
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            current.append(character)
            continue
        if character == "[":
            square_depth += 1
            current.append(character)
            continue
        if character == "]":
            square_depth = max(0, square_depth - 1)
            current.append(character)
            continue
        if character == "(":
            paren_depth += 1
            current.append(character)
            continue
        if character == ")":
            paren_depth = max(0, paren_depth - 1)
            current.append(character)
            continue
        if character == separator and square_depth == 0 and paren_depth == 0:
            fields.append("".join(current))
            current = []
            continue
        current.append(character)
    fields.append("".join(current))
    return tuple(fields)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


__all__ = [
    "SapCommerceSolrEvidence",
    "SapCommerceSolrFacetSearchConfigEvidence",
    "SapCommerceSolrIndexedPropertyEvidence",
    "SapCommerceSolrIndexedTypeEvidence",
    "SapCommerceSolrUnit",
    "extract_sap_commerce_solr_units",
    "render_sap_commerce_solr_unit",
]
