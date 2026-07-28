"""Workflow module registry and dependency validation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.workflow.execution import ModuleExecutionPlan, ModuleExecutionStep
from app.workflow.module import ModuleDefinition, WorkflowModule
from app.workflow.presets import MVP_WORKFLOW_PRESETS, WorkflowPresetDefinition
from app.domain.enums import WorkflowPreset


class ModuleRegistryError(ValueError):
    """Raised when module registration or dependency validation fails."""


@dataclass(slots=True, frozen=True)
class RegisteredModule:
    """Snapshot of a module and its contract."""

    definition: ModuleDefinition


def _as_definition(module: ModuleDefinition | WorkflowModule) -> ModuleDefinition:
    if isinstance(module, ModuleDefinition):
        return module
    definition = getattr(module, "definition", None)
    if isinstance(definition, ModuleDefinition):
        return definition
    raise ModuleRegistryError("WorkflowModule instances must expose a ModuleDefinition.")


class ModuleRegistry:
    """Registry of workflow modules keyed by module name."""

    def __init__(self, modules: Iterable[ModuleDefinition | WorkflowModule] | None = None) -> None:
        self._modules: dict[str, ModuleDefinition] = {}
        if modules is not None:
            for module in modules:
                self.register(module)

    def register(self, module: ModuleDefinition | WorkflowModule) -> None:
        definition = _as_definition(module)
        if definition.name in self._modules:
            raise ModuleRegistryError(f"Duplicate module registration: {definition.name}.")
        self._modules[definition.name] = definition

    def get(self, module_name: str) -> ModuleDefinition:
        try:
            return self._modules[module_name]
        except KeyError as exc:
            raise ModuleRegistryError(f"Unknown module: {module_name}.") from exc

    def has(self, module_name: str) -> bool:
        return module_name in self._modules

    def list_modules(self) -> tuple[ModuleDefinition, ...]:
        return tuple(self._modules.values())

    def validate_dependencies(
        self,
        module_names: Sequence[str],
        *,
        disabled_modules: Sequence[str] | None = None,
    ) -> None:
        self.build_execution_plan(
            module_names,
            disabled_modules=disabled_modules,
        )

    def build_execution_plan(
        self,
        module_names: Sequence[str],
        *,
        disabled_modules: Sequence[str] | None = None,
    ) -> ModuleExecutionPlan:
        requested = tuple(str(name).strip() for name in module_names)
        if any(not name for name in requested):
            raise ModuleRegistryError("Module names in the execution plan cannot be blank.")
        if len(set(requested)) != len(requested):
            raise ModuleRegistryError("Module names in the execution plan must be unique.")

        disabled_set = {str(name).strip() for name in disabled_modules or () if str(name).strip()}
        positions = {name: index for index, name in enumerate(requested)}
        steps: list[ModuleExecutionStep] = []

        for index, module_name in enumerate(requested):
            definition = self.get(module_name)

            if module_name in disabled_set:
                if definition.disabled_behavior == "fail":
                    raise ModuleRegistryError(f"Module {module_name} cannot be disabled.")
                steps.append(
                    ModuleExecutionStep(
                        module_name=module_name,
                        dependency_groups=definition.dependencies,
                        enabled=False,
                        status="skipped",
                        retry_limit=definition.retry_limit,
                        artifact_outputs=definition.artifact_outputs,
                        disabled_reason="disabled",
                    )
                )
                continue

            resolved_dependencies: list[str] = []
            for dependency_group in definition.dependencies:
                resolved_dependency = self._resolve_dependency_group(
                    dependency_group=dependency_group,
                    positions=positions,
                    current_index=index,
                    disabled_set=disabled_set,
                )
                resolved_dependencies.append(resolved_dependency)

            steps.append(
                ModuleExecutionStep(
                    module_name=module_name,
                    dependency_groups=definition.dependencies,
                    enabled=True,
                    status="pending",
                    retry_limit=definition.retry_limit,
                    artifact_outputs=definition.artifact_outputs,
                    resolved_dependencies=tuple(resolved_dependencies),
                )
            )

        return ModuleExecutionPlan(steps=tuple(steps))

    def _resolve_dependency_group(
        self,
        *,
        dependency_group: tuple[str, ...],
        positions: dict[str, int],
        current_index: int,
        disabled_set: set[str],
    ) -> str:
        missing: list[str] = []
        for candidate in dependency_group:
            if candidate not in self._modules:
                missing.append(candidate)
                continue
            if candidate in disabled_set:
                missing.append(candidate)
                continue
            candidate_index = positions.get(candidate)
            if candidate_index is None or candidate_index >= current_index:
                missing.append(candidate)
                continue
            return candidate

        dependency_list = ", ".join(dependency_group)
        missing_list = ", ".join(missing) if missing else dependency_list
        raise ModuleRegistryError(
            f"Unsatisfied module dependency group ({dependency_list}); missing or out of order: {missing_list}."
        )


class WorkflowPresetRegistryError(ValueError):
    """Raised when preset registration or lookup fails."""


def _normalize_workflow_preset(preset: WorkflowPreset | str) -> WorkflowPreset:
    try:
        return WorkflowPreset(preset)
    except ValueError as exc:
        raise WorkflowPresetRegistryError(f"Unknown workflow preset: {preset}.") from exc


def _as_preset_definition(
    preset: WorkflowPresetDefinition | WorkflowPreset | str,
) -> WorkflowPresetDefinition:
    if isinstance(preset, WorkflowPresetDefinition):
        return preset
    normalized = _normalize_workflow_preset(preset)
    for definition in MVP_WORKFLOW_PRESETS:
        if definition.workflow_preset is normalized:
            return definition
    raise WorkflowPresetRegistryError(f"Unknown workflow preset: {normalized.value}.")


class WorkflowPresetRegistry:
    """Registry of canonical workflow presets."""

    def __init__(
        self,
        presets: Iterable[WorkflowPresetDefinition | WorkflowPreset | str] | None = None,
    ) -> None:
        self._presets: dict[WorkflowPreset, WorkflowPresetDefinition] = {}
        if presets is not None:
            for preset in presets:
                self.register(preset)

    def register(self, preset: WorkflowPresetDefinition | WorkflowPreset | str) -> None:
        definition = _as_preset_definition(preset)
        if definition.workflow_preset in self._presets:
            raise WorkflowPresetRegistryError(
                f"Duplicate workflow preset registration: {definition.workflow_preset.value}."
            )
        self._presets[definition.workflow_preset] = definition

    def get(self, preset: WorkflowPreset | str) -> WorkflowPresetDefinition:
        normalized = _normalize_workflow_preset(preset)
        try:
            return self._presets[normalized]
        except KeyError as exc:
            raise WorkflowPresetRegistryError(
                f"Unknown workflow preset: {normalized.value}."
            ) from exc

    def has(self, preset: WorkflowPreset | str) -> bool:
        try:
            normalized = _normalize_workflow_preset(preset)
        except WorkflowPresetRegistryError:
            return False
        return normalized in self._presets

    def list_presets(self) -> tuple[WorkflowPresetDefinition, ...]:
        return tuple(self._presets.values())

    def build_workflow_config_payload(
        self,
        preset: WorkflowPreset | str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        definition = self.get(preset)
        return definition.build_workflow_config_payload(project_id=project_id)


def build_mvp_workflow_preset_registry() -> WorkflowPresetRegistry:
    """Return the canonical preset registry for the MVP feature set."""

    return WorkflowPresetRegistry(MVP_WORKFLOW_PRESETS)


MVP_WORKFLOW_PRESET_REGISTRY = build_mvp_workflow_preset_registry()
