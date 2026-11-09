"""Optional grammar-backed declaration observations for additional languages."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from importlib import import_module
from importlib.metadata import version
from pathlib import PurePosixPath

from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan


_GRAMMARS: dict[str, str] = {}
_IMPORT_NODES: dict[str, frozenset[str]] = {
    "rust": frozenset({"use_declaration"}),
    "csharp": frozenset({"using_directive"}),
    "kotlin": frozenset({"import_header"}),
    "php": frozenset({"namespace_use_declaration"}),
    "swift": frozenset({"import_declaration"}),
    "scala": frozenset({"import_declaration"}),
}
_DECLARATIONS: dict[str, dict[str, str]] = {
    "rust": {"mod_item": "module", "struct_item": "struct", "enum_item": "enum", "trait_item": "trait", "function_item": "function", "impl_item": "implementation"},
    "csharp": {"namespace_declaration": "namespace", "file_scoped_namespace_declaration": "namespace", "class_declaration": "class", "struct_declaration": "struct", "interface_declaration": "interface", "enum_declaration": "enum", "method_declaration": "method", "constructor_declaration": "constructor"},
    "kotlin": {"class_declaration": "class", "object_declaration": "object", "interface_declaration": "interface", "function_declaration": "function", "type_alias": "type"},
    "ruby": {"module": "module", "class": "class", "method": "method", "singleton_method": "method"},
    "php": {"class_declaration": "class", "interface_declaration": "interface", "trait_declaration": "trait", "function_definition": "function", "method_declaration": "method"},
    "swift": {"class_declaration": "class", "protocol_declaration": "protocol", "function_declaration": "function", "typealias_declaration": "type"},
    "scala": {"class_definition": "class", "object_definition": "object", "trait_definition": "trait", "function_definition": "function", "type_definition": "type"},
}
_SCOPES = {"mod_item", "namespace_declaration", "file_scoped_namespace_declaration", "class_declaration", "struct_declaration", "interface_declaration", "trait_declaration", "enum_declaration", "object_declaration", "module", "class", "struct_item", "trait_item", "impl_item", "object_definition", "class_definition", "trait_definition"}


@lru_cache(maxsize=8)
def _parser(language: str):  # type: ignore[no-untyped-def]
    try:
        get_parser = import_module("tree_sitter_language_pack").get_parser
    except ImportError as exc:
        raise RuntimeError("structural grammar unavailable; install fetchcore[structure]") from exc
    return get_parser(_GRAMMARS.get(language, language))


class TreeSitterStructuralExtractor:
    """Observe declarations from a grammar; never claim reference resolution."""

    indexing_contract_version = "1"
    structural_capabilities: tuple[str, ...] = ("declarations",)

    def __init__(self, language: str) -> None:
        if language not in _DECLARATIONS:
            raise ValueError(f"unsupported grammar: {language}")
        self.language = language
        self.structural_capabilities = (
            ("declarations", "imports", "calls_observed")
            if language in _IMPORT_NODES else ("declarations", "calls_observed")
        )
        contract_version = "4"
        self.indexing_contract_version = f"{contract_version}:{version('tree-sitter-language-pack')}"

    def extract(self, content: str) -> tuple[StructuralSymbolSpan, ...]:
        return self.extract_structure(content).chunk_symbols

    def extract_structure(self, content: str, *, path: str | None = None) -> SourceStructure:
        data = content.encode("utf-8")
        root = _parser(self.language).parse(data).root_node
        symbols: list[StructuralSymbolSpan] = []
        chunks: list[StructuralSymbolSpan] = []
        package: str | None = None
        imports: set[str] = set()

        def visit(node, parents: tuple[str, ...], inside_declaration: bool) -> None:  # type: ignore[no-untyped-def]
            nonlocal package
            kind = _DECLARATIONS[self.language].get(node.type)
            if node.type in {"package_header", "package_clause", "namespace_definition", "file_scoped_namespace_declaration"}:
                value = node.child_by_field_name("name")
                if value is None:
                    value = next((child for child in node.named_children if child.type in {"identifier", "package_identifier", "namespace_name"}), None)
                if value is not None:
                    package = data[value.start_byte:value.end_byte].decode("utf-8")
            if node.type in _IMPORT_NODES.get(self.language, ()):
                observed = data[node.start_byte:node.end_byte].decode("utf-8").strip()
                target = observed.split(maxsplit=1)
                if len(target) == 2 and target[1].rstrip(";").strip():
                    imports.add(target[1].rstrip(";").strip())
            next_parents = parents
            next_inside = inside_declaration
            if kind is not None:
                name_node = node.child_by_field_name("name")
                if name_node is None and node.type == "impl_item":
                    name_node = node.child_by_field_name("type")
                if name_node is None:
                    name_node = next((child for child in node.named_children if child.type in {"identifier", "simple_identifier", "type_identifier", "name", "constant"}), None)
                if name_node is not None:
                    name = data[name_node.start_byte:name_node.end_byte].decode("utf-8")
                    qualified = ".".join((*parents, name))
                    line_start = node.start_point.row + 1
                    line_end = node.end_point.row + (1 if node.end_point.column else 0)
                    observed_kind = kind
                    if self.language == "swift" and node.type == "class_declaration":
                        observed_kind = data[node.start_byte:name_node.start_byte].decode("utf-8").strip().split()[-1]
                    symbol = StructuralSymbolSpan(
                        name=name, symbol_type=observed_kind, line_start=line_start,
                        line_end=max(line_start, line_end), qualified_name=qualified,
                        container=parents[-1] if parents else None,
                        signature=content.splitlines()[line_start - 1].strip()[:200],
                    )
                    symbols.append(symbol)
                    if not inside_declaration:
                        chunks.append(symbol)
                    if node.type in _SCOPES:
                        next_parents = (*parents, name)
                    next_inside = True
            for child in node.named_children:
                visit(child, next_parents, next_inside)

        visit(root, (), False)
        if package:
            symbols = [replace(item, qualified_name=f"{package}.{item.qualified_name}")
                       if not (item.symbol_type == "namespace" and item.qualified_name == package)
                       else item for item in symbols]
            chunks = [replace(item, qualified_name=f"{package}.{item.qualified_name}")
                      if not (item.symbol_type == "namespace" and item.qualified_name == package)
                      else item for item in chunks]
        return SourceStructure(
            language=self.language, package=package,
            module=PurePosixPath(path).stem if path else None,
            imports=tuple(sorted(imports)),
            symbols=tuple(sorted(symbols, key=lambda item: (item.line_start, item.qualified_name or ""))),
            chunk_symbols=tuple(sorted(chunks, key=lambda item: (item.line_start, item.qualified_name or ""))),
        )
