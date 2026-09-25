"""Opt-in installed composition for the D027 OpenAI image provider."""

from __future__ import annotations

from collections.abc import Mapping
import os

from app.providers.image_factory import build_image_provider


def compose_installed_image(*, environment: Mapping[str, str] | None = None, transport=None):
    environment = os.environ if environment is None else environment
    selected = str(environment.get("AICS_IMAGE_PROVIDER", "")).strip().lower()
    if not selected:
        return None
    if selected == "local":
        root = environment.get("AICS_LOCAL_IMAGE_ROOT", "")
        return build_image_provider("local", settings={"root": root})
    if selected != "openai":
        raise ValueError(f"Unsupported installed image provider: {selected}.")
    settings = {
        "model": environment.get("AICS_OPENAI_IMAGE_MODEL", ""),
        "apiKeyEnv": environment.get("AICS_OPENAI_API_KEY_ENV", "OPENAI_API_KEY"),
    }
    mappings = (
        ("AICS_OPENAI_IMAGE_QUALITY", "quality", str),
        ("AICS_OPENAI_IMAGE_BACKGROUND", "background", str),
        ("AICS_OPENAI_IMAGE_MODERATION", "moderation", str),
        ("AICS_OPENAI_IMAGE_OUTPUT_COMPRESSION", "outputCompression", int),
        ("AICS_OPENAI_IMAGE_TIMEOUT_SECONDS", "timeoutSeconds", float),
        ("AICS_OPENAI_IMAGE_MAX_BYTES", "maxImageBytes", int),
        ("AICS_OPENAI_IMAGE_MAX_RETRIES", "maxRetries", int),
        ("AICS_OPENAI_IMAGE_REQUEST_INTERVAL_SECONDS", "requestIntervalSeconds", float),
        ("AICS_OPENAI_IMAGE_RETRY_DELAY_SECONDS", "retryDelaySeconds", float),
    )
    for env_name, setting, converter in mappings:
        raw = environment.get(env_name)
        if raw is not None and str(raw).strip():
            try:
                settings[setting] = converter(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{env_name} has an invalid value.") from None
    return build_image_provider("openai", settings=settings, transport=transport, environment=environment)


__all__ = ["compose_installed_image"]
