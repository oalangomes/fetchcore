"""Conservative, bounded Postman Collection 2.1 request acquisition.

A collection is untrusted input. Return ONLY allowlisted structure; never
persist request bodies, examples, scripts, variable values, headers values,
auth credentials, hosts, or raw URLs. Malformed/unsupported inputs fail closed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from fetchcore.repository.api_detection import detect_api_artifact
from fetchcore.repository.safe_scan import SanitizedApiExtraction, SanitizedApiUnit

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"})
_AUTH_TYPES = frozenset({
    "noauth", "bearer", "basic", "apikey", "oauth2", "oauth1", "digest",
    "awsv4", "hawk", "ntlm", "edgegrid",
})
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_. -]{0,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_ROUTE = re.compile(r"^/[A-Za-z0-9_./{}-]{0,511}$")
_BASE_VAR = re.compile(r"^\{\{[A-Za-z_][A-Za-z0-9_]{0,39}\}\}(?=/|\?|$)")
_PATH_VAR = re.compile(r"^\{\{([A-Za-z_][A-Za-z0-9_]{0,39})\}\}$")
_UNSAFE = re.compile(
    r"(?:secret|password|bearer|token|credential|private.?key|api.?key)",
    re.IGNORECASE,
)
_MIME = re.compile(r"^[A-Za-z0-9_.+*-]+/[A-Za-z0-9_.+*-]{1,40}$")
_MAX_NODES = 12_000
_MAX_DEPTH = 48
_MAX_FOLDERS = 24
_MAX_REQUESTS = 2_000


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate JSON key")
        values[key] = value
    return values


def _bounded(value: object, count: list[int], depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("Postman JSON is too deeply nested")
    count[0] += 1
    if count[0] > _MAX_NODES:
        raise ValueError("Postman JSON exceeds node budget")
    if isinstance(value, dict):
        for child in value.values():
            _bounded(child, count, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _bounded(child, count, depth + 1)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _label(value: object) -> str | None:
    if (not isinstance(value, str) or _LABEL.fullmatch(value) is None
            or _UNSAFE.search(value)):
        return None
    return value


def _identifier(value: object) -> str | None:
    if (not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None
            or _UNSAFE.search(value)):
        return None
    return value


def _normalize_path_segment(value: object) -> str | None:
    segment = value
    if isinstance(value, dict):
        segment = value.get("value")
    if not isinstance(segment, str):
        return None
    match = _PATH_VAR.fullmatch(segment)
    if match:
        return "{" + match.group(1) + "}"
    if segment.startswith(":") and _IDENTIFIER.fullmatch(segment[1:]):
        return "{" + segment[1:] + "}"
    if not re.fullmatch(r"[A-Za-z0-9_.{}-]{1,64}", segment):
        return None
    if _UNSAFE.search(segment) and len(segment) > 30:
        return None
    return segment


def _route(value: str) -> str | None:
    if (len(value) > 512 or _ROUTE.fullmatch(value) is None
            or "//" in value or ".." in value):
        return None
    if any(_UNSAFE.search(s) and len(s) > 30 for s in value.split("/")):
        return None
    return value


def _query_names(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    names = {
        name
        for item in values
        for name in [_identifier(_mapping(item).get("key"))]
        if name is not None
    }
    return tuple(sorted(names))[:24]


def _raw_route(raw: str) -> tuple[str | None, tuple[str, ...]]:
    if len(raw) > 2048 or any(ord(char) < 32 for char in raw):
        return None, ()
    cleaned = raw
    if cleaned.startswith(("https://", "http://")):
        parsed = urlsplit(cleaned)
        cleaned = parsed.path or "/"
        query = parsed.query
    else:
        base_match = _BASE_VAR.match(cleaned)
        if base_match:
            cleaned = cleaned[base_match.end():]
        if not cleaned.startswith("/"):
            return None, ()
        cleaned, _, _fragment = cleaned.partition("#")
        cleaned, _, query = cleaned.partition("?")
    segments = []
    for segment in cleaned.split("/")[1:]:
        if not segment:
            continue
        safe_segment = _normalize_path_segment(segment)
        if safe_segment is None:
            return None, ()
        segments.append(safe_segment)
    candidate = "/" + "/".join(segments)
    names = tuple(sorted({
        name
        for item in query.split("&")
        for name in [_identifier(item.split("=", 1)[0])]
        if name is not None
    }))[:24]
    return _route(candidate), names


def _request_url(value: object) -> tuple[str | None, tuple[str, ...]]:
    if isinstance(value, str):
        return _raw_route(value)
    if not isinstance(value, dict):
        return None, ()
    path = value.get("path")
    if isinstance(path, list):
        pieces: list[str] = []
        for item in path:
            piece = _normalize_path_segment(item)
            if piece is None:
                return None, ()
            pieces.append(piece)
        return _route("/" + "/".join(pieces)), _query_names(value.get("query"))
    raw = value.get("raw")
    if isinstance(raw, str):
        route, query = _raw_route(raw)
        explicit = _query_names(value.get("query"))
        return route, tuple(sorted({*query, *explicit}))[:24]
    return None, ()


def _auth_type(value: object) -> str | None:
    data = _mapping(value)
    candidate = data.get("type")
    if isinstance(candidate, str) and candidate.casefold() in _AUTH_TYPES:
        return candidate.casefold()
    return None


def _header_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names = {
        name
        for item in value
        for name in [_identifier(_mapping(item).get("key"))]
        if name is not None and name.lower() not in {"cookie", "set-cookie", "authorization"}
    }
    return tuple(sorted(names))[:24]


def _examples(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    codes: set[str] = set()
    for item in value[:100]:
        code = _mapping(item).get("code")
        if type(code) is int and 100 <= code <= 599:
            codes.add(str(code))
    return tuple(sorted(codes))[:24]


def _mimes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: set[str] = set()
    for item in value[:100]:
        headers = _mapping(item).get("header")
        if not isinstance(headers, list):
            continue
        for header in headers:
            data = _mapping(header)
            mime = data.get("value")
            if (str(data.get("key", "")).casefold() == "content-type"
                    and isinstance(mime, str)
                    and _MIME.fullmatch(mime)):
                names.add(mime)
    return tuple(sorted(names))[:12]


def _pointer(parts: tuple[str, ...]) -> str:
    return "#/" + "/".join(
        part.replace("~", "~0").replace("/", "~1") for part in parts
    )


def sanitize_postman_collection(
    relative: str, raw: str,
) -> SanitizedApiExtraction | None:
    """Return safe request-level Postman v2.1 facts or quarantine the file.

    Unsupported versions, ambiguous folder/request structures, invalid URLs
    and potentially unsafe request paths are never downgraded to raw chunks.
    """

    decision = detect_api_artifact(relative, raw)
    if (decision.status != "supported" or decision.kind != "postman_collection"
            or decision.version != "2.1.0"):
        return None
    try:
        root = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        _bounded(root, [0])
        if not isinstance(root, dict):
            return None
        info = _mapping(root.get("info"))
        if info.get("schema") != (
            "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        ):
            return None
        items = root.get("item")
        if not isinstance(items, list):
            return None
        units: list[SanitizedApiUnit] = []
        collection_auth = _auth_type(root.get("auth"))

        def walk(
            current: list[object], folder_names: tuple[str, ...],
            folder_auth: tuple[str, ...], pointer: tuple[str, ...],
            depth: int,
        ) -> None:
            if depth > _MAX_FOLDERS:
                raise ValueError("Postman folder depth budget exceeded")
            for index, item in enumerate(current):
                data = _mapping(item)
                if not data:
                    raise ValueError("invalid Postman item")
                loc = (*pointer, str(index))
                nested = data.get("item")
                request = data.get("request")
                if nested is not None and request is not None:
                    raise ValueError("ambiguous Postman folder/request")
                if nested is not None:
                    if not isinstance(nested, list):
                        raise ValueError("invalid Postman folder items")
                    folder_name = _label(data.get("name"))
                    safe_folders = (*folder_names, folder_name) if folder_name else folder_names
                    own_auth = _auth_type(data.get("auth"))
                    auth_scopes = (
                        (*folder_auth, own_auth) if own_auth is not None else folder_auth
                    )
                    walk(nested, safe_folders, auth_scopes, (*loc, "item"), depth + 1)
                    continue
                if request is None:
                    raise ValueError("Postman item without request or child items")
                req = _mapping(request)
                method = req.get("method")
                if not isinstance(method, str) or method.upper() not in _METHODS:
                    continue
                route, url_query = _request_url(req.get("url"))
                if route is None:
                    continue
                name = _label(data.get("name"))
                headers = _header_names(req.get("header"))
                examples = _examples(data.get("response"))
                response_mimes = _mimes(data.get("response"))
                own_auth = _auth_type(req.get("auth"))
                tokens = [method.upper(), route]
                if name:
                    tokens += ["request", name]
                if folder_names:
                    tokens += ["folders", *folder_names]
                if url_query:
                    tokens += ["query", *url_query]
                if headers:
                    tokens += ["headers", *headers]
                if collection_auth:
                    tokens += ["collection_auth", collection_auth]
                if folder_auth:
                    tokens += ["folder_auth", *folder_auth]
                if own_auth:
                    tokens += ["request_auth", own_auth]
                if examples:
                    tokens += ["response_status", *examples]
                if response_mimes:
                    tokens += ["response_mime", *response_mimes]
                if data.get("disabled") is True:
                    tokens.append("disabled")
                if isinstance(req.get("description"), (str, dict)):
                    tokens.append("description_present")
                units.append(SanitizedApiUnit(
                    kind="api_request", format="postman_collection",
                    format_version="2.1.0", pointer=_pointer(loc),
                    content=" ".join(tokens), method=method.upper(),
                    route=route, name=name,
                ))
                if len(units) > _MAX_REQUESTS:
                    raise ValueError("Postman request count budget exceeded")

        walk(items, (), (), ("item",), 0)
        return SanitizedApiExtraction(tuple(units)) if units else None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
