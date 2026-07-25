"""Deterministic mock LLM provider."""

from __future__ import annotations

from app.domain.enums import ProviderType
from app.domain.types import JsonDict

from .interfaces import LLMProvider, _coerce_json_dict, _stable_signature, _slugify


class MockLLMProvider(LLMProvider):
    provider_type = ProviderType.LLM

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def generate_text(self, prompt: str, context: JsonDict | None = None) -> str:
        normalized_context = _coerce_json_dict(context)
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "prompt": prompt,
                "context": normalized_context,
            }
        )
        return f"mock-llm:{_slugify(prompt)}:{signature[:12]}"

    def generate_structured(self, prompt: str, schema: JsonDict) -> JsonDict:
        normalized_schema = _coerce_json_dict(schema)
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "prompt": prompt,
                "schema": normalized_schema,
            }
        )
        return {
            "provider": self.provider_name,
            "prompt": prompt,
            "schema": normalized_schema,
            "response": f"mock-llm-structured:{signature[:12]}",
        }
