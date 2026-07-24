"""Core workflow orchestration for module execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.domain.types import JsonDict
from app.workflow.execution import (
    ExecutionStatus,
    ModuleExecutionContext,
    ModuleExecutionPlan,
    ModuleExecutionStep,
    ModuleResult,
)
from app.workflow.module import WorkflowModule
from app.workflow.registry import ModuleRegistry, ModuleRegistryError


def _coerce_inputs(inputs: JsonDict | None) -> JsonDict:
    return dict(inputs or {})


def _artifact_ids(module_results: Mapping[str, ModuleResult]) -> tuple[str, ...]:
    artifact_ids: list[str] = []
    for result in module_results.values():
        artifact_ids.extend(result.output_artifact_ids)
    return tuple(artifact_ids)


def _failure_result(module_name: str, message: str) -> ModuleResult:
    return ModuleResult(module_name=module_name, status="failed", error_message=message)


def _skipped_result(module_name: str, reason: str) -> ModuleResult:
    return ModuleResult(module_name=module_name, status="skipped", skipped_reason=reason)


@dataclass(slots=True, frozen=True)
class WorkflowExecutionResult:
    """Summary of a workflow plan execution."""

    status: ExecutionStatus
    plan: ModuleExecutionPlan
    module_results: dict[str, ModuleResult] = field(default_factory=dict)
    completed_modules: tuple[str, ...] = field(default_factory=tuple)
    failed_module: str | None = None
    failure_message: str = ""
    artifact_ids: tuple[str, ...] = field(default_factory=tuple)


class CoreWorkflowEngine:
    """Execute workflow modules in registry order."""

    def __init__(
        self,
        module_registry: ModuleRegistry,
        modules: Mapping[str, WorkflowModule] | None = None,
    ) -> None:
        self._module_registry = module_registry
        self._modules: dict[str, WorkflowModule] = dict(modules or {})

    def register_module(self, module: WorkflowModule) -> None:
        """Register an executable module instance with the engine."""

        self._modules[module.definition.name] = module

    def run(
        self,
        plan: ModuleExecutionPlan,
        *,
        workflow_run_id: str,
        workflow_config_id: str,
        inputs: JsonDict | None = None,
        modules: Mapping[str, WorkflowModule] | None = None,
    ) -> WorkflowExecutionResult:
        """Execute a plan and return the captured module outcomes."""

        return self.run_plan(
            plan,
            workflow_run_id=workflow_run_id,
            workflow_config_id=workflow_config_id,
            inputs=inputs,
            modules=modules,
        )

    def run_plan(
        self,
        plan: ModuleExecutionPlan,
        *,
        workflow_run_id: str,
        workflow_config_id: str,
        inputs: JsonDict | None = None,
        modules: Mapping[str, WorkflowModule] | None = None,
    ) -> WorkflowExecutionResult:
        module_map = dict(self._modules)
        if modules is not None:
            module_map.update(modules)

        module_results: dict[str, ModuleResult] = {}
        completed_modules: list[str] = []
        overall_status: ExecutionStatus = "completed"
        failed_module: str | None = None
        failure_message = ""
        enabled_modules = plan.enabled_modules
        disabled_modules = plan.disabled_modules

        for step in plan.steps:
            try:
                self._module_registry.get(step.module_name)
            except ModuleRegistryError as exc:
                failure_message = str(exc)
                failed_module = step.module_name
                module_results[step.module_name] = _failure_result(step.module_name, failure_message)
                overall_status = "failed"
                break

            if not step.enabled:
                module_results[step.module_name] = _skipped_result(
                    step.module_name,
                    step.disabled_reason or "disabled",
                )
                continue

            if step.module_name not in module_map:
                failure_message = f"Module {step.module_name} is not registered with the engine."
                failed_module = step.module_name
                module_results[step.module_name] = _failure_result(step.module_name, failure_message)
                overall_status = "failed"
                break

            dependency_error = self._dependency_error(step, module_results)
            if dependency_error is not None:
                failure_message = dependency_error
                failed_module = step.module_name
                module_results[step.module_name] = _failure_result(step.module_name, dependency_error)
                overall_status = "failed"
                break

            module = module_map.get(step.module_name)
            if module is None:
                failure_message = f"Module {step.module_name} is not registered with the engine."
                failed_module = step.module_name
                module_results[step.module_name] = _failure_result(step.module_name, failure_message)
                overall_status = "failed"
                break

            context = ModuleExecutionContext(
                workflow_run_id=workflow_run_id,
                workflow_config_id=workflow_config_id,
                module_name=step.module_name,
                enabled_modules=enabled_modules,
                disabled_modules=disabled_modules,
                inputs=_coerce_inputs(inputs),
                module_results=dict(module_results),
                artifact_ids=_artifact_ids(module_results),
            )
            try:
                result = module.execute(context)
            except Exception as exc:  # pragma: no cover - defensive guard
                message = str(exc) or exc.__class__.__name__
                result = _failure_result(step.module_name, message)

            if result.module_name != step.module_name:
                failure_message = (
                    f"Module {step.module_name} returned result for {result.module_name}."
                )
                failed_module = step.module_name
                module_results[step.module_name] = _failure_result(step.module_name, failure_message)
                overall_status = "failed"
                break

            module_results[step.module_name] = result
            if result.status == "completed":
                completed_modules.append(step.module_name)
                continue

            if result.status == "skipped":
                continue

            failed_module = step.module_name if result.status == "failed" else failed_module
            failure_message = result.error_message
            overall_status = result.status
            break

        if overall_status == "completed" and plan.steps and not completed_modules:
            overall_status = "skipped"

        return WorkflowExecutionResult(
            status=overall_status,
            plan=plan,
            module_results=module_results,
            completed_modules=tuple(completed_modules),
            failed_module=failed_module,
            failure_message=failure_message,
            artifact_ids=_artifact_ids(module_results),
        )

    def _dependency_error(
        self,
        step: ModuleExecutionStep,
        module_results: Mapping[str, ModuleResult],
    ) -> str | None:
        if step.resolved_dependencies:
            for dependency_name in step.resolved_dependencies:
                dependency_result = module_results.get(dependency_name)
                if dependency_result is None:
                    return (
                        f"Module {step.module_name} requires completed dependency {dependency_name}."
                    )
                if dependency_result.status != "completed":
                    return (
                        f"Module {step.module_name} requires completed dependency {dependency_name}."
                    )
            return None

        for dependency_group in step.dependency_groups:
            if any(
                module_results.get(candidate) is not None
                and module_results[candidate].status == "completed"
                for candidate in dependency_group
            ):
                continue
            dependency_list = ", ".join(dependency_group)
            return (
                f"Module {step.module_name} requires one completed dependency from: {dependency_list}."
            )

        return None
