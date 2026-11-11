"""Exact Projection v3 SolrCloud schema validation."""

from __future__ import annotations

from pathlib import Path
from typing import cast
import xml.etree.ElementTree as ET

from fetchcore.retrieval.contracts import KnowledgeSearchError


_SCHEMA_PATH = (
    Path(__file__).parents[3] / "operational" / "configset" / "conf" / "schema.xml"
)


def validate_projection_v3_schema(schema: object) -> None:
    """Require the complete live Solr schema to match the packaged configset."""
    if not isinstance(schema, dict):
        raise KnowledgeSearchError(
            "SolrCloud collection returned no schema object",
            category="schema_mismatch",
        )
    try:
        expected_schema = ET.parse(_SCHEMA_PATH).getroot()
    except (OSError, ET.ParseError) as exc:
        raise KnowledgeSearchError(
            "Packaged Solr Projection v3 schema is unavailable",
            category="schema_mismatch",
        ) from exc
    actual = cast(dict[str, object], schema)
    if (
        actual.get("name") != expected_schema.get("name")
        or str(actual.get("version")) != expected_schema.get("version")
        or actual.get("uniqueKey") != expected_schema.findtext("uniqueKey")
    ):
        raise KnowledgeSearchError(
            "SolrCloud collection schema identity is incompatible",
            category="schema_mismatch",
        )

    def compare_elements(
        expected_elements: list[ET.Element],
        actual_value: object,
        label: str,
        *,
        compare_analyzers: bool = False,
    ) -> None:
        if not isinstance(actual_value, list) or not all(
            isinstance(item, dict) for item in actual_value
        ):
            raise KnowledgeSearchError(
                f"SolrCloud collection returned invalid {label}",
                category="schema_mismatch",
            )
        expected = {item.get("name"): item for item in expected_elements}
        observed_items = cast(list[dict[str, object]], actual_value)
        observed = {item.get("name"): item for item in observed_items}
        if len(observed) != len(observed_items) or set(observed) != set(expected):
            raise KnowledgeSearchError(
                f"SolrCloud collection {label} differ from Projection v3",
                category="schema_mismatch",
            )
        for name, element in expected.items():
            definition = observed[name]
            for key, value in element.attrib.items():
                if definition.get(key) != _schema_value(value):
                    raise KnowledgeSearchError(
                        f"SolrCloud collection {label} definition is incompatible: {name}",
                        category="schema_mismatch",
                    )
            if compare_analyzers:
                for analyzer in element.findall("analyzer"):
                    analyzer_type = analyzer.get("type")
                    if analyzer_type not in {"index", "query"}:
                        raise KnowledgeSearchError(
                            "Projection v3 contains an unknown analyzer type",
                            category="schema_mismatch",
                        )
                    analyzer_key = f"{analyzer_type}Analyzer"
                    if definition.get(analyzer_key) != _expected_analyzer(analyzer):
                        raise KnowledgeSearchError(
                            f"SolrCloud collection field type is incompatible: {name}",
                            category="schema_mismatch",
                        )

    compare_elements(expected_schema.findall("field"), actual.get("fields"), "fields")
    compare_elements(
        expected_schema.findall("fieldType"),
        actual.get("fieldTypes"),
        "fieldTypes",
        compare_analyzers=True,
    )
    compare_elements(
        expected_schema.findall("dynamicField"),
        actual.get("dynamicFields"),
        "dynamicFields",
    )
    copy_fields = actual.get("copyFields")
    expected_copy_fields = expected_schema.findall("copyField")
    if (
        not isinstance(copy_fields, list)
        or len(copy_fields) != len(expected_copy_fields)
        or any(
            not isinstance(item, dict) or item != element.attrib
            for item, element in zip(copy_fields, expected_copy_fields, strict=True)
        )
    ):
        raise KnowledgeSearchError(
            "SolrCloud collection copyFields differ from Projection v3",
            category="schema_mismatch",
        )

    expected_similarity = expected_schema.find("similarity")
    actual_similarity = actual.get("similarity")
    if expected_similarity is None or not isinstance(actual_similarity, dict):
        raise KnowledgeSearchError(
            "SolrCloud collection similarity configuration is invalid",
            category="schema_mismatch",
        )
    if actual_similarity.get("class") != expected_similarity.get("class"):
        raise KnowledgeSearchError(
            "SolrCloud collection similarity is incompatible",
            category="schema_mismatch",
        )
    for parameter in expected_similarity:
        if actual_similarity.get(parameter.get("name", "")) != (parameter.text or "").strip():
            raise KnowledgeSearchError(
                "SolrCloud collection similarity parameters are incompatible",
                category="schema_mismatch",
            )


def _schema_value(value: str) -> object:
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _expected_analyzer(analyzer: ET.Element) -> dict[str, object]:
    result: dict[str, object] = {}
    tokenizer = analyzer.find("tokenizer")
    if tokenizer is not None:
        result["tokenizer"] = dict(tokenizer.attrib)
    for tag, key in (("filter", "filters"), ("charFilter", "charFilters")):
        entries = [dict(element.attrib) for element in analyzer.findall(tag)]
        if entries:
            result[key] = entries
    return result
