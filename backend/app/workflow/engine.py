"""Core workflow orchestration for module execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from app.domain.approval import ApprovalCheckpoint
from app.domain.base import DomainValidationError
from app.domain.workflow_config import WorkflowConfig
from app.domain.types import JsonDict
from app.providers.registry import ProviderRegistry
from app.providers.validation import validate_provider_availability
from app.workflow.execution import (
    ExecutionStatus,
    ModuleExecutionContext,
    ModuleExecutionPlan,
    ModuleExecutionStep,
    ModuleResult,
    approval_checkpoint_id_from_result,
    approval_checkpoint_ids_from_result,
)
from app.workflow.module import WorkflowModule
from app.workflow.registry import ModuleRegistry, ModuleRegistryError
from app.workflow.usage import NoopCostTracker, UsageTracker


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


def _coerce_approval_checkpoint_map(
    approval_checkpoints: Mapping[str, ApprovalCheckpoint] | Sequence[ApprovalCheckpoint] | None,
) -> dict[str, ApprovalCheckpoint]:
    if approval_checkpoints is None:
        return {}
    if isinstance(approval_checkpoints, Sequence) and not isinstance(approval_checkpoints, Mapping):
        checkpoint_map: dict[str, ApprovalCheckpoint] = {}
        for checkpoint in approval_checkpoints:
            if not isinstance(checkpoint, ApprovalCheckpoint):
                continue
            checkpoint_id = checkpoint.id.strip()
            if checkpoint_id:
                checkpoint_map[checkpoint_id] = checkpoint
        return checkpoint_map
    checkpoint_map: dict[str, ApprovalCheckpoint] = {}
    for checkpoint_id, checkpoint in approval_checkpoints.items():
        normalized_checkpoint_id = str(checkpoint_id).strip()
        if not normalized_checkpoint_id:
            continue
        if not isinstance(checkpoint, ApprovalCheckpoint):
            continue
        checkpoint_map[normalized_checkpoint_id] = checkpoint
    return checkpoint_map


def _normalize_module_result_for_resume(
    result: ModuleResult,
    *,
    approval_checkpoints: Mapping[str, ApprovalCheckpoint],
) -> ModuleResult:
    if result.status != "waiting_for_approval":
        return result

    checkpoint_id = approval_checkpoint_id_from_result(result)
    checkpoint = approval_checkpoints.get(checkpoint_id) if checkpoint_id else None
    if checkpoint is None or not checkpoint.is_resumable:
        return result

    output = dict(result.output)
    checkpoint_payload = dict(output.get("approval_checkpoint", {}))
    checkpoint_payload["status"] = checkpoint.status
    checkpoint_payload["required"] = checkpoint.required
    checkpoint_payload["resolved_at"] = (
        checkpoint.resolved_at.isoformat() if checkpoint.resolved_at is not None else None
    )
    checkpoint_payload["decision_history"] = [
        {
            "id": decision.id,
            "checkpoint_id": decision.checkpoint_id,
            "decision": decision.decision,
            "reviewer_id": decision.reviewer_id,
            "comment": decision.comment,
            "revised_artifact_id": decision.revised_artifact_id,
            "created_at": decision.created_at.isoformat(),
        }
        for decision in checkpoint.decision_history
    ]
    checkpoint_payload.setdefault("id", checkpoint.id)
    checkpoint_payload.setdefault("checkpoint_type", checkpoint.checkpoint_type)
    checkpoint_payload.setdefault("artifact_id", checkpoint.artifact_id)
    checkpoint_payload.setdefault("created_at", checkpoint.created_at.isoformat())
    output["approval_checkpoint"] = checkpoint_payload

    return replace(result, status="completed", output=output)


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
    approval_checkpoint_ids: tuple[str, ...] = field(default_factory=tuple)


class CoreWorkflowEngine:
    """Execute workflow modules in registry order."""

    def __init__(
        self,
        module_registry: ModuleRegistry,
        modules: Mapping[str, WorkflowModule] | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._module_registry = module_registry
        self._modules: dict[str, WorkflowModule] = dict(modules or {})
        self._usage_tracker = usage_tracker or NoopCostTracker()

    def register_module(self, module: WorkflowModule) -> None:
        """Register an executable module instance with the engine."""

        self._modules[module.definition.name] = module

    def run(
        self,
        plan: ModuleExecutionPlan,
        *,
        workflow_run_id: str,
        workflow_config_id: str,
        workflow_config: WorkflowConfig | None = None,
        provider_registry: ProviderRegistry | None = None,
        inputs: JsonDict | None = None,
        modules: Mapping[str, WorkflowModule] | None = None,
        seed_module_results: Mapping[str, ModuleResult] | None = None,
        approval_checkpoints: Mapping[str, ApprovalCheckpoint] | Sequence[ApprovalCheckpoint] | None = None,
    ) -> WorkflowExecutionResult:
        """Execute a plan and return the captured module outcomes."""

        if workflow_config is not None:
            if workflow_config.id != workflow_config_id:
                raise DomainValidationError(
                    "WorkflowConfig id does not match the supplied workflow_config_id."
                )
            if provider_registry is None:
                raise DomainValidationError(
                    "Provider registry is required when workflow_config is supplied."
                )
            workflow_config.validate(
                provider_validator=lambda config: validate_provider_availability(
                    workflow_config=config,
                    plan=plan,
                    provider_registry=provider_registry,
                )
            )

        return self.run_plan(
            plan,
            workflow_run_id=workflow_run_id,
            workflow_config_id=workflow_config_id,
            inputs=inputs,
            modules=modules,
            seed_module_results=seed_module_results,
            approval_checkpoints=approval_checkpoints,
        )

    def run_plan(
        self,
        plan: ModuleExecutionPlan,
        *,
        workflow_run_id: str,
        workflow_config_id: str,
        inputs: JsonDict | None = None,
        modules: Mapping[str, WorkflowModule] | None = None,
        seed_module_results: Mapping[str, ModuleResult] | None = None,
        approval_checkpoints: Mapping[str, ApprovalCheckpoint] | Sequence[ApprovalCheckpoint] | None = None,
    ) -> WorkflowExecutionResult:
        module_map = dict(self._modules)
        if modules is not None:
            module_map.update(modules)

        module_results: dict[str, ModuleResult] = dict(seed_module_results or {})
        completed_modules: list[str] = []
        overall_status: ExecutionStatus = "completed"
        failed_module: str | None = None
        failure_message = ""
        enabled_modules = plan.enabled_modules
        disabled_modules = plan.disabled_modules
        approval_checkpoint_map = _coerce_approval_checkpoint_map(approval_checkpoints)
        approval_checkpoint_ids: list[str] = []

        for seeded_result in module_results.values():
            for checkpoint_id in approval_checkpoint_ids_from_result(seeded_result):
                if checkpoint_id not in approval_checkpoint_ids:
                    approval_checkpoint_ids.append(checkpoint_id)

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

            existing_result = module_results.get(step.module_name)
            if existing_result is not None:
                checkpoint_id = approval_checkpoint_id_from_result(existing_result)
                if checkpoint_id and checkpoint_id not in approval_checkpoint_ids:
                    approval_checkpoint_ids.append(checkpoint_id)

                normalized_result = _normalize_module_result_for_resume(
                    existing_result,
                    approval_checkpoints=approval_checkpoint_map,
                )
                module_results[step.module_name] = normalized_result

                if normalized_result.status == "completed":
                    completed_modules.append(step.module_name)
                    continue

                if normalized_result.status == "skipped":
                    continue

                if normalized_result.status == "waiting_for_approval":
                    failure_message = ""
                    failed_module = None
                    overall_status = "waiting_for_approval"
                    break

                if normalized_result.status == "failed":
                    failure_message = normalized_result.error_message
                    failed_module = step.module_name
                    overall_status = "failed"
                    break

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
                approval_checkpoint_ids=tuple(approval_checkpoint_ids),
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
            self._track_usage(workflow_run_id=workflow_run_id, result=result)
            for checkpoint_id in approval_checkpoint_ids_from_result(result):
                if checkpoint_id not in approval_checkpoint_ids:
                    approval_checkpoint_ids.append(checkpoint_id)

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
            approval_checkpoint_ids=tuple(approval_checkpoint_ids),
        )

    def _track_usage(self, *, workflow_run_id: str, result: ModuleResult) -> None:
        """Forward optional usage metadata to the configured tracker."""

        usage_metadata = result.usage_metadata
        self._usage_tracker.record(
            workflow_run_id=workflow_run_id,
            module_name=result.module_name,
            usage_metadata=usage_metadata if usage_metadata else None,
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
