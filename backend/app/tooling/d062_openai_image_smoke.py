"""Explicit, paid GPT Image 2 landscape smoke; never run as part of pytest."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import time

from app.environment import load_application_environment
from app.providers.image_generation import ImageGenerationRequest
from app.providers.openai_image import OpenAIImageProvider, OpenAIImageSettings
from app.storage.image_decoder import ImageLimits, decode_image


def run(output_dir: Path) -> dict:
    load_application_environment()
    provider = OpenAIImageProvider(OpenAIImageSettings.from_mapping({
        "model": "gpt-image-2", "apiKeyEnv": "OPENAI_API_KEY", "quality": "low",
        "outputCompression": 90, "background": "auto", "moderation": "auto",
    }))
    request = ImageGenerationRequest(
        "A simple editorial illustration of a small desk plant beside a closed notebook, no text.",
        1280, 720, format="WEBP")
    started = time.perf_counter()
    result = provider.generate(request)
    duration = round(time.perf_counter() - started, 3)
    measured = decode_image(result.image_bytes, ImageLimits(max_bytes=16 * 1024 * 1024,
                                                            max_dimension=3840,
                                                            max_pixels=8_294_400))
    if (result.format, result.width, result.height) != ("WEBP", 1280, 720) or measured["format"] != "WEBP":
        raise ValueError("GPT Image 2 smoke returned unexpected output measurements.")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "d062-gpt-image-2-landscape.webp").write_bytes(result.image_bytes)
    report = {
        "success": True,
        "model": "gpt-image-2",
        "width": result.width,
        "height": result.height,
        "format": result.format,
        "byte_count": len(result.image_bytes),
        "sha256": sha256(result.image_bytes).hexdigest(),
        "duration_seconds": duration,
        "usage": result.metadata.get("usage", {}),
    }
    (output_dir / "d062-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory outside project artifacts for the paid result and safe report.")
    args = parser.parse_args()
    try:
        report = run(args.output_dir)
    except Exception as exc:
        report = {"success": False, "error": str(exc) if type(exc).__name__ in {
            "OpenAIImageError", "OpenAIImageTransportError", "ValueError"} else "GPT Image 2 smoke failed."}
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
