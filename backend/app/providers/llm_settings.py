"""Validated, secret-free settings for configured LLM providers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


class LLMSettingsError(ValueError):
    """Raised when public LLM settings are invalid or contain credentials."""


_ALIASES = {
    "api_key_env": "apiKeyEnv",
    "max_output_tokens": "maxOutputTokens",
    "max_retries": "maxRetries",
    "request_interval_seconds": "requestIntervalSeconds",
    "retry_delay_seconds": "retryDelaySeconds",
    "timeout_seconds": "timeoutSeconds",
}
_ALLOWED = {
    "apiKeyEnv",
    "maxOutputTokens",
    "maxRetries",
    "model",
    "requestIntervalSeconds",
    "retryDelaySeconds",
    "timeoutSeconds",
}
_SECRET_KEYS = {
    "apiKey", "api_key", "authorization", "bearer", "credential", "credentials",
    "secret", "token",
}


def _number(value: Any, *, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMSettingsError(f"OpenAI {name} must be a number.")
    number = float(value)
    if not minimum <= number <= maximum:
        raise LLMSettingsError(f"OpenAI {name} must be between {minimum:g} and {maximum:g}.")
    return number


@dataclass(frozen=True, slots=True)
class OpenAISettings:
    model: str
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: float = 60.0
    max_output_tokens: int = 4096
    max_retries: int = 2
    request_interval_seconds: float = 0.0
    retry_delay_seconds: float = 1.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OpenAISettings":
        if not isinstance(value, Mapping):
            raise LLMSettingsError("OpenAI settings must be an object.")
        raw = dict(value)
        secrets = sorted(set(raw) & _SECRET_KEYS)
        if secrets:
            raise LLMSettingsError(
                "OpenAI credentials must be supplied through an environment variable, not provider settings."
            )
        normalized: dict[str, Any] = {}
        for key, item in raw.items():
            canonical = _ALIASES.get(key, key)
            if canonical in normalized:
                raise LLMSettingsError(f"OpenAI setting {canonical} was supplied more than once.")
            normalized[canonical] = item
        unknown = sorted(set(normalized) - _ALLOWED)
        if unknown:
            raise LLMSettingsError("Unknown OpenAI setting(s): " + ", ".join(unknown) + ".")

        model = normalized.get("model")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model):
            raise LLMSettingsError("OpenAI model must be an explicit model identifier.")
        api_key_env = normalized.get("apiKeyEnv", "OPENAI_API_KEY")
        if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise LLMSettingsError("OpenAI apiKeyEnv must be an environment variable name.")
        timeout = _number(normalized.get("timeoutSeconds", 60), name="timeoutSeconds", minimum=1, maximum=300)
        interval = _number(normalized.get("requestIntervalSeconds", 0), name="requestIntervalSeconds",
                           minimum=0, maximum=60)
        retry_delay = _number(normalized.get("retryDelaySeconds", 1), name="retryDelaySeconds",
                              minimum=0, maximum=60)
        tokens = normalized.get("maxOutputTokens", 4096)
        retries = normalized.get("maxRetries", 2)
        if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= 100_000:
            raise LLMSettingsError("OpenAI maxOutputTokens must be an integer between 1 and 100000.")
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
            raise LLMSettingsError("OpenAI maxRetries must be an integer between 0 and 5.")
        return cls(model.strip(), api_key_env, timeout, tokens, retries, interval, retry_delay)

    def public_payload(self) -> dict[str, object]:
        return {
            "model": self.model,
            "apiKeyEnv": self.api_key_env,
            "timeoutSeconds": self.timeout_seconds,
            "maxOutputTokens": self.max_output_tokens,
            "maxRetries": self.max_retries,
            "requestIntervalSeconds": self.request_interval_seconds,
            "retryDelaySeconds": self.retry_delay_seconds,
        }


__all__ = ["LLMSettingsError", "OpenAISettings"]
