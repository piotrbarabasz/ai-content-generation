"""Explicit credentialed D027 image smoke; excluded from the offline suite."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from app.providers.image_generation import ImageGenerationRequest
from app.providers.openai_image import OpenAIImageProvider, OpenAIImageSettings


def run(*, model: str, api_key_env: str, output_dir: Path, quality: str = "low") -> dict:
    settings = OpenAIImageSettings.from_mapping({
        "model": model, "apiKeyEnv": api_key_env, "quality": quality,
    })
    provider = OpenAIImageProvider(settings)
    result = provider.generate(ImageGenerationRequest(
        "A simple editorial illustration of a desktop video editor timeline, no text or logos.",
        1024,
        1024,
    ))
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "d027-openai-image.png"
    image_path.write_bytes(result.image_bytes)
    report = {
        "schema_version": 1,
        "task": "D027",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider.capabilities().to_payload(),
        "image": {
            "name": image_path.name,
            "format": result.format,
            "width": result.width,
            "height": result.height,
            "size_bytes": len(result.image_bytes),
            "sha256": sha256(result.image_bytes).hexdigest(),
            "response_metadata": result.metadata,
        },
        "credentials": {"source": "environment", "variable": api_key_env, "serialized": False},
        "pass": True,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--quality", choices=("auto", "low", "medium", "high"), default="low")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(model=args.model, api_key_env=args.api_key_env,
                 output_dir=args.output_dir, quality=args.quality)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
