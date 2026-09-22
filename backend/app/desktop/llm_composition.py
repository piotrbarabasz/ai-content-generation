"""Opt-in installed composition for the D026 OpenAI provider."""

from __future__ import annotations

from collections.abc import Mapping
import os

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.llm_factory import build_llm_provider


def compose_installed_llm(*, environment: Mapping[str, str] | None = None, transport=None):
    environment = os.environ if environment is None else environment
    selected = str(environment.get("AICS_LLM_PROVIDER", "")).strip().lower()
    if not selected:
        return None
    if selected != "openai":
        raise ValueError(f"Unsupported installed LLM provider: {selected}.")
    settings = {
        "model": environment.get("AICS_OPENAI_MODEL", ""),
        "apiKeyEnv": environment.get("AICS_OPENAI_API_KEY_ENV", "OPENAI_API_KEY"),
    }
    for env_name, setting in (
        ("AICS_OPENAI_TIMEOUT_SECONDS", "timeoutSeconds"),
        ("AICS_OPENAI_MAX_OUTPUT_TOKENS", "maxOutputTokens"),
        ("AICS_OPENAI_MAX_RETRIES", "maxRetries"),
        ("AICS_OPENAI_REQUEST_INTERVAL_SECONDS", "requestIntervalSeconds"),
        ("AICS_OPENAI_RETRY_DELAY_SECONDS", "retryDelaySeconds"),
    ):
        raw = environment.get(env_name)
        if raw is not None and str(raw).strip():
            try:
                settings[setting] = float(raw) if "Seconds" in setting else int(raw)
            except ValueError:
                raise ValueError(f"{env_name} must be numeric.") from None
    config = ProviderConfig.create(workflow_config_id="installed-desktop", provider_type=ProviderType.LLM,
                                   provider_name="openai", settings=settings)
    return build_llm_provider(config, transport=transport, environment=environment)


__all__ = ["compose_installed_llm"]
