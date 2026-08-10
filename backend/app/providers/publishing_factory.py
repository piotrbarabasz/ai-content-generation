"""Publishing provider composition through ProviderConfig and ProviderRegistry."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.registry import ProviderRegistry, ProviderRegistryError
from app.providers.youtube_publishing import YouTubePublishingProvider, YouTubeTransport


class PublishingFactoryError(ValueError):
    pass


def build_publishing_provider(
    provider_config: ProviderConfig,
    *,
    registry: ProviderRegistry | None = None,
    transport: YouTubeTransport | None = None,
    artifact_reader: Callable[[str], bytes] | None = None,
) -> Any:
    if provider_config.provider_type is not ProviderType.PUBLISHING:
        raise PublishingFactoryError("Publishing factory requires ProviderType.PUBLISHING.")
    if not provider_config.enabled:
        raise PublishingFactoryError("Publishing provider is disabled.")
    if registry is not None and registry.has(ProviderType.PUBLISHING, provider_config.provider_name):
        return registry.resolve_from_config(provider_config)

    if provider_config.provider_name == "mock":
        from app.providers.mocks import MockPublishingProvider

        provider = MockPublishingProvider(provider_config.provider_name)
        if registry is not None:
            registry.register(provider)
            return registry.resolve_from_config(provider_config)
        return provider
    if provider_config.provider_name != "youtube":
        raise PublishingFactoryError(
            f"Unsupported publishing provider: {provider_config.provider_name}."
        )
    settings = dict(provider_config.settings)
    unknown_settings = sorted(
        set(settings) - {"credentialsEnv", "credentials_env"}
    )
    if unknown_settings:
        raise PublishingFactoryError(
            "Unknown YouTube publishing setting(s): " + ", ".join(unknown_settings) + "."
        )
    credentials_env = str(
        settings.get("credentialsEnv") or settings.get("credentials_env") or "YOUTUBE_CREDENTIALS_FILE"
    ).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", credentials_env):
        raise PublishingFactoryError("YouTube credentialsEnv must be an environment variable name.")
    provider = YouTubePublishingProvider(
        provider_config.provider_name,
        transport=transport,
        artifact_reader=artifact_reader,
        credentials_env=credentials_env,
    )
    if registry is not None:
        try:
            registry.register(provider)
        except ProviderRegistryError as exc:
            raise PublishingFactoryError(str(exc)) from exc
        return registry.resolve_from_config(provider_config)
    return provider


__all__ = ["PublishingFactoryError", "build_publishing_provider"]
