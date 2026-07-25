"""Provider availability validation used before workflow execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.base import DomainValidationError
from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.domain.workflow_config import WorkflowConfig
from app.providers.registry import ProviderRegistry, ProviderRegistryError
from app.workflow.execution import ModuleExecutionPlan


_MODULE_PROVIDER_REQUIREMENTS: dict[str, tuple[ProviderType, ...]] = {
    "brief": (ProviderType.LLM,),
    "research": (ProviderType.LLM,),
    "dossier": (ProviderType.LLM,),
    "outline": (ProviderType.LLM,),
    "scriptGeneration": (ProviderType.LLM,),
    "qa": (ProviderType.LLM,),
    "scenePlanning": (ProviderType.ASSET,),
    "voiceover": (ProviderType.TTS,),
    "captions": (ProviderType.CAPTION,),
    "videoRendering": (ProviderType.VIDEO_RENDERER,),
    "export": (ProviderType.STORAGE,),
}


def _normalize_provider_type(provider_type: ProviderType | str) -> ProviderType:
    try:
        return ProviderType(provider_type)
    except ValueError as exc:
        raise DomainValidationError(f"Unknown provider type: {provider_type}.") from exc


def _build_provider_config(
    *,
    workflow_config_id: str,
    provider_type_name: str,
    provider_payload: Mapping[str, Any],
) -> ProviderConfig:
    provider_type = _normalize_provider_type(provider_type_name)
    provider_name = provider_payload.get("providerName", provider_payload.get("provider_name", "mock"))
    enabled = provider_payload.get("enabled", True)
    settings = provider_payload.get("settings")

    try:
        return ProviderConfig.create(
            workflow_config_id=workflow_config_id,
            provider_type=provider_type,
            provider_name=provider_name,
            enabled=enabled,
            settings=settings or {},
        )
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc


def _required_provider_types(plan: ModuleExecutionPlan) -> tuple[ProviderType, ...]:
    required: set[ProviderType] = set()
    for step in plan.steps:
        if not step.enabled:
            continue
        required.update(_MODULE_PROVIDER_REQUIREMENTS.get(step.module_name, ()))
    return tuple(sorted(required, key=lambda provider_type: provider_type.value))


def validate_provider_availability(
    *,
    workflow_config: WorkflowConfig,
    plan: ModuleExecutionPlan,
    provider_registry: ProviderRegistry,
) -> None:
    """Validate that the workflow config can resolve required providers."""

    declared_provider_configs: dict[ProviderType, ProviderConfig] = {}
    for provider_type_name, provider_payload in workflow_config.provider_config_items():
        if not isinstance(provider_payload, Mapping):
            raise DomainValidationError(
                f"ProviderConfig entry for {provider_type_name} must be an object."
            )
        provider_config = _build_provider_config(
            workflow_config_id=workflow_config.id,
            provider_type_name=provider_type_name,
            provider_payload=provider_payload,
        )
        declared_provider_configs[provider_config.provider_type] = provider_config
        if provider_config.enabled:
            try:
                provider_registry.resolve_from_config(provider_config)
            except ProviderRegistryError as exc:
                raise DomainValidationError(str(exc)) from exc

    for provider_type in _required_provider_types(plan):
        provider_config = declared_provider_configs.get(provider_type)
        if provider_config is None or not provider_config.enabled:
            raise DomainValidationError(
                f"Missing provider {provider_type.value} for enabled workflow modules."
            )
