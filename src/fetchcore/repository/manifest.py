"""Typed package/build manifest evidence for canonical repository knowledge.

Manifest extraction is intentionally ecosystem-aware but the evidence contract is
backend-neutral. Extractors preserve declared coordinates, dependency scope,
runtime constraints, modules, plugins and executable scripts without flattening
them into free-form configuration keys.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TypeVar


_NODE_DEPENDENCY_FIELDS = (
    ("dependencies", "runtime"),
    ("devDependencies", "development"),
    ("peerDependencies", "peer"),
    ("optionalDependencies", "optional"),
)
_NODE_ENTRYPOINT_FIELDS = ("main", "module", "types", "typings", "browser")
_T = TypeVar("_T")


@dataclass(frozen=True)
class ManifestCoordinateEvidence:
    """One declared manifest coordinate, including parent references."""

    group: str | None
    name: str
    version: str | None
    relative_path: str | None = None

    @property
    def identity(self) -> str:
        return f"{self.group}:{self.name}" if self.group else self.name

    def to_mapping(self) -> dict[str, object]:
        return {
            "group": self.group,
            "name": self.name,
            "version": self.version,
            "relative_path": self.relative_path,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestCoordinateEvidence:
        return cls(
            group=_optional_string(value.get("group")),
            name=str(value.get("name", "")),
            version=_optional_string(value.get("version")),
            relative_path=_optional_string(value.get("relative_path")),
        )


@dataclass(frozen=True)
class ManifestDependencyEvidence:
    """One dependency declaration with its manifest-native requirement and scope."""

    name: str
    requirement: str | None
    scope: str
    group: str | None = None
    dependency_type: str | None = None
    classifier: str | None = None
    optional: bool | None = None
    managed: bool = False

    @property
    def identity(self) -> str:
        return f"{self.group}:{self.name}" if self.group else self.name

    def to_mapping(self) -> dict[str, object]:
        return {
            "group": self.group,
            "name": self.name,
            "requirement": self.requirement,
            "scope": self.scope,
            "dependency_type": self.dependency_type,
            "classifier": self.classifier,
            "optional": self.optional,
            "managed": self.managed,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestDependencyEvidence:
        return cls(
            group=_optional_string(value.get("group")),
            name=str(value.get("name", "")),
            requirement=_optional_string(value.get("requirement")),
            scope=str(value.get("scope", "unknown")),
            dependency_type=_optional_string(value.get("dependency_type")),
            classifier=_optional_string(value.get("classifier")),
            optional=_optional_bool(value.get("optional")),
            managed=bool(value.get("managed", False)),
        )


@dataclass(frozen=True)
class ManifestScriptEvidence:
    """One named executable command declared by a package/build manifest."""

    name: str
    command: str

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "command": self.command}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestScriptEvidence:
        return cls(name=str(value.get("name", "")), command=str(value.get("command", "")))


@dataclass(frozen=True)
class ManifestConstraintEvidence:
    """One runtime/toolchain constraint such as Node or Maven version requirements."""

    name: str
    requirement: str

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "requirement": self.requirement}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestConstraintEvidence:
        return cls(
            name=str(value.get("name", "")),
            requirement=str(value.get("requirement", "")),
        )


@dataclass(frozen=True)
class ManifestEntryPointEvidence:
    """One named package entrypoint observed as a literal manifest value."""

    kind: str
    target: str

    def to_mapping(self) -> dict[str, object]:
        return {"kind": self.kind, "target": self.target}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestEntryPointEvidence:
        return cls(kind=str(value.get("kind", "")), target=str(value.get("target", "")))


@dataclass(frozen=True)
class ManifestPropertyEvidence:
    """One literal manifest property without attempting interpolation."""

    name: str
    value: str

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "value": self.value}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestPropertyEvidence:
        return cls(name=str(value.get("name", "")), value=str(value.get("value", "")))


@dataclass(frozen=True)
class ManifestPluginExecutionEvidence:
    """One Maven plugin execution with directly declared lifecycle behavior."""

    execution_id: str | None = None
    phase: str | None = None
    goals: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        return {
            "execution_id": self.execution_id,
            "phase": self.phase,
            "goals": list(self.goals),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestPluginExecutionEvidence:
        return cls(
            execution_id=_optional_string(value.get("execution_id")),
            phase=_optional_string(value.get("phase")),
            goals=_strings(value.get("goals")),
        )


@dataclass(frozen=True)
class ManifestPluginEvidence:
    """One Maven build plugin declaration or plugin-management entry."""

    name: str
    group: str | None = None
    version: str | None = None
    managed: bool = False
    extensions: bool | None = None
    inherited: bool | None = None
    executions: tuple[ManifestPluginExecutionEvidence, ...] = ()

    @property
    def identity(self) -> str:
        return f"{self.group}:{self.name}" if self.group else self.name

    def to_mapping(self) -> dict[str, object]:
        return {
            "group": self.group,
            "name": self.name,
            "version": self.version,
            "managed": self.managed,
            "extensions": self.extensions,
            "inherited": self.inherited,
            "executions": [item.to_mapping() for item in self.executions],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestPluginEvidence:
        return cls(
            group=_optional_string(value.get("group")),
            name=str(value.get("name", "")),
            version=_optional_string(value.get("version")),
            managed=bool(value.get("managed", False)),
            extensions=_optional_bool(value.get("extensions")),
            inherited=_optional_bool(value.get("inherited")),
            executions=_mapped_items(
                value.get("executions"),
                ManifestPluginExecutionEvidence.from_mapping,
            ),
        )


@dataclass(frozen=True)
class ManifestEvidence:
    """Canonical package/build manifest facts shared by all chunks of one source."""

    ecosystem: str
    group: str | None = None
    name: str | None = None
    version: str | None = None
    display_name: str | None = None
    description: str | None = None
    packaging: str | None = None
    model_version: str | None = None
    parent: ManifestCoordinateEvidence | None = None
    private: bool | None = None
    module_type: str | None = None
    package_manager: str | None = None
    entrypoints: tuple[ManifestEntryPointEvidence, ...] = ()
    runtime_constraints: tuple[ManifestConstraintEvidence, ...] = ()
    workspaces: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    properties: tuple[ManifestPropertyEvidence, ...] = ()
    scripts: tuple[ManifestScriptEvidence, ...] = ()
    dependencies: tuple[ManifestDependencyEvidence, ...] = ()
    managed_dependencies: tuple[ManifestDependencyEvidence, ...] = ()
    plugins: tuple[ManifestPluginEvidence, ...] = ()
    managed_plugins: tuple[ManifestPluginEvidence, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        return {
            "ecosystem": self.ecosystem,
            "group": self.group,
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "packaging": self.packaging,
            "model_version": self.model_version,
            "parent": None if self.parent is None else self.parent.to_mapping(),
            "private": self.private,
            "module_type": self.module_type,
            "package_manager": self.package_manager,
            "entrypoints": [item.to_mapping() for item in self.entrypoints],
            "runtime_constraints": [
                item.to_mapping() for item in self.runtime_constraints
            ],
            "workspaces": list(self.workspaces),
            "modules": list(self.modules),
            "properties": [item.to_mapping() for item in self.properties],
            "scripts": [item.to_mapping() for item in self.scripts],
            "dependencies": [item.to_mapping() for item in self.dependencies],
            "managed_dependencies": [
                item.to_mapping() for item in self.managed_dependencies
            ],
            "plugins": [item.to_mapping() for item in self.plugins],
            "managed_plugins": [item.to_mapping() for item in self.managed_plugins],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManifestEvidence:
        raw_parent = value.get("parent")
        return cls(
            ecosystem=str(value.get("ecosystem", "unknown")),
            group=_optional_string(value.get("group")),
            name=_optional_string(value.get("name")),
            version=_optional_string(value.get("version")),
            display_name=_optional_string(value.get("display_name")),
            description=_optional_string(value.get("description")),
            packaging=_optional_string(value.get("packaging")),
            model_version=_optional_string(value.get("model_version")),
            parent=(
                ManifestCoordinateEvidence.from_mapping(raw_parent)
                if isinstance(raw_parent, Mapping)
                else None
            ),
            private=_optional_bool(value.get("private")),
            module_type=_optional_string(value.get("module_type")),
            package_manager=_optional_string(value.get("package_manager")),
            entrypoints=_mapped_items(
                value.get("entrypoints"),
                ManifestEntryPointEvidence.from_mapping,
            ),
            runtime_constraints=_mapped_items(
                value.get("runtime_constraints"),
                ManifestConstraintEvidence.from_mapping,
            ),
            workspaces=_strings(value.get("workspaces")),
            modules=_strings(value.get("modules")),
            properties=_mapped_items(
                value.get("properties"),
                ManifestPropertyEvidence.from_mapping,
            ),
            scripts=_mapped_items(
                value.get("scripts"),
                ManifestScriptEvidence.from_mapping,
            ),
            dependencies=_mapped_items(
                value.get("dependencies"),
                ManifestDependencyEvidence.from_mapping,
            ),
            managed_dependencies=_mapped_items(
                value.get("managed_dependencies"),
                ManifestDependencyEvidence.from_mapping,
            ),
            plugins=_mapped_items(
                value.get("plugins"),
                ManifestPluginEvidence.from_mapping,
            ),
            managed_plugins=_mapped_items(
                value.get("managed_plugins"),
                ManifestPluginEvidence.from_mapping,
            ),
        )


def extract_manifest_evidence(path: str, content: str) -> ManifestEvidence | None:
    """Extract a supported package/build manifest, failing closed on invalid input."""

    filename = PurePosixPath(path).name
    if filename == "package.json":
        return _extract_node_package_json(content)
    if filename == "pom.xml":
        return _extract_maven_pom(content)
    return None


def _extract_node_package_json(content: str) -> ManifestEvidence | None:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None

    dependencies: list[ManifestDependencyEvidence] = []
    for field, scope in _NODE_DEPENDENCY_FIELDS:
        raw_dependencies = decoded.get(field)
        if not isinstance(raw_dependencies, dict):
            continue
        for raw_name, raw_requirement in sorted(raw_dependencies.items()):
            if not isinstance(raw_name, str) or not isinstance(raw_requirement, str):
                continue
            dependencies.append(
                ManifestDependencyEvidence(
                    name=raw_name,
                    requirement=raw_requirement,
                    scope=scope,
                )
            )

    raw_scripts = decoded.get("scripts")
    scripts = (
        tuple(
            ManifestScriptEvidence(name=name, command=command)
            for name, command in sorted(raw_scripts.items())
            if isinstance(name, str) and isinstance(command, str)
        )
        if isinstance(raw_scripts, dict)
        else ()
    )

    raw_engines = decoded.get("engines")
    constraints = (
        tuple(
            ManifestConstraintEvidence(name=name, requirement=requirement)
            for name, requirement in sorted(raw_engines.items())
            if isinstance(name, str) and isinstance(requirement, str)
        )
        if isinstance(raw_engines, dict)
        else ()
    )

    entrypoints = tuple(
        ManifestEntryPointEvidence(kind=field, target=value)
        for field in _NODE_ENTRYPOINT_FIELDS
        if isinstance((value := decoded.get(field)), str)
    )

    return ManifestEvidence(
        ecosystem="node",
        name=_mapping_string(decoded, "name"),
        version=_mapping_string(decoded, "version"),
        description=_mapping_string(decoded, "description"),
        private=_mapping_bool(decoded, "private"),
        module_type=_mapping_string(decoded, "type"),
        package_manager=_mapping_string(decoded, "packageManager"),
        entrypoints=entrypoints,
        runtime_constraints=constraints,
        workspaces=_node_workspaces(decoded.get("workspaces")),
        scripts=scripts,
        dependencies=tuple(dependencies),
    )


def _extract_maven_pom(content: str) -> ManifestEvidence | None:
    if "<!DOCTYPE" in content.upper():
        return None
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    if _local_name(root.tag) != "project":
        return None
    model_version = _child_text(root, "modelVersion")
    if model_version != "4.0.0":
        return None

    parent = _maven_parent(root)
    dependencies = _maven_dependencies(_child(root, "dependencies"), managed=False)
    managed_dependencies = _maven_dependencies(
        _nested_child(root, "dependencyManagement", "dependencies"),
        managed=True,
    )
    plugins = _maven_plugins(
        _nested_child(root, "build", "plugins"),
        managed=False,
    )
    managed_plugins = _maven_plugins(
        _nested_child(root, "build", "pluginManagement", "plugins"),
        managed=True,
    )
    prerequisites = _child(root, "prerequisites")
    maven_requirement = _child_text(prerequisites, "maven")

    return ManifestEvidence(
        ecosystem="maven",
        group=_child_text(root, "groupId"),
        name=_child_text(root, "artifactId"),
        version=_child_text(root, "version"),
        display_name=_child_text(root, "name"),
        description=_child_text(root, "description"),
        packaging=_child_text(root, "packaging"),
        model_version=model_version,
        parent=parent,
        runtime_constraints=(
            (ManifestConstraintEvidence(name="maven", requirement=maven_requirement),)
            if maven_requirement
            else ()
        ),
        modules=_maven_modules(root),
        properties=_maven_properties(root),
        dependencies=dependencies,
        managed_dependencies=managed_dependencies,
        plugins=plugins,
        managed_plugins=managed_plugins,
    )


def _maven_parent(root: ET.Element) -> ManifestCoordinateEvidence | None:
    parent = _child(root, "parent")
    if parent is None:
        return None
    artifact_id = _child_text(parent, "artifactId")
    if not artifact_id:
        return None
    return ManifestCoordinateEvidence(
        group=_child_text(parent, "groupId"),
        name=artifact_id,
        version=_child_text(parent, "version"),
        relative_path=_child_text(parent, "relativePath"),
    )


def _maven_modules(root: ET.Element) -> tuple[str, ...]:
    modules = _child(root, "modules")
    if modules is None:
        return ()
    return tuple(
        value
        for child in modules
        if _local_name(child.tag) == "module"
        if (value := _element_text(child)) is not None
    )


def _maven_properties(root: ET.Element) -> tuple[ManifestPropertyEvidence, ...]:
    properties = _child(root, "properties")
    if properties is None:
        return ()
    values: list[ManifestPropertyEvidence] = []
    for child in properties:
        if len(child):
            continue
        value = _element_text(child)
        if value is None:
            continue
        values.append(
            ManifestPropertyEvidence(
                name=_local_name(child.tag),
                value=value,
            )
        )
    return tuple(values)


def _maven_dependencies(
    container: ET.Element | None,
    *,
    managed: bool,
) -> tuple[ManifestDependencyEvidence, ...]:
    if container is None:
        return ()
    values: list[ManifestDependencyEvidence] = []
    for dependency in container:
        if _local_name(dependency.tag) != "dependency":
            continue
        artifact_id = _child_text(dependency, "artifactId")
        if not artifact_id:
            continue
        values.append(
            ManifestDependencyEvidence(
                group=_child_text(dependency, "groupId"),
                name=artifact_id,
                requirement=_child_text(dependency, "version"),
                scope=_child_text(dependency, "scope") or "compile",
                dependency_type=_child_text(dependency, "type"),
                classifier=_child_text(dependency, "classifier"),
                optional=_maven_bool(_child_text(dependency, "optional")),
                managed=managed,
            )
        )
    return tuple(values)


def _maven_plugins(
    container: ET.Element | None,
    *,
    managed: bool,
) -> tuple[ManifestPluginEvidence, ...]:
    if container is None:
        return ()
    values: list[ManifestPluginEvidence] = []
    for plugin in container:
        if _local_name(plugin.tag) != "plugin":
            continue
        artifact_id = _child_text(plugin, "artifactId")
        if not artifact_id:
            continue
        values.append(
            ManifestPluginEvidence(
                group=_child_text(plugin, "groupId"),
                name=artifact_id,
                version=_child_text(plugin, "version"),
                managed=managed,
                extensions=_maven_bool(_child_text(plugin, "extensions")),
                inherited=_maven_bool(_child_text(plugin, "inherited")),
                executions=_maven_plugin_executions(plugin),
            )
        )
    return tuple(values)


def _maven_plugin_executions(plugin: ET.Element) -> tuple[ManifestPluginExecutionEvidence, ...]:
    executions = _child(plugin, "executions")
    if executions is None:
        return ()
    values: list[ManifestPluginExecutionEvidence] = []
    for execution in executions:
        if _local_name(execution.tag) != "execution":
            continue
        goals = _child(execution, "goals")
        values.append(
            ManifestPluginExecutionEvidence(
                execution_id=_child_text(execution, "id"),
                phase=_child_text(execution, "phase"),
                goals=(
                    tuple(
                        value
                        for child in goals
                        if _local_name(child.tag) == "goal"
                        if (value := _element_text(child)) is not None
                    )
                    if goals is not None
                    else ()
                ),
            )
        )
    return tuple(values)


def _node_workspaces(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    if isinstance(value, dict):
        packages = value.get("packages")
        if isinstance(packages, list):
            return tuple(item for item in packages if isinstance(item, str))
    return ()


def _nested_child(root: ET.Element, *names: str) -> ET.Element | None:
    current: ET.Element | None = root
    for name in names:
        if current is None:
            return None
        current = _child(current, name)
    return current


def _child(root: ET.Element, name: str) -> ET.Element | None:
    for child in root:
        if _local_name(child.tag) == name:
            return child
    return None


def _child_text(root: ET.Element | None, name: str) -> str | None:
    if root is None:
        return None
    child = _child(root, name)
    return _element_text(child) if child is not None else None


def _element_text(element: ET.Element) -> str | None:
    value = (element.text or "").strip()
    return value or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _maven_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _mapping_string(value: Mapping[str, object], key: str) -> str | None:
    raw = value.get(key)
    return raw if isinstance(raw, str) else None


def _mapping_bool(value: Mapping[str, object], key: str) -> bool | None:
    raw = value.get(key)
    return raw if isinstance(raw, bool) else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _mapped_items(
    value: object,
    factory: Callable[[Mapping[str, object]], _T],
) -> tuple[_T, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(factory(item) for item in value if isinstance(item, Mapping))
