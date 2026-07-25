"""Provider registry for deterministic provider lookup and validation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig


class ProviderRegistryError(ValueError):
    """Raised when provider registration or resolution fails."""


@dataclass(slots=True, frozen=True)
class RegisteredProvider:
    """Immutable snapshot of a registered provider."""

    provider_type: ProviderType
    provider_name: str
    provider: Any


def _normalize_provider_type(provider_type: ProviderType | str) -> ProviderType:
    try:
        return ProviderType(provider_type)
    except ValueError as exc:
        raise ProviderRegistryError(f"Unknown provider type: {provider_type}.") from exc


def _normalize_provider_name(provider_name: Any) -> str:
    normalized = str(provider_name).strip()
    if not normalized:
        raise ProviderRegistryError("Provider name is required.")
    return normalized


def _extract_provider_signature(provider: Any) -> tuple[ProviderType, str]:
    if provider is None:
        raise ProviderRegistryError("Provider cannot be None.")

    provider_type = getattr(provider, "provider_type", None)
    provider_name = getattr(provider, "provider_name", None)

    if provider_type is None:
        raise ProviderRegistryError("Provider must define provider_type.")
    if provider_name is None:
        raise ProviderRegistryError("Provider must define provider_name.")

    return _normalize_provider_type(provider_type), _normalize_provider_name(provider_name)


class ProviderRegistry:
    """Registry of provider implementations keyed by type and name."""

    def __init__(self, providers: Iterable[Any] | None = None) -> None:
        self._providers: dict[ProviderType, dict[str, Any]] = {}
        if providers is not None:
            for provider in providers:
                self.register(provider)

    def register(self, provider: Any) -> Any:
        """Register a provider implementation and return it."""

        provider_type, provider_name = _extract_provider_signature(provider)
        providers_by_name = self._providers.setdefault(provider_type, {})
        if provider_name in providers_by_name:
            raise ProviderRegistryError(
                f"Duplicate provider registration: {provider_type.value}/{provider_name}."
            )
        providers_by_name[provider_name] = provider
        return provider

    def register_all(self, providers: Iterable[Any]) -> None:
        """Register multiple providers."""

        for provider in providers:
            self.register(provider)

    def resolve(
        self,
        provider_type: ProviderType | str,
        provider: ProviderConfig | str,
    ) -> Any:
        """Resolve a provider by type and name or provider config."""

        normalized_type = _normalize_provider_type(provider_type)
        provider_name = self._resolve_provider_name(provider, normalized_type)
        try:
            return self._providers[normalized_type][provider_name]
        except KeyError as exc:
            raise ProviderRegistryError(
                f"Unknown provider: {normalized_type.value}/{provider_name}."
            ) from exc

    def resolve_from_config(self, provider_config: ProviderConfig) -> Any:
        """Resolve a provider using a ProviderConfig instance."""

        return self.resolve(provider_config.provider_type, provider_config)

    def has(self, provider_type: ProviderType | str, provider_name: str) -> bool:
        """Return whether a provider exists for the supplied type and name."""

        normalized_type = _normalize_provider_type(provider_type)
        normalized_name = _normalize_provider_name(provider_name)
        return normalized_name in self._providers.get(normalized_type, {})

    def get(self, provider_type: ProviderType | str, provider_name: str) -> Any:
        """Alias for resolve when the provider name is known directly."""

        return self.resolve(provider_type, provider_name)

    def list_provider_types(self) -> tuple[ProviderType, ...]:
        """Return registered provider types in deterministic order."""

        return tuple(sorted(self._providers, key=lambda provider_type: provider_type.value))

    def list_provider_names(self, provider_type: ProviderType | str) -> tuple[str, ...]:
        """Return registered provider names for a specific provider type."""

        normalized_type = _normalize_provider_type(provider_type)
        provider_names = self._providers.get(normalized_type, {})
        return tuple(sorted(provider_names))

    def snapshot(self) -> tuple[RegisteredProvider, ...]:
        """Return a deterministic snapshot of the registry contents."""

        return tuple(
            RegisteredProvider(
                provider_type=provider_type,
                provider_name=provider_name,
                provider=provider,
            )
            for provider_type in self.list_provider_types()
            for provider_name, provider in sorted(self._providers[provider_type].items())
        )

    def as_execution_context(self) -> dict[ProviderType, dict[str, Any]]:
        """Expose registered providers in a structure suitable for module execution."""

        return {
            provider_type: dict(sorted(self._providers[provider_type].items()))
            for provider_type in self.list_provider_types()
        }

    def _resolve_provider_name(
        self,
        provider: ProviderConfig | str,
        expected_type: ProviderType,
    ) -> str:
        if isinstance(provider, ProviderConfig):
            if provider.provider_type is not expected_type:
                raise ProviderRegistryError(
                    "ProviderConfig provider_type does not match the requested provider type."
                )
            return _normalize_provider_name(provider.provider_name)
        return _normalize_provider_name(provider)
