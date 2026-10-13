"""Chunk-local, framework-observed behavior evidence for repository knowledge.

Framework extraction is deliberately conservative. It recognizes only a small set
of high-signal framework constructs whose semantics can be tied to source syntax
and supporting imports or explicit file-system conventions. Dynamic paths/topics,
unknown receivers, arbitrary annotations, and organization-specific conventions
remain unresolved.
"""

from __future__ import annotations

import ast
import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from fetchcore.repository.imports import ImportEvidence
from fetchcore.repository.lexical import mask_c_like_non_code


_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})
_EXPRESS_ROUTE = re.compile(
    r"(?P<receiver>\b(?:app|router))\s*\.\s*"
    r"(?P<verb>get|post|put|patch|delete|options|head|all)\s*"
    r"\(\s*(?P<quote>['\"])(?P<path>[^'\"\r\n]*)(?P=quote)\s*,",
    re.IGNORECASE,
)
_NEXT_PAGE_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})
_JAVA_ANNOTATION_START = re.compile(r"^\s*@(?P<name>[A-Za-z_$][A-Za-z0-9_$.]*)")
_JAVA_MAPPING_METHODS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
}
_JAVA_ROUTE_ANNOTATIONS = frozenset({*_JAVA_MAPPING_METHODS, "RequestMapping"})
_KAFKA_TEMPLATE_DECLARATION = re.compile(
    r"\bKafkaTemplate\s*(?:<[^;\n{}]*>)?\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b"
)
_KAFKA_SEND = re.compile(
    r"\b(?P<receiver>[A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*send\s*"
    r"\(\s*(?P<quote>['\"])(?P<topic>[^'\"\r\n]+)(?P=quote)"
)


@dataclass(frozen=True)
class BehaviorScope:
    """A symbol span used to attach behavior to the narrowest proven owner."""

    source_symbol: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class RouteEvidence:
    """One framework route declaration observed in a canonical source chunk."""

    source_symbol: str | None
    http_method: str | None
    path: str
    framework: str | None
    mechanism: str | None
    line: int | None
    resolution: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "source_symbol": self.source_symbol,
            "http_method": self.http_method,
            "path": self.path,
            "framework": self.framework,
            "mechanism": self.mechanism,
            "line": self.line,
            "resolution": self.resolution,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RouteEvidence:
        raw_line = value.get("line")
        line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None
        return cls(
            source_symbol=_optional_string(value.get("source_symbol")),
            http_method=_optional_string(value.get("http_method")),
            path=str(value.get("path", "")),
            framework=_optional_string(value.get("framework")),
            mechanism=_optional_string(value.get("mechanism")),
            line=line,
            resolution=str(value.get("resolution", "unknown")),
        )

    @classmethod
    def legacy(cls, value: str) -> RouteEvidence:
        """Hydrate pre-9.4 route strings without inventing structured facts."""

        return cls(
            source_symbol=None,
            http_method=None,
            path=value,
            framework=None,
            mechanism=None,
            line=None,
            resolution="legacy",
        )


@dataclass(frozen=True)
class EventEvidence:
    """One produced or consumed framework event/topic observation."""

    source_symbol: str | None
    name: str
    direction: str
    framework: str | None
    mechanism: str | None
    line: int | None
    resolution: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "source_symbol": self.source_symbol,
            "name": self.name,
            "direction": self.direction,
            "framework": self.framework,
            "mechanism": self.mechanism,
            "line": self.line,
            "resolution": self.resolution,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> EventEvidence:
        raw_line = value.get("line")
        line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None
        return cls(
            source_symbol=_optional_string(value.get("source_symbol")),
            name=str(value.get("name", "")),
            direction=str(value.get("direction", "unknown")),
            framework=_optional_string(value.get("framework")),
            mechanism=_optional_string(value.get("mechanism")),
            line=line,
            resolution=str(value.get("resolution", "unknown")),
        )

    @classmethod
    def legacy(cls, value: str, *, direction: str) -> EventEvidence:
        """Hydrate pre-9.4 event strings without inventing framework provenance."""

        return cls(
            source_symbol=None,
            name=value,
            direction=direction,
            framework=None,
            mechanism=None,
            line=None,
            resolution="legacy",
        )


@dataclass(frozen=True)
class FrameworkEvidence:
    """Framework behavior observed inside one canonical source chunk."""

    routes: tuple[RouteEvidence, ...] = ()
    events_produced: tuple[EventEvidence, ...] = ()
    events_consumed: tuple[EventEvidence, ...] = ()


def extract_framework_evidence(
    language: str,
    content: str,
    imports: tuple[ImportEvidence, ...],
    *,
    source_symbol: str | None,
    line_start: int,
    source_path: str | None = None,
    scopes: tuple[BehaviorScope, ...] = (),
    lexical_content: str | None = None,
    proven_fastapi_receivers: tuple[str, ...] = (),
) -> FrameworkEvidence:
    """Extract only framework behavior supported by syntax and typed imports."""

    if not content:
        return FrameworkEvidence()

    if language == "python" and _has_import(imports, "fastapi", prefix=True):
        return FrameworkEvidence(
            routes=_fastapi_routes(
                content,
                source_symbol=source_symbol,
                line_start=line_start,
                scopes=scopes,
                lexical_content=lexical_content,
                proven_receivers=proven_fastapi_receivers,
            )
        )

    if language in {"javascript", "typescript"}:
        js_routes: list[RouteEvidence] = []
        next_route = _next_pages_api_route(source_path)
        if next_route is not None:
            js_routes.append(next_route)
        if _has_import(imports, "express"):
            js_routes.extend(
                _express_routes(
                    content,
                    source_symbol=source_symbol,
                    line_start=line_start,
                    scopes=scopes,
                    lexical_content=lexical_content,
                )
            )
        return FrameworkEvidence(routes=_ordered_routes(js_routes))

    if language != "java":
        return FrameworkEvidence()

    java_routes: tuple[RouteEvidence, ...] = ()
    produced: tuple[EventEvidence, ...] = ()
    consumed: tuple[EventEvidence, ...] = ()
    if _has_import(imports, "org.springframework.web.bind.annotation.", prefix=True):
        java_routes = _spring_routes(
            content,
            source_symbol=source_symbol,
            line_start=line_start,
            scopes=scopes,
            lexical_content=lexical_content,
        )
    if _has_import(imports, "org.springframework.kafka.annotation.KafkaListener"):
        consumed = _spring_kafka_consumed(
            content,
            source_symbol=source_symbol,
            line_start=line_start,
            scopes=scopes,
            lexical_content=lexical_content,
        )
    if _has_import(imports, "org.springframework.kafka.core.KafkaTemplate"):
        produced = _spring_kafka_produced(
            content,
            source_symbol=source_symbol,
            line_start=line_start,
            scopes=scopes,
            lexical_content=lexical_content,
        )
    return FrameworkEvidence(
        routes=java_routes,
        events_produced=produced,
        events_consumed=consumed,
    )


def _fastapi_routes(
    content: str,
    *,
    source_symbol: str | None,
    line_start: int,
    scopes: tuple[BehaviorScope, ...],
    lexical_content: str | None,
    proven_receivers: tuple[str, ...],
) -> tuple[RouteEvidence, ...]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return ()

    values: list[RouteEvidence] = []
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            verb = decorator.func.attr.upper()
            if verb not in _HTTP_METHODS:
                continue
            receiver = decorator.func.value
            if not isinstance(receiver, ast.Name) or receiver.id not in {"app", "router"}:
                continue
            if lexical_content is not None and not _python_name_is_lexical_code(
                lexical_content,
                line=receiver.lineno,
                column=receiver.col_offset,
                name=receiver.id,
            ):
                continue
            path = _python_literal_argument(decorator)
            if path is None:
                continue
            absolute_line = line_start + decorator.lineno - 1
            values.append(
                RouteEvidence(
                    source_symbol=_caller_for_line(source_symbol, absolute_line, scopes),
                    http_method=verb,
                    path=path,
                    framework="fastapi" if receiver.id in proven_receivers else None,
                    mechanism=f"@{receiver.id}.{decorator.func.attr}",
                    line=absolute_line,
                    resolution=("proven_binding" if receiver.id in proven_receivers
                                else "observed"),
                )
            )
    return _ordered_routes(values)


def proven_fastapi_receivers(content: str) -> tuple[str, ...]:
    """Prove simple module-level FastAPI() bindings without flow inference.

    Any second binding, including nested shadowing, invalidates the receiver.
    This deliberately rejects valid but complex programs rather than asserting
    a framework identity that syntax cannot establish.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return ()
    factories: set[str] = set()
    imported: list[str] = []
    all_import_bindings: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                all_import_bindings.append(alias.asname or alias.name)
                if node.level == 0 and node.module == "fastapi" and alias.name == "FastAPI":
                    factories.add(alias.asname or alias.name)
                    imported.append(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            all_import_bindings.extend(alias.asname or alias.name.split(".")[0]
                                       for alias in node.names)
    stores: dict[str, int] = {}
    for walked in ast.walk(tree):
        if isinstance(walked, ast.Name) and isinstance(walked.ctx, (ast.Store, ast.Del)):
            stores[walked.id] = stores.get(walked.id, 0) + 1
        elif isinstance(walked, ast.arg):
            stores[walked.arg] = stores.get(walked.arg, 0) + 1
        elif isinstance(walked, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stores[walked.name] = stores.get(walked.name, 0) + 1
    valid_factories = {name for name in factories
                       if imported.count(name) == 1 and all_import_bindings.count(name) == 1
                       and stores.get(name, 0) == 0}
    receivers: list[str] = []
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in valid_factories):
            name = node.targets[0].id
            earlier_decorators = all(
                decorator.lineno > node.lineno
                for function in tree.body
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                for decorator in function.decorator_list
                if isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == name
            )
            if (stores.get(name) == 1 and name not in factories
                    and name not in all_import_bindings and earlier_decorators):
                receivers.append(name)
    return tuple(sorted(set(receivers)))


def _python_literal_argument(call: ast.Call) -> str | None:
    if not call.args:
        return None
    value = call.args[0]
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return None
    return value.value


def _next_pages_api_route(source_path: str | None) -> RouteEvidence | None:
    """Resolve one Next.js Pages Router API endpoint from canonical source path."""

    if not source_path:
        return None
    source = PurePosixPath(source_path)
    if source.suffix.lower() not in _NEXT_PAGE_EXTENSIONS:
        return None
    parts = source.parts
    route_start = _pages_api_path_start(parts)
    if route_start is None:
        return None
    route_segments = list(parts[route_start:])
    if not route_segments:
        return None
    route_segments[-1] = PurePosixPath(route_segments[-1]).stem
    if not route_segments[-1]:
        return None
    if route_segments[-1] == "index":
        route_segments.pop()
    path = "/api"
    if route_segments:
        path += "/" + "/".join(route_segments)
    return RouteEvidence(
        source_symbol=None,
        http_method=None,
        path=path,
        framework="nextjs",
        mechanism="pages-api-filesystem",
        line=None,
        resolution="framework_convention",
    )


def _pages_api_path_start(parts: tuple[str, ...]) -> int | None:
    for index in range(len(parts) - 1):
        if parts[index] == "pages" and parts[index + 1] == "api":
            return index + 2
    return None


def _express_routes(
    content: str,
    *,
    source_symbol: str | None,
    line_start: int,
    scopes: tuple[BehaviorScope, ...],
    lexical_content: str | None,
) -> tuple[RouteEvidence, ...]:
    masked = lexical_content if lexical_content is not None else mask_c_like_non_code(content)
    newline_offsets = _newline_offsets(content)
    values: list[RouteEvidence] = []
    for match in _EXPRESS_ROUTE.finditer(content):
        receiver = match.group("receiver")
        if masked[match.start("receiver") : match.end("receiver")] != receiver:
            continue
        path = match.group("path")
        verb = match.group("verb").upper()
        absolute_line = line_start + bisect_right(newline_offsets, match.start("receiver"))
        values.append(
            RouteEvidence(
                source_symbol=_caller_for_line(source_symbol, absolute_line, scopes),
                http_method=None if verb == "ALL" else verb,
                path=path,
                framework="express",
                mechanism=f"{receiver}.{match.group('verb').lower()}",
                line=absolute_line,
                resolution="framework_pattern",
            )
        )
    return _ordered_routes(values)


def _spring_routes(
    content: str,
    *,
    source_symbol: str | None,
    line_start: int,
    scopes: tuple[BehaviorScope, ...],
    lexical_content: str | None,
) -> tuple[RouteEvidence, ...]:
    annotations = _java_annotations(
        content,
        line_start=line_start,
        names=_JAVA_ROUTE_ANNOTATIONS,
        lexical_content=lexical_content,
    )
    class_prefixes: list[str] = []
    for name, args, line in annotations:
        if name != "RequestMapping" or _scope_for_line(line, scopes) is not None:
            continue
        path = _annotation_path(args)
        if path is not None:
            class_prefixes.append(path)

    unique_prefixes = tuple(dict.fromkeys(class_prefixes))
    if len(unique_prefixes) > 1:
        # Multiple class-level prefixes are ambiguous without modeling mapping arrays.
        return ()
    class_prefix = unique_prefixes[0] if unique_prefixes else ""

    values: list[RouteEvidence] = []
    for name, args, line in annotations:
        scope = _scope_for_line(line, scopes)
        if scope is None:
            continue
        local_path = _annotation_path(args)
        if local_path is None:
            continue
        methods = _spring_http_methods(name, args)
        for method in methods:
            values.append(
                RouteEvidence(
                    source_symbol=scope.source_symbol,
                    http_method=method,
                    path=_join_route_paths(class_prefix, local_path),
                    framework="spring-mvc",
                    mechanism=f"@{name}",
                    line=line,
                    resolution="framework_syntax",
                )
            )
    return _ordered_routes(values)


def _spring_http_methods(name: str, args: str | None) -> tuple[str | None, ...]:
    direct = _JAVA_MAPPING_METHODS.get(name)
    if direct is not None:
        return (direct,)
    if name != "RequestMapping":
        return ()
    observed = tuple(
        dict.fromkeys(
            match.group(1)
            for match in re.finditer(
                r"\bRequestMethod\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\b",
                args or "",
            )
        )
    )
    return observed or (None,)


def _spring_kafka_consumed(
    content: str,
    *,
    source_symbol: str | None,
    line_start: int,
    scopes: tuple[BehaviorScope, ...],
    lexical_content: str | None,
) -> tuple[EventEvidence, ...]:
    values: list[EventEvidence] = []
    for name, args, line in _java_annotations(
        content,
        line_start=line_start,
        names=frozenset({"KafkaListener"}),
        lexical_content=lexical_content,
    ):
        del name
        topic = _named_literal(args, "topics")
        if topic is None or "${" in topic:
            continue
        values.append(
            EventEvidence(
                source_symbol=_caller_for_line(source_symbol, line, scopes),
                name=topic,
                direction="consumed",
                framework="spring-kafka",
                mechanism="@KafkaListener",
                line=line,
                resolution="framework_syntax",
            )
        )
    return _ordered_events(values)


def _spring_kafka_produced(
    content: str,
    *,
    source_symbol: str | None,
    line_start: int,
    scopes: tuple[BehaviorScope, ...],
    lexical_content: str | None,
) -> tuple[EventEvidence, ...]:
    masked = lexical_content if lexical_content is not None else mask_c_like_non_code(content)
    receivers = {
        match.group("name")
        for match in _KAFKA_TEMPLATE_DECLARATION.finditer(masked)
    }
    if not receivers:
        return ()

    newline_offsets = _newline_offsets(content)
    values: list[EventEvidence] = []
    for match in _KAFKA_SEND.finditer(content):
        receiver = match.group("receiver")
        if receiver not in receivers:
            continue
        if masked[match.start("receiver") : match.end("receiver")] != receiver:
            continue
        topic = match.group("topic")
        if "${" in topic:
            continue
        line = line_start + bisect_right(newline_offsets, match.start("receiver"))
        values.append(
            EventEvidence(
                source_symbol=_caller_for_line(source_symbol, line, scopes),
                name=topic,
                direction="produced",
                framework="spring-kafka",
                mechanism="KafkaTemplate.send",
                line=line,
                resolution="framework_syntax",
            )
        )
    return _ordered_events(values)


def _java_annotations(
    content: str,
    *,
    line_start: int,
    names: frozenset[str],
    lexical_content: str | None = None,
) -> tuple[tuple[str, str | None, int], ...]:
    """Read bounded Java annotations without allowing a malformed one to scan a file."""

    lines = content.splitlines()
    lexical_lines = lexical_content.splitlines() if lexical_content is not None else None
    values: list[tuple[str, str | None, int]] = []
    index = 0
    while index < len(lines):
        first = lines[index]
        match = _JAVA_ANNOTATION_START.match(first)
        if match is None:
            index += 1
            continue
        if lexical_lines is not None:
            lexical_match = (
                _JAVA_ANNOTATION_START.match(lexical_lines[index])
                if index < len(lexical_lines)
                else None
            )
            if lexical_match is None or lexical_match.group("name") != match.group("name"):
                index += 1
                continue
        name = match.group("name").rsplit(".", 1)[-1]
        if name not in names:
            index += 1
            continue

        statement = first.strip()
        consumed = 1
        balance = _paren_balance(statement)
        while balance > 0 and index + consumed < len(lines) and consumed < 8:
            following = lines[index + consumed].strip()
            statement += " " + following
            balance += _paren_balance(following)
            consumed += 1
        if balance > 0:
            index += consumed
            continue

        args = _annotation_args(statement)
        values.append((name, args, line_start + index))
        index += consumed
    return tuple(values)


def _python_name_is_lexical_code(
    lexical_content: str,
    *,
    line: int,
    column: int,
    name: str,
) -> bool:
    lines = lexical_content.splitlines()
    if line < 1 or line > len(lines):
        return False
    return lines[line - 1][column : column + len(name)] == name


def _paren_balance(value: str) -> int:
    balance = 0
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote is not None:
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            balance += 1
        elif char == ")":
            balance -= 1
    return balance


def _annotation_args(statement: str) -> str | None:
    start = statement.find("(")
    if start < 0:
        return None
    end = statement.rfind(")")
    if end <= start:
        return None
    return statement[start + 1 : end].strip()


def _annotation_path(args: str | None) -> str | None:
    if args is None or not args.strip():
        return ""
    named = _named_literal(args, "value") or _named_literal(args, "path")
    if named is not None:
        return named
    stripped = args.lstrip()
    literal = re.match(r"(?P<quote>['\"])(?P<value>[^'\"\r\n]*)(?P=quote)", stripped)
    if literal is None:
        return None
    return literal.group("value")


def _named_literal(args: str | None, name: str) -> str | None:
    if args is None:
        return None
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(?P<quote>['\"])(?P<value>[^'\"\r\n]*)(?P=quote)",
        args,
    )
    return match.group("value") if match is not None else None


def _join_route_paths(prefix: str, local: str) -> str:
    if not prefix and not local:
        return "/"
    if not prefix:
        return local if local.startswith("/") else "/" + local
    if not local:
        return prefix if prefix.startswith("/") else "/" + prefix
    return "/" + "/".join(
        part.strip("/")
        for part in (prefix, local)
        if part.strip("/")
    )


def _has_import(
    imports: tuple[ImportEvidence, ...],
    expected: str,
    *,
    prefix: bool = False,
) -> bool:
    return any(
        item.kind != "legacy"
        and (
            item.source.startswith(expected)
            if prefix
            else item.source == expected
        )
        for item in imports
    )


def _scope_for_line(
    line: int,
    scopes: tuple[BehaviorScope, ...],
) -> BehaviorScope | None:
    matching = [scope for scope in scopes if scope.line_start <= line <= scope.line_end]
    if not matching:
        return None
    return min(
        matching,
        key=lambda item: (item.line_end - item.line_start, item.line_start, item.source_symbol),
    )


def _caller_for_line(
    fallback: str | None,
    line: int,
    scopes: tuple[BehaviorScope, ...],
) -> str | None:
    scope = _scope_for_line(line, scopes)
    return scope.source_symbol if scope is not None else fallback


def _newline_offsets(content: str) -> list[int]:
    return [index for index, char in enumerate(content) if char == "\n"]


def _ordered_routes(values: Iterable[RouteEvidence]) -> tuple[RouteEvidence, ...]:
    unique: dict[tuple[object, ...], RouteEvidence] = {}
    for item in values:
        key = (
            item.source_symbol,
            item.http_method,
            item.path,
            item.framework,
            item.mechanism,
            item.line,
            item.resolution,
        )
        unique[key] = item
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.line if item.line is not None else 0,
                item.http_method or "",
                item.path,
                item.source_symbol or "",
            ),
        )
    )


def _ordered_events(values: Iterable[EventEvidence]) -> tuple[EventEvidence, ...]:
    unique: dict[tuple[object, ...], EventEvidence] = {}
    for item in values:
        key = (
            item.source_symbol,
            item.name,
            item.direction,
            item.framework,
            item.mechanism,
            item.line,
            item.resolution,
        )
        unique[key] = item
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.line if item.line is not None else 0,
                item.direction,
                item.name,
                item.source_symbol or "",
            ),
        )
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value)
    return rendered if rendered else None
