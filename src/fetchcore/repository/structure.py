"""Rich, backend-neutral source structure extraction for repository indexing.

The current RepositoryIndexer consumes only ``SymbolSpan`` values for safe,
non-overlapping chunk boundaries.  This module deliberately extracts a richer
``SourceStructure`` at the same time so later KnowledgeDocument enrichment can
reuse the same language analysis instead of introducing a second parser layer.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Protocol

from fetchcore.repository.indexing import SymbolExtractor, SymbolSpan
from fetchcore.repository.lexical import (
    analyze_lexical_source,
    mask_c_like_non_code,
)


_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_JAVA_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_CONTROL_NAMES = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "throw", "new", "do", "else"}
)
_JS_TOP_LEVEL_DECLARATION_START = re.compile(
    r"(?:(?:export\s+(?:default\s+)?)?(?:abstract\s+)?"
    r"(?:class|interface|enum|function)\b|"
    r"(?:export\s+)?(?:declare\s+)?(?:const|let|var)\b|"
    r"(?:export\s+)?type\b|import\b|(?:namespace|module)\b|@)"
)


@dataclass(frozen=True)
class StructuralSymbolSpan(SymbolSpan):
    """Observed declaration facts for one source symbol."""

    qualified_name: str | None = None
    visibility: str | None = None
    container: str | None = None
    signature: str | None = None
    parameters: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize this observed symbol span."""

        return asdict(self)


@dataclass(frozen=True)
class SourceStructure:
    """Observed structural facts for one source file.

    ``symbols`` may contain nested members. ``chunk_symbols`` must contain only
    non-overlapping declaration spans that are safe for the current chunker.
    """

    language: str
    module: str | None = None
    package: str | None = None
    namespace: str | None = None
    imports: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()
    symbols: tuple[StructuralSymbolSpan, ...] = ()
    chunk_symbols: tuple[StructuralSymbolSpan, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize this source-level structural observation."""

        return {
            "language": self.language,
            "module": self.module,
            "package": self.package,
            "namespace": self.namespace,
            "imports": list(self.imports),
            "exports": list(self.exports),
            "symbols": [item.to_mapping() for item in self.symbols],
            "chunk_symbols": [item.to_mapping() for item in self.chunk_symbols],
        }


class StructuralExtractor(SymbolExtractor, Protocol):
    """A SymbolExtractor that also exposes reusable rich structure."""

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        """Extract reusable source structure from one source text."""

        ...


class PythonStructuralExtractor:
    """Extract Python declarations with the standard-library AST."""

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        """Extract non-overlapping Python symbols for repository chunking."""

        return tuple(self.extract_structure(content).chunk_symbols)

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        """Extract reusable Python structural evidence from one source file."""

        module = _module_from_path(path)
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return SourceStructure(language="python", module=module)

        imports: set[str] = set()
        exports: set[str] = set()
        symbols: list[StructuralSymbolSpan] = []
        chunks: list[StructuralSymbolSpan] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
                continue
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(("." * node.level) + node.module)
                elif node.level:
                    imports.add("." * node.level)
                continue
            if _is_python_all_assignment(node):
                exports.update(_python_string_sequence(node.value))  # type: ignore[attr-defined]
                continue
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                symbol = _python_callable_symbol(node, module=module, container=None)
                symbols.append(symbol)
                chunks.append(symbol)
                continue
            if isinstance(node, ast.ClassDef):
                class_symbol = _python_class_symbol(node, module=module)
                symbols.append(class_symbol)
                chunks.append(class_symbol)
                for member in node.body:
                    if isinstance(member, (ast.AsyncFunctionDef, ast.FunctionDef)):
                        symbols.append(
                            _python_callable_symbol(
                                member,
                                module=module,
                                container=node.name,
                            )
                        )

        return SourceStructure(
            language="python",
            module=module,
            imports=tuple(sorted(imports)),
            exports=tuple(sorted(exports)),
            symbols=_ordered_symbols(symbols),
            chunk_symbols=_ordered_symbols(chunks),
        )


class JavaScriptTypeScriptStructuralExtractor:
    """Deterministic lightweight structural extraction for JavaScript/TypeScript.

    The extractor intentionally avoids type inference. It masks comments and
    literals, proves top-level brace depth, then records declaration syntax that
    is directly observable in the file.
    """

    def __init__(self, language: str) -> None:
        """Initialize extraction for JavaScript or TypeScript."""

        if language not in {"javascript", "typescript"}:
            raise ValueError("language must be javascript or typescript")
        self.language = language

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        """Extract non-overlapping JS/TS symbols for repository chunking."""

        return tuple(self.extract_structure(content).chunk_symbols)

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        """Extract reusable JavaScript or TypeScript structural evidence."""

        module = _module_from_path(path)
        lexical = analyze_lexical_source(self.language, content)
        if lexical is None:  # pragma: no cover - constructor constrains language
            raise ValueError("lexical analysis unavailable for JS/TS")
        masked = lexical.masked_content
        depths = _brace_depths(masked)
        symbols: list[StructuralSymbolSpan] = []
        chunks: list[StructuralSymbolSpan] = []
        exports = set(_js_exports(masked, depths))

        declaration_pattern = re.compile(
            rf"(?m)^[ \t]*(?P<export>export\s+)?(?P<default>default\s+)?"
            rf"(?P<abstract>abstract\s+)?(?P<kind>class|interface|enum)\s+"
            rf"(?P<name>{_IDENTIFIER})"
        )
        for match in declaration_pattern.finditer(masked):
            if depths[match.start()] != 0:
                continue
            name = match.group("name")
            kind = match.group("kind")
            end_index = _js_type_declaration_end(masked, match.end())
            if end_index is None:
                continue
            line_start = _decorated_line_start(content, _line_number(content, match.start()))
            line_end = _line_number(content, max(match.start(), end_index - 1))
            decorators = _decorators_before(content, _line_number(content, match.start()))
            modifiers = tuple(
                value
                for value, enabled in (
                    ("export", bool(match.group("export"))),
                    ("default", bool(match.group("default"))),
                    ("abstract", bool(match.group("abstract"))),
                )
                if enabled
            )
            visibility = "public" if match.group("export") else "module"
            qualified = _qualified(module, name)
            symbol = StructuralSymbolSpan(
                name=name,
                symbol_type=kind,
                line_start=line_start,
                line_end=line_end,
                qualified_name=qualified,
                visibility=visibility,
                signature=_line_signature(content, match.start(), end_index),
                modifiers=modifiers,
                decorators=decorators,
            )
            symbols.append(symbol)
            chunks.append(symbol)
            if match.group("export"):
                exports.add(name)
            if kind == "class":
                body_open = masked.find("{", match.end(), end_index)
                if body_open >= 0:
                    symbols.extend(
                        _js_class_members(
                            content,
                            masked,
                            depths,
                            body_open=body_open,
                            body_end=end_index,
                            class_name=name,
                            module=module,
                        )
                    )

        function_pattern = re.compile(
            rf"(?m)^[ \t]*(?P<export>export\s+)?(?P<default>default\s+)?"
            rf"(?P<async>async\s+)?function\s+(?P<name>{_IDENTIFIER})\s*"
            rf"\((?P<params>[^)]*)\)"
        )
        for match in function_pattern.finditer(masked):
            if depths[match.start()] != 0:
                continue
            symbol = _js_callable_symbol(
                content,
                masked,
                match,
                module=module,
                kind="function",
            )
            symbols.append(symbol)
            chunks.append(symbol)
            if match.group("export"):
                exports.add(match.group("name"))

        variable_symbols = _js_variable_symbols(
            content,
            masked,
            depths,
            non_code_newlines=lexical.non_code_newlines,
            language=self.language,
            module=module,
        )
        symbols.extend(variable_symbols)
        chunks.extend(variable_symbols)
        exports.update(
            item.name for item in variable_symbols if "export" in item.modifiers
        )
        exports.update(_js_identifier_exports(masked, depths))

        imports = _js_imports(content)
        return SourceStructure(
            language=self.language,
            module=module,
            imports=imports,
            exports=tuple(sorted(exports)),
            symbols=_ordered_symbols(symbols),
            chunk_symbols=_ordered_symbols(chunks),
        )


class JavaStructuralExtractor:
    """Extract Java package/import/type/method declarations without inference."""

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        """Extract non-overlapping Java symbols for repository chunking."""

        return tuple(self.extract_structure(content).chunk_symbols)

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        """Extract reusable Java structural evidence from one source file."""

        del path
        masked = mask_c_like_non_code(content)
        depths = _brace_depths(masked)
        package_match = re.search(
            rf"(?m)^\s*package\s+({_JAVA_IDENTIFIER}(?:\.{_JAVA_IDENTIFIER})*)\s*;",
            masked,
        )
        package = package_match.group(1) if package_match else None
        imports = tuple(
            sorted(
                {
                    match.group(1)
                    for match in re.finditer(
                        rf"(?m)^\s*import\s+(?:static\s+)?"
                        rf"({_JAVA_IDENTIFIER}(?:\.{_JAVA_IDENTIFIER})*(?:\.\*)?)\s*;",
                        masked,
                    )
                }
            )
        )

        symbols: list[StructuralSymbolSpan] = []
        chunks: list[StructuralSymbolSpan] = []
        type_pattern = re.compile(
            rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private|abstract|static|final|"
            rf"sealed|non-sealed|strictfp)\s+)*)"
            rf"(?P<kind>class|interface|enum|record)\s+(?P<name>{_JAVA_IDENTIFIER})"
        )
        for match in type_pattern.finditer(masked):
            if depths[match.start()] != 0:
                continue
            body_open = masked.find("{", match.end())
            if body_open < 0:
                continue
            body_end = _matching_brace(masked, body_open)
            if body_end is None:
                continue
            name = match.group("name")
            modifiers = tuple(match.group("prefix").split())
            decorators = _decorators_before(content, _line_number(content, match.start()))
            line_start = _decorated_line_start(content, _line_number(content, match.start()))
            line_end = _line_number(content, body_end)
            qualified = _qualified(package, name)
            type_symbol = StructuralSymbolSpan(
                name=name,
                symbol_type=match.group("kind"),
                line_start=line_start,
                line_end=line_end,
                qualified_name=qualified,
                visibility=_java_visibility(modifiers),
                signature=_line_signature(content, match.start(), body_open + 1),
                modifiers=modifiers,
                annotations=decorators,
            )
            symbols.append(type_symbol)
            chunks.append(type_symbol)
            symbols.extend(
                _java_members(
                    content,
                    masked,
                    depths,
                    body_open=body_open,
                    body_end=body_end,
                    type_name=name,
                    package=package,
                )
            )

        return SourceStructure(
            language="java",
            package=package,
            imports=imports,
            symbols=_ordered_symbols(symbols),
            chunk_symbols=_ordered_symbols(chunks),
        )


def default_structural_symbol_extractors() -> dict[str, SymbolExtractor]:
    """Return the default priority-language extractor registry."""

    # Local import avoids a module cycle: the Groovy extractor reuses the
    # canonical SourceStructure/StructuralSymbolSpan contracts defined here.
    from fetchcore.repository.extractors.languages.dart import DartStructuralExtractor
    from fetchcore.repository.extractors.languages.groovy import GroovyStructuralExtractor

    return {
        "python": PythonStructuralExtractor(),
        "javascript": JavaScriptTypeScriptStructuralExtractor("javascript"),
        "typescript": JavaScriptTypeScriptStructuralExtractor("typescript"),
        "java": JavaStructuralExtractor(),
        "groovy": GroovyStructuralExtractor(),
        "dart": DartStructuralExtractor(),
    }


def _python_class_symbol(node: ast.ClassDef, *, module: str | None) -> StructuralSymbolSpan:
    decorators = tuple(_python_expr(item) for item in node.decorator_list)
    starts = [node.lineno, *(item.lineno for item in node.decorator_list)]
    return StructuralSymbolSpan(
        name=node.name,
        symbol_type="class",
        line_start=min(starts),
        line_end=int(node.end_lineno or node.lineno),
        qualified_name=_qualified(module, node.name),
        visibility=_python_visibility(node.name),
        signature=f"class {node.name}",
        decorators=decorators,
    )


def _python_callable_symbol(
    node: ast.AsyncFunctionDef | ast.FunctionDef,
    *,
    module: str | None,
    container: str | None,
) -> StructuralSymbolSpan:
    decorators = tuple(_python_expr(item) for item in node.decorator_list)
    starts = [node.lineno, *(item.lineno for item in node.decorator_list)]
    parameters = _python_parameters(node.args)
    modifiers = ("async",) if isinstance(node, ast.AsyncFunctionDef) else ()
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    qualified_parent = _qualified(module, container) if container else module
    return StructuralSymbolSpan(
        name=node.name,
        symbol_type="method" if container else "function",
        line_start=min(starts),
        line_end=int(node.end_lineno or node.lineno),
        qualified_name=_qualified(qualified_parent, node.name),
        visibility=_python_visibility(node.name),
        container=container,
        signature=f"{prefix} {node.name}({', '.join(parameters)})",
        parameters=parameters,
        modifiers=modifiers,
        decorators=decorators,
    )


def _python_parameters(arguments: ast.arguments) -> tuple[str, ...]:
    values = [item.arg for item in (*arguments.posonlyargs, *arguments.args)]
    if arguments.vararg is not None:
        values.append("*" + arguments.vararg.arg)
    values.extend(item.arg for item in arguments.kwonlyargs)
    if arguments.kwarg is not None:
        values.append("**" + arguments.kwarg.arg)
    return tuple(values)


def _python_expr(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


def _python_visibility(name: str) -> str:
    if name.startswith("__") and name.endswith("__"):
        return "public"
    return "private" if name.startswith("_") else "public"


def _is_python_all_assignment(node: ast.stmt) -> bool:
    if isinstance(node, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        )
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name) and node.target.id == "__all__"
    return False


def _python_string_sequence(value: ast.expr | None) -> tuple[str, ...]:
    if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return ()
    return tuple(
        item.value
        for item in value.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    )


def _js_imports(content: str) -> tuple[str, ...]:
    imports: set[str] = set()
    patterns = (
        r"(?m)^\s*import(?:[\s\S]*?\sfrom\s*)?[\"']([^\"']+)[\"']\s*;?",
        r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)",
        r"\bimport\(\s*[\"']([^\"']+)[\"']\s*\)",
    )
    for pattern in patterns:
        imports.update(match.group(1) for match in re.finditer(pattern, content))
    return tuple(sorted(imports))


def _js_exports(masked: str, depths: list[int]) -> tuple[str, ...]:
    exports: set[str] = set()
    for match in re.finditer(r"(?m)^\s*export\s*\{([^}]+)\}", masked):
        if depths[match.start()] != 0:
            continue
        for raw in match.group(1).split(","):
            token = raw.strip()
            if not token:
                continue
            if " as " in token:
                exports.add(token.split(" as ", 1)[1].strip())
            else:
                exports.add(token)
    return tuple(sorted(exports))


def _js_identifier_exports(masked: str, depths: list[int]) -> tuple[str, ...]:
    """Return only identifier-only ESM default exports."""

    exports: set[str] = set()
    pattern = re.compile(
        rf"(?m)^[ \t]*export\s+default\s+(?P<name>{_IDENTIFIER})"
        rf"[ \t]*;?[ \t]*(?:\n|$)"
    )
    for match in pattern.finditer(masked):
        if depths[match.start()] == 0:
            exports.add(match.group("name"))
    return tuple(sorted(exports))


def _js_variable_symbols(
    content: str,
    masked: str,
    depths: list[int],
    *,
    non_code_newlines: frozenset[int],
    language: str,
    module: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    declaration_pattern = re.compile(
        r"(?m)^[ \t]*(?P<export>export\s+)?(?P<declare>declare\s+)?"
        r"(?P<kind>const|let|var)\b"
    )
    symbols: list[StructuralSymbolSpan] = []
    for match in declaration_pattern.finditer(masked):
        if depths[match.start()] != 0:
            continue
        declared = bool(match.group("declare"))
        if declared and language != "typescript":
            continue
        statement_end = _js_declaration_end(
            masked,
            match.end(),
            non_code_newlines=non_code_newlines,
        )
        if statement_end <= match.end():
            continue
        declarators = _js_declarator_ranges(masked, match.end(), statement_end)
        for start, end in declarators:
            observed = _js_simple_binding(
                masked[start:end],
                declaration_kind=match.group("kind"),
                declared=declared,
                language=language,
            )
            if observed is None:
                continue
            name, symbol_type, parameters, is_async = observed
            modifiers = tuple(
                value
                for value, enabled in (
                    ("export", bool(match.group("export"))),
                    ("declare", declared),
                    ("async", is_async),
                )
                if enabled
            )
            line_start = _line_number(content, match.start())
            symbols.append(
                StructuralSymbolSpan(
                    name=name,
                    symbol_type=symbol_type,
                    line_start=_decorated_line_start(content, line_start),
                    line_end=_line_number(
                        content,
                        max(match.start(), statement_end - 1),
                    ),
                    qualified_name=_qualified(module, name),
                    visibility="public" if match.group("export") else "module",
                    signature=_line_signature(content, match.start(), statement_end),
                    parameters=parameters,
                    modifiers=modifiers,
                    decorators=_decorators_before(content, line_start),
                )
            )
    return tuple(symbols)


def _js_simple_binding(
    declarator: str,
    *,
    declaration_kind: str,
    declared: bool,
    language: str,
) -> tuple[str, str, tuple[str, ...], bool] | None:
    name_match = re.match(rf"\s*(?P<name>{_IDENTIFIER})\b", declarator)
    if name_match is None:
        return None
    name = name_match.group("name")
    remainder = declarator[name_match.end() :]
    assignment = _js_top_level_assignment(remainder)
    declaration_suffix = (
        remainder if assignment is None else remainder[:assignment]
    ).strip()
    if declaration_suffix:
        if language != "typescript":
            return None
        if not re.fullmatch(r"[!?]?\s*:\s*[\s\S]+", declaration_suffix):
            return None
    has_initializer = assignment is not None
    if declared and has_initializer:
        return None
    if declaration_kind == "const" and not has_initializer and not declared:
        return None
    initializer = remainder[assignment + 1 :] if assignment is not None else ""
    arrow = re.match(
        rf"\s*(?P<async>async\s+)?"
        rf"(?:\((?P<params>[^)]*)\)|(?P<single>{_IDENTIFIER}))\s*=>",
        initializer,
    )
    if arrow is not None:
        raw_parameters = arrow.group("params") or arrow.group("single") or ""
        return (
            name,
            "function",
            _parameter_names(raw_parameters),
            bool(arrow.group("async")),
        )
    symbol_type = "constant" if declaration_kind == "const" else "variable"
    return name, symbol_type, (), False


def _js_declaration_end(
    masked: str,
    start: int,
    *,
    non_code_newlines: frozenset[int],
) -> int:
    round_depth = 0
    square_depth = 0
    brace_depth = 0
    angle_depth = 0
    seen_assignment = False
    index = start
    while index < len(masked):
        char = masked[index]
        following = masked[index + 1] if index + 1 < len(masked) else ""
        previous = masked[index - 1] if index > start else ""
        if char == "(":
            round_depth += 1
        elif char == ")" and round_depth:
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]" and square_depth:
            square_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif not seen_assignment and char == "<":
            angle_depth += 1
        elif not seen_assignment and char == ">" and angle_depth:
            angle_depth -= 1
        elif (
            char == "="
            and following not in {"=", ">"}
            and previous not in {"=", "!", "<", ">"}
            and not any((round_depth, square_depth, brace_depth, angle_depth))
        ):
            seen_assignment = True

        at_top_level = not any((round_depth, square_depth, brace_depth, angle_depth))
        if char == ";" and at_top_level:
            return index + 1
        if char == "\n" and index not in non_code_newlines and at_top_level:
            prior = masked[start:index].rstrip()
            if prior and prior[-1] not in "=,.:?+-*/%&|^!~<>":
                return index
        index += 1
    return len(masked)


def _js_declarator_ranges(
    masked: str,
    start: int,
    end: int,
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    current = start
    round_depth = 0
    square_depth = 0
    brace_depth = 0
    angle_depth = 0
    seen_assignment = False
    for index in range(start, end):
        char = masked[index]
        following = masked[index + 1] if index + 1 < end else ""
        previous = masked[index - 1] if index > current else ""
        if char == "(":
            round_depth += 1
        elif char == ")" and round_depth:
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]" and square_depth:
            square_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif not seen_assignment and char == "<":
            angle_depth += 1
        elif not seen_assignment and char == ">" and angle_depth:
            angle_depth -= 1
        elif (
            char == "="
            and following not in {"=", ">"}
            and previous not in {"=", "!", "<", ">"}
            and not any((round_depth, square_depth, brace_depth, angle_depth))
        ):
            seen_assignment = True
        elif char == "," and not any(
            (round_depth, square_depth, brace_depth, angle_depth)
        ):
            ranges.append((current, index))
            current = index + 1
            seen_assignment = False
            angle_depth = 0
    terminal = end - 1 if end > start and masked[end - 1] == ";" else end
    ranges.append((current, terminal))
    return tuple(ranges)


def _js_top_level_assignment(value: str) -> int | None:
    round_depth = 0
    square_depth = 0
    brace_depth = 0
    angle_depth = 0
    for index, char in enumerate(value):
        following = value[index + 1] if index + 1 < len(value) else ""
        previous = value[index - 1] if index else ""
        if char == "(":
            round_depth += 1
        elif char == ")" and round_depth:
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]" and square_depth:
            square_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth:
            angle_depth -= 1
        elif (
            char == "="
            and following not in {"=", ">"}
            and previous not in {"=", "!", "<", ">"}
            and not any((round_depth, square_depth, brace_depth, angle_depth))
        ):
            return index
    return None


def _js_callable_symbol(
    content: str,
    masked: str,
    match: re.Match[str],
    *,
    module: str | None,
    kind: str,
) -> StructuralSymbolSpan:
    name = match.group("name")
    end_index = _block_or_statement_end(masked, match.end())
    start_line = _line_number(content, match.start())
    params = match.groupdict().get("params") or match.groupdict().get("single") or ""
    modifiers = tuple(
        value
        for value, enabled in (
            ("export", bool(match.groupdict().get("export"))),
            ("default", bool(match.groupdict().get("default"))),
            ("async", bool(match.groupdict().get("async"))),
        )
        if enabled
    )
    return StructuralSymbolSpan(
        name=name,
        symbol_type=kind,
        line_start=_decorated_line_start(content, start_line),
        line_end=_line_number(content, max(match.start(), end_index - 1)),
        qualified_name=_qualified(module, name),
        visibility="public" if match.groupdict().get("export") else "module",
        signature=_line_signature(content, match.start(), end_index),
        parameters=_parameter_names(params),
        modifiers=modifiers,
        decorators=_decorators_before(content, start_line),
    )


def _js_class_members(
    content: str,
    masked: str,
    depths: list[int],
    *,
    body_open: int,
    body_end: int,
    class_name: str,
    module: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    member_pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private|static|async|abstract|"
        rf"readonly|override|get|set)\s+)*)"
        rf"(?P<name>{_IDENTIFIER})\s*\((?P<params>[^)]*)\)\s*"
        rf"(?:\:[^{{;=\n]+)?(?P<end>[{{;])"
    )
    members: list[StructuralSymbolSpan] = []
    target_depth = depths[body_open] + 1
    for match in member_pattern.finditer(masked, body_open + 1, body_end):
        if depths[match.start()] != target_depth:
            continue
        name = match.group("name")
        if name in _CONTROL_NAMES:
            continue
        modifiers = tuple(match.group("prefix").split())
        end_token = match.group("end")
        if end_token == "{":
            open_index = masked.find("{", match.start(), match.end())
            matched_end = _matching_brace(masked, open_index) if open_index >= 0 else None
            end_index = (matched_end + 1) if matched_end is not None else match.end()
        else:
            end_index = match.end()
        start_line = _line_number(content, match.start())
        members.append(
            StructuralSymbolSpan(
                name=name,
                symbol_type="constructor" if name == "constructor" else "method",
                line_start=_decorated_line_start(content, start_line),
                line_end=_line_number(content, max(match.start(), end_index - 1)),
                qualified_name=_qualified(_qualified(module, class_name), name),
                visibility=_member_visibility(modifiers, default="public"),
                container=class_name,
                signature=_line_signature(content, match.start(), end_index),
                parameters=_parameter_names(match.group("params")),
                modifiers=modifiers,
                decorators=_decorators_before(content, start_line),
            )
        )

    arrow_pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private|static|readonly|override)\s+)*)"
        rf"(?P<name>{_IDENTIFIER})\s*[!?]?\s*"
        rf"(?:\:\s*[^=\n]+)?=\s*"
        rf"(?P<async>async\s+)?"
        rf"(?:\((?P<params>[^)]*)\)|(?P<single>{_IDENTIFIER}))\s*=>"
    )
    for match in arrow_pattern.finditer(masked, body_open + 1, body_end):
        if depths[match.start()] != target_depth:
            continue
        name = match.group("name")
        arrow_modifiers = list(match.group("prefix").split())
        if match.group("async"):
            arrow_modifiers.append("async")
        arrow_end = _js_class_field_arrow_end(masked, match.end(), body_end=body_end)
        if arrow_end is None:
            continue
        raw_parameters = match.group("params") or match.group("single") or ""
        start_line = _line_number(content, match.start())
        members.append(
            StructuralSymbolSpan(
                name=name,
                symbol_type="method",
                line_start=_decorated_line_start(content, start_line),
                line_end=_line_number(content, max(match.start(), arrow_end - 1)),
                qualified_name=_qualified(_qualified(module, class_name), name),
                visibility=_member_visibility(tuple(arrow_modifiers), default="public"),
                container=class_name,
                signature=_line_signature(content, match.start(), arrow_end),
                parameters=_parameter_names(raw_parameters),
                modifiers=tuple(arrow_modifiers),
                decorators=_decorators_before(content, start_line),
            )
        )
    return _ordered_symbols(members)


def _js_class_field_arrow_end(masked: str, start: int, *, body_end: int) -> int | None:
    """Return a proven class-field arrow boundary without crossing the class body."""

    index = start
    while index < body_end and masked[index].isspace():
        index += 1
    if index >= body_end:
        return None

    if masked[index] == "{":
        matched = _matching_brace(masked, index)
        if matched is None or matched >= body_end:
            return None
        end = matched + 1
        while end < body_end and masked[end] in " \t":
            end += 1
        return end + 1 if end < body_end and masked[end] == ";" else end

    round_depth = 0
    square_depth = 0
    brace_depth = 0
    while index < body_end:
        char = masked[index]
        if char == "(":
            round_depth += 1
        elif char == ")":
            if not round_depth:
                return None
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            if not square_depth:
                return None
            square_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            if not brace_depth:
                return None
            brace_depth -= 1

        at_top_level = not any((round_depth, square_depth, brace_depth))
        if char == ";" and at_top_level:
            return index + 1
        if char == "\n" and at_top_level:
            prior = masked[start:index].rstrip()
            if prior and prior[-1] not in "=,.:?+-*/%&|^!~<>":
                return index
        index += 1
    return None


def _java_members(
    content: str,
    masked: str,
    depths: list[int],
    *,
    body_open: int,
    body_end: int,
    type_name: str,
    package: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    """Extract class-level Java callables with balanced parameter parsing."""

    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private|static|final|abstract|"
        rf"synchronized|native|default|strictfp)\s+)*)"
        rf"(?:(?P<return>[A-Za-z0-9_$<>\[\],.?@ ]+)\s+)?"
        rf"(?P<name>{_JAVA_IDENTIFIER})\s*\("
    )
    members: list[StructuralSymbolSpan] = []
    target_depth = depths[body_open] + 1
    for match in pattern.finditer(masked, body_open + 1, body_end):
        if depths[match.start()] != target_depth:
            continue
        name = match.group("name")
        if name in _CONTROL_NAMES:
            continue

        params_open = match.end() - 1
        params_close = _matching_parenthesis(masked, params_open)
        if params_close is None or params_close >= body_end:
            continue
        terminator = _java_member_terminator(masked, params_close + 1, body_end)
        if terminator is None:
            continue
        terminator_index, terminator_char = terminator

        modifiers = tuple(match.group("prefix").split())
        is_constructor = name == type_name and not (match.group("return") or "").strip()
        if terminator_char == "{":
            matched_end = _matching_brace(masked, terminator_index)
            end_index = (
                matched_end + 1
                if matched_end is not None
                else terminator_index + 1
            )
        else:
            end_index = terminator_index + 1

        start_line = _line_number(content, match.start())
        annotations = _decorators_before(content, start_line)
        members.append(
            StructuralSymbolSpan(
                name=name,
                symbol_type="constructor" if is_constructor else "method",
                line_start=_decorated_line_start(content, start_line),
                line_end=_line_number(content, max(match.start(), end_index - 1)),
                qualified_name=_qualified(_qualified(package, type_name), name),
                visibility=_java_visibility(modifiers),
                container=type_name,
                signature=_line_signature(content, match.start(), end_index),
                parameters=_java_parameter_names(
                    content[params_open + 1 : params_close]
                ),
                modifiers=modifiers,
                annotations=annotations,
            )
        )
    return _ordered_symbols(members)


def _matching_parenthesis(masked: str, open_index: int) -> int | None:
    if (
        open_index < 0
        or open_index >= len(masked)
        or masked[open_index] != "("
    ):
        return None
    depth = 0
    for index in range(open_index, len(masked)):
        char = masked[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _java_member_terminator(
    masked: str,
    start: int,
    body_end: int,
) -> tuple[int, str] | None:
    """Find the callable body/statement terminator after one balanced signature."""

    index = start
    while index < body_end:
        char = masked[index]
        if char in "{;":
            return index, char
        if not char.isspace() and not masked.startswith("throws", index):
            return None
        if masked.startswith("throws", index):
            index += len("throws")
            while index < body_end and masked[index] not in "{;":
                index += 1
            if index < body_end:
                return index, masked[index]
            return None
        index += 1
    return None


def _module_from_path(path: str | None) -> str | None:
    if not path:
        return None
    pure = PurePosixPath(path)
    raw = pure.as_posix()
    if raw.endswith((".d.ts", ".d.tsx")):
        declaration_suffix = ".d.tsx" if raw.endswith(".d.tsx") else ".d.ts"
        without_suffix = PurePosixPath(raw[: -len(declaration_suffix)])
    else:
        without_suffix = pure.with_suffix("")
    parts = tuple(part for part in without_suffix.parts if part not in {".", "src"})
    return ".".join(parts) or None


def _qualified(parent: str | None, name: str | None) -> str | None:
    if not name:
        return parent
    return f"{parent}.{name}" if parent else name


def _ordered_symbols(values: list[StructuralSymbolSpan]) -> tuple[StructuralSymbolSpan, ...]:
    return tuple(
        sorted(
            values,
            # Extraction order is source order for co-located declarators. Python's
            # stable sort preserves that order while still interleaving extractor
            # passes by their source span.
            key=lambda item: (item.line_start, item.line_end),
        )
    )


def _brace_depths(masked: str) -> list[int]:
    depths = [0] * (len(masked) + 1)
    depth = 0
    for index, char in enumerate(masked):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    depths[len(masked)] = depth
    return depths


def _matching_brace(masked: str, open_index: int) -> int | None:
    if open_index < 0 or open_index >= len(masked) or masked[open_index] != "{":
        return None
    depth = 0
    for index in range(open_index, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _js_type_declaration_end(masked: str, start: int) -> int | None:
    """Return a proven multiline JS/TS type span without consuming a later declaration."""

    round_depth = 0
    square_depth = 0
    angle_depth = 0
    line_breaks = 0
    index = start
    while index < len(masked):
        char = masked[index]
        if char == "(":
            round_depth += 1
        elif char == ")" and round_depth:
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]" and square_depth:
            square_depth -= 1
        elif char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth:
            angle_depth -= 1

        at_top_level = not any((round_depth, square_depth, angle_depth))
        if char == "{" and at_top_level:
            matched = _matching_brace(masked, index)
            return matched + 1 if matched is not None else None
        if char == ";" and at_top_level:
            return index + 1
        if char == "\n" and at_top_level:
            line_breaks += 1
            if line_breaks > 64:
                return None
            next_line_end = masked.find("\n", index + 1)
            if next_line_end < 0:
                next_line_end = len(masked)
            next_line = masked[index + 1 : next_line_end].lstrip()
            if _JS_TOP_LEVEL_DECLARATION_START.match(next_line):
                return None
        index += 1
    return None


def _block_or_statement_end(masked: str, start: int) -> int:
    """Bound a JS/TS callable without letting multiline signatures eat later declarations."""

    line_breaks = 0
    index = start
    while index < len(masked):
        char = masked[index]
        if char == "{":
            matched = _matching_brace(masked, index)
            return matched + 1 if matched is not None else index
        if char == ";":
            return index + 1
        if char == "\n":
            line_breaks += 1
            if line_breaks > 64:
                return index
            next_line_end = masked.find("\n", index + 1)
            if next_line_end < 0:
                next_line_end = len(masked)
            next_line = masked[index + 1 : next_line_end].lstrip()
            if _JS_TOP_LEVEL_DECLARATION_START.match(next_line):
                return index
        index += 1
    return len(masked)


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, max(0, index)) + 1


def _decorators_before(content: str, declaration_line: int) -> tuple[str, ...]:
    lines = content.splitlines()
    values: list[str] = []
    index = declaration_line - 2
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped:
            break
        if not stripped.startswith("@"):
            break
        values.append(stripped.removeprefix("@").strip())
        index -= 1
    return tuple(reversed(values))


def _decorated_line_start(content: str, declaration_line: int) -> int:
    return declaration_line - len(_decorators_before(content, declaration_line))


def _line_signature(content: str, start: int, end: int) -> str:
    rendered = " ".join(content[start:end].strip().split())
    if "{" in rendered:
        rendered = rendered.split("{", 1)[0].strip()
    return rendered.rstrip(";")


def _parameter_names(raw: str) -> tuple[str, ...]:
    names: list[str] = []
    for part in _split_parameters(raw):
        token = part.strip()
        if not token:
            continue
        token = token.split("=", 1)[0].strip()
        token = token.removeprefix("...")
        token = token.split(":", 1)[0].strip()
        token = (
            token.removeprefix("public ")
            .removeprefix("private ")
            .removeprefix("protected ")
        )
        token = token.removeprefix("readonly ").strip()
        if token:
            names.append(token)
    return tuple(names)


def _java_parameter_names(raw: str) -> tuple[str, ...]:
    names: list[str] = []
    for part in _split_parameters(raw):
        cleaned = re.sub(r"@[A-Za-z0-9_.]+(?:\([^)]*\))?\s*", "", part).strip()
        cleaned = re.sub(r"\b(?:final|volatile|transient)\b\s*", "", cleaned).strip()
        if not cleaned:
            continue
        tokens = cleaned.replace("...", " ").split()
        if tokens:
            names.append(tokens[-1].replace("[]", ""))
    return tuple(names)


def _split_parameters(raw: str) -> tuple[str, ...]:
    values: list[str] = []
    current: list[str] = []
    depth = 0
    for char in raw:
        if char in "<([{":
            depth += 1
        elif char in ">)]}" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            values.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        values.append("".join(current))
    return tuple(values)


def _member_visibility(modifiers: tuple[str, ...], *, default: str) -> str:
    for value in ("public", "protected", "private"):
        if value in modifiers:
            return value
    return default


def _java_visibility(modifiers: tuple[str, ...]) -> str:
    return _member_visibility(modifiers, default="package")
