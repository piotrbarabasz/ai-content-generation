"""Deterministic mock LLM provider."""

from __future__ import annotations

import json
import re

from app.domain.enums import ProviderType
from app.domain.types import JsonDict
from app.domain.script_sections import script_sections_schema
from app.domain.visual_prompt import visual_prompt_schema

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
        if normalized_schema == visual_prompt_schema():
            inputs = json.loads(prompt)["inputs"]
            return {"prompt": (f"Scene: {inputs['scene']['text']}\n"
                               f"Section: {inputs['section_context']['title']}\n{inputs['section_context']['text']}\n"
                               f"Film brief: {inputs['film_brief']['text']}\nStyle: {inputs['visual_style']['text']}")}
        if normalized_schema == script_sections_schema():
            # Deliberately simple offline fixture, not a real language model.
            # The desktop envelope carries language as context without changing it.
            try:
                envelope = json.loads(prompt)
            except (ValueError, TypeError):
                envelope = None
            desktop = isinstance(envelope, dict) and envelope.get("task") == "generate_script_sections"
            request = envelope["request"] if desktop else prompt
            paragraphs = [part for part in re.split(r"(?:\r?\n\s*){2,}", request) if part.strip()]
            if not paragraphs:
                raise ValueError("Mock script generation requires nonempty input.")
            sections = [{"title": f"Section {index + 1}", "role": "hook" if index == 0 else "body", "text": paragraph}
                        for index, paragraph in enumerate(paragraphs)]
            if desktop and envelope.get("include_cta") is True:
                sections.append({"title": "Next step", "role": "cta", "text": "Choose your next step."})
            return {"sections": sections}
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
