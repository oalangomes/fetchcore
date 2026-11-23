"""Exact live schema check for the separate GraphProjection v1 configset."""

from __future__ import annotations

from pathlib import Path
from typing import cast
import xml.etree.ElementTree as ET

from fetchcore.retrieval.contracts import KnowledgeSearchError


_SCHEMA_PATH = (
    Path(__file__).parents[3] / "operational" / "graph_configset" / "conf" / "schema.xml"
)


def validate_graph_schema(value: object) -> None:
    if not isinstance(value, dict):
        raise KnowledgeSearchError("graph schema is unavailable", category="schema_mismatch")
    try:
        expected = ET.parse(_SCHEMA_PATH).getroot()
    except (OSError, ET.ParseError) as exc:
        raise KnowledgeSearchError("packaged graph schema is unavailable",
                                   category="schema_mismatch") from exc
    actual = cast(dict[str, object], value)
    if (actual.get("name") != expected.get("name")
            or str(actual.get("version")) != expected.get("version")
            or actual.get("uniqueKey") != expected.findtext("uniqueKey")):
        raise KnowledgeSearchError("graph schema identity is incompatible",
                                   category="schema_mismatch")
    for tag, key in (("field", "fields"), ("fieldType", "fieldTypes"),
                     ("dynamicField", "dynamicFields")):
        raw = actual.get(key)
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise KnowledgeSearchError(f"graph schema {key} are invalid",
                                       category="schema_mismatch")
        observed = {item.get("name"): item for item in raw}
        declared = {item.get("name"): item for item in expected.findall(tag)}
        if len(observed) != len(raw) or set(observed) != set(declared):
            raise KnowledgeSearchError(f"graph schema {key} differ",
                                       category="schema_mismatch")
        for name, element in declared.items():
            for attr, text in element.attrib.items():
                parsed: object = (text == "true" if text in {"true", "false"} else text)
                if observed[name].get(attr) != parsed:
                    raise KnowledgeSearchError(
                        f"graph schema {key} definition is incompatible: {name}",
                        category="schema_mismatch",
                    )
    if actual.get("copyFields") != []:
        raise KnowledgeSearchError("graph schema has unexpected copy fields",
                                   category="schema_mismatch")
