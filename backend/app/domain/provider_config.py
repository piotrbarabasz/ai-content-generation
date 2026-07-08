"""Provider configuration domain model."""

from dataclasses import dataclass, field

from app.domain.base import DomainEntity, new_id
from app.domain.enums import ProviderType
from app.domain.types import JsonDict


@dataclass(slots=True)
class ProviderConfig(DomainEntity):
    workflow_config_id: str = ""
    provider_type: ProviderType = ProviderType.LLM
    provider_name: str = "mock"
    enabled: bool = True
    settings: JsonDict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        workflow_config_id: str,
        provider_type: ProviderType | str,
        provider_name: str = "mock",
        enabled: bool = True,
        settings: JsonDict | None = None,
    ) -> "ProviderConfig":
        return cls(
            id=new_id("provider_config"),
            workflow_config_id=workflow_config_id,
            provider_type=ProviderType(provider_type),
            provider_name=provider_name,
            enabled=enabled,
            settings=settings or {},
        )
