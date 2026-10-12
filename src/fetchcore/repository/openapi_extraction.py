"""Conservative OpenAPI/Swagger acquisition into *sanitized* source-local API units.

Never return parsed raw trees, free-text descriptions, examples, credentials,
request bodies, server URLs or remote reference targets. All outputs are a
closed allowlist of structural facts with bounded size. This is NOT a complete
OpenAPI validator and MUST only be used through the opt-in safe scanner hook.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from fetchcore.repository.api_detection import detect_api_artifact
from fetchcore.repository.safe_scan import SanitizedApiExtraction, SanitizedApiUnit

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,79}$")
_ROUTE = re.compile(r"^/[A-Za-z0-9_./{}-]{0,511}$")
_STATUS = re.compile(r"^(?:[1-5][0-9]{2}|[1-5]XX|default)$")
_SENSITIVE = re.compile(r"(?i)(?:secret|password|bearer|token|credential|private.?key|api.?key)")
_MAX_UNITS = 2000
_MAX_NODES = 12000
_MAX_DEPTH = 80


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate OpenAPI JSON key")
        output[key] = value
    return output


def _yaml_root(raw: str) -> object:
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("YAML parser is unavailable") from exc

    class RestrictedLoader(yaml.SafeLoader):
        def compose_node(self, parent: Any, index: Any) -> Any:
            if self.check_event(yaml.AliasEvent):
                raise ValueError("YAML alias is disallowed")
            self._observed_nodes = getattr(self, "_observed_nodes", 0) + 1
            if self._observed_nodes > _MAX_NODES:
                raise ValueError("YAML node budget exceeded")
            return super().compose_node(parent, index)

        def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
            if not isinstance(node, yaml.MappingNode):
                raise ValueError("YAML mapping required")
            result: dict[Any, Any] = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result or key == "<<":
                    raise ValueError("invalid or duplicated YAML mapping key")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    try:
        root = yaml.load(raw, Loader=RestrictedLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError("invalid OpenAPI YAML source") from exc
    return root


def _bounded_tree(node: object, *, count: list[int], depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("API syntax depth budget exceeded")
    count[0] += 1
    if count[0] > _MAX_NODES:
        raise ValueError("API syntax node budget exceeded")
    if isinstance(node, Mapping):
        for key, value in node.items():
            if not isinstance(key, str):
                raise ValueError("API syntax requires string keys")
            _bounded_tree(value, count=count, depth=depth + 1)
    elif isinstance(node, list):
        for value in node:
            _bounded_tree(value, count=count, depth=depth + 1)


def _safe_name(value: object) -> str | None:
    if not isinstance(value, str) or _IDENT.fullmatch(value) is None:
        return None
    if _SENSITIVE.search(value):
        return None
    return value


def _safe_route(value: object) -> str | None:
    if not isinstance(value, str) or _ROUTE.fullmatch(value) is None:
        return None
    if "//" in value or ".." in value:
        return None
    # Treat apparent embedded credentials as unsafe and omit the whole operation.
    if any(_SENSITIVE.search(segment) and len(segment) > 30 for segment in value.split("/")):
        return None
    return value


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _safe_media(media: str) -> bool:
    return (
        len(media) < 80
        and re.fullmatch(r"[a-zA-Z0-9_.+*-]+/[a-zA-Z0-9_.+*-]+", media) is not None
    )


def _pointer(*parts: str) -> str:
    return "#/" + "/".join(_escape(part) for part in parts)


def _map(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _ref(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("#/"):
        return None
    if len(value) > 512 or _SENSITIVE.search(value):
        return None
    # No external / relative-file dereference, traversal, or remote reference.
    segments = value[2:].split("/")
    if not segments or any(
        not segment or "~" in re.sub(r"~[01]", "", segment)
        or not re.fullmatch(r"[A-Za-z0-9_.~-]+", segment)
        for segment in segments
    ):
        return None
    return value


def _effective_parameters(
    path_item: Mapping[str, object],
    operation: Mapping[str, object],
) -> list[object]:
    """Operation-level (name,in) overrides the same observed path parameter."""

    merged: dict[tuple[str, str], object] = {}
    for holder_index, holder in enumerate((path_item, operation)):
        parameters = holder.get("parameters")
        if not isinstance(parameters, list):
            continue
        for index, raw in enumerate(parameters):
            param = _map(raw)
            name = _safe_name(param.get("name"))
            location = param.get("in")
            if name is not None and location in {"path", "query", "header", "cookie", "body"}:
                merged[(location, name)] = raw
            else:
                merged[(str(holder_index), str(index))] = raw
    return list(merged.values())


def sanitize_openapi(relative: str, raw: str) -> SanitizedApiExtraction | None:
    """Parse bounded supported specs and emit only safe method/path/schema facts.

    None means quarantine (including invalid/malformed input), never raw fallback.
    """

    decision = detect_api_artifact(relative, raw)
    if decision.status != "supported" or decision.kind not in {"openapi", "swagger"}:
        return None
    try:
        if relative.casefold().endswith(".json"):
            root = json.loads(raw, object_pairs_hook=_json_object)
        else:
            root = _yaml_root(raw)
        _bounded_tree(root, count=[0])
        if not isinstance(root, dict):
            return None
        if root.get(decision.kind) != decision.version:
            return None
        if not isinstance(root.get("paths"), dict):
            return None
        outputs: list[SanitizedApiUnit] = []
        version = decision.version or ""
        kind = decision.kind
        paths = _map(root.get("paths"))
        for path, item in sorted(paths.items()):
            route = _safe_route(path)
            if route is None:
                continue
            entry = _map(item)
            for method in _HTTP_METHODS:
                if method not in entry:
                    continue
                operation = _map(entry[method])
                if not operation:
                    continue
                loc = _pointer("paths", path, method)
                name = _safe_name(operation.get("operationId"))
                tokens = [method.upper(), route]
                if name:
                    tokens.append(name)
                parameters = _effective_parameters(entry, operation)
                param_names = sorted({
                    f"{param.get('in')}:{name}"
                    for raw_param in parameters
                    for param in [_map(raw_param)]
                    for name in [_safe_name(param.get("name"))]
                    if name is not None
                    and param.get("in") in {"path", "query", "header", "cookie", "body"}
                })
                if param_names:
                    tokens.extend(("parameters", *param_names[:30]))
                effective_security = operation.get(
                    "security", root.get("security"),
                )
                if isinstance(effective_security, list):
                    tokens.extend(("security", "required" if effective_security else "none"))
                responses = _map(operation.get("responses"))
                statuses = sorted(key for key in responses if _STATUS.fullmatch(key))
                if statuses:
                    tokens.extend(("responses", *statuses[:40]))
                outputs.append(SanitizedApiUnit(
                    kind="api_operation", format=kind, format_version=version,
                    pointer=loc, content=" ".join(tokens), method=method.upper(),
                    route=route, name=name,
                ))
                # Only directly observable, local, non-secret reference declarations.
                ref_candidates: list[tuple[str, object]] = []
                request = _map(operation.get("requestBody"))
                ref_candidates.append((loc + "/requestBody/$ref", request.get("$ref")))
                for media, raw_media in sorted(_map(request.get("content")).items()):
                    if isinstance(media, str) and _safe_media(media):
                        schema_ref = _map(_map(raw_media).get("schema")).get("$ref")
                        ref_candidates.append((
                            loc + "/requestBody/content/" + _escape(media) + "/schema/$ref",
                            schema_ref,
                        ))
                for index, parameter in enumerate(parameters):
                    param = _map(parameter)
                    ref_candidates.extend((
                        (loc + "/parameters/" + str(index) + "/$ref", param.get("$ref")),
                        (loc + "/parameters/" + str(index) + "/schema/$ref",
                         _map(param.get("schema")).get("$ref")),
                    ))
                for status, response in sorted(responses.items()):
                    response_map = _map(response)
                    ref_candidates.append(
                        (loc + "/responses/" + status.replace("~", "~0").replace("/", "~1") + "/$ref",
                         response_map.get("$ref"))
                    )
                    schema = _map(response_map.get("schema"))
                    ref_candidates.append(
                        (loc + "/responses/" + _escape(status) + "/schema/$ref",
                         schema.get("$ref"))
                    )
                    for media, raw_media in sorted(_map(response_map.get("content")).items()):
                        if isinstance(media, str) and _safe_media(media):
                            ref_candidates.append((
                                loc + "/responses/" + _escape(status) + "/content/"
                                + _escape(media) + "/schema/$ref",
                                _map(_map(raw_media).get("schema")).get("$ref"),
                            ))
                for p, target in ref_candidates:
                    local = _ref(target)
                    if local is not None:
                        outputs.append(SanitizedApiUnit(
                            kind="api_reference", format=kind, format_version=version,
                            pointer=p, content="reference " + local, target=local,
                        ))

        schemas = _map(root.get("definitions" if kind == "swagger" else "components"))
        if kind == "openapi":
            schemas = _map(schemas.get("schemas"))
        for name, schema_definition in sorted(schemas.items()):
            safe_name = _safe_name(name)
            if safe_name is None:
                continue
            pointer = _pointer(
                *(("definitions", name) if kind == "swagger" else ("components", "schemas", name))
            )
            properties = _map(_map(schema_definition).get("properties"))
            fields = sorted(
                key for key in properties if _safe_name(key) is not None
            )[:40]
            outputs.append(SanitizedApiUnit(
                kind="api_schema", format=kind, format_version=version,
                pointer=pointer, content=" ".join(("schema", safe_name, *fields)),
                name=safe_name,
            ))

        if len(outputs) > _MAX_UNITS:
            return None
        # Empty or unknown shapes stay quarantined and are not indexed as text.
        return SanitizedApiExtraction(tuple(outputs)) if outputs else None
    except (TypeError, ValueError, RecursionError, OverflowError):
        return None
