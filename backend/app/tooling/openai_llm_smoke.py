"""Explicit credentialed D026 smoke; excluded from the default offline suite."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.domain.script_sections import script_sections_schema, validate_script_sections
from app.domain.visual_prompt import validate_visual_prompt, visual_prompt_schema
from app.providers.llm_factory import build_llm_provider


def run(*, model: str, api_key_env: str, timeout_seconds: float) -> dict:
    config = ProviderConfig.create(
        workflow_config_id="d026-credentialed-smoke",
        provider_type=ProviderType.LLM,
        provider_name="openai",
        settings={"model": model, "apiKeyEnv": api_key_env, "timeoutSeconds": timeout_seconds},
    )
    provider = build_llm_provider(config)
    script = provider.generate_structured(
        json.dumps({"task": "generate_script_sections", "language": "en",
                    "request": "Explain why local project revisions matter in two short sections.",
                    "include_cta": False}),
        script_sections_schema(),
    )
    sections = validate_script_sections(script)
    script_usage = provider.last_usage()
    prompt = provider.generate_structured(
        json.dumps({"task": "visual_prompt", "inputs": {
            "scene": {"text": sections[0].text},
            "section_context": {"title": sections[0].title, "text": sections[0].text},
            "film_brief": {"text": "A clear educational film."},
            "visual_style": {"text": "Natural light, documentary framing."},
        }}),
        visual_prompt_schema(),
    )
    validated_prompt = validate_visual_prompt(prompt)
    return {
        "schema_version": 1,
        "task": "D026",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider.generation_identity(),
        "script_sections": len(sections),
        "visual_prompt_characters": len(validated_prompt),
        "script_usage": script_usage,
        "prompt_usage": provider.last_usage(),
        "credentials": {"source": "environment", "variable": api_key_env, "serialized": False},
        "pass": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(model=args.model, api_key_env=args.api_key_env, timeout_seconds=args.timeout_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
