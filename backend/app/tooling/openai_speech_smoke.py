"""Opt-in short paid preview; offline estimate unless --live and --budget-usd are supplied."""
import argparse
import asyncio
import json
from pathlib import Path

from app.domain.narration import NarrationSettings
from app.storage.narration import estimate_chunk, PRICING


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="History turns on decisions made under pressure.")
    parser.add_argument("--voice", default="cedar")
    parser.add_argument("--speed", type=float, default=1.)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args(argv)
    if len(args.text) > 400:
        parser.error("Smoke preview is limited to 400 characters.")
    settings = NarrationSettings(voice=args.voice, speed=args.speed)
    from app.providers.openai_tts import OpenAITTSProvider
    OpenAITTSProvider(settings=settings).validate_text(args.text)
    print(json.dumps({"estimate": estimate_chunk(args.text, settings), "pricing": PRICING, "paid": args.live}), flush=True)
    if not args.live:
        return 0
    if args.budget_usd is None or args.workspace is None:
        parser.error("--live requires --budget-usd and a new --workspace.")
    from app.environment import load_application_environment
    from app.application.projects import ProjectSession
    from app.desktop.product_composition import compose_installed_audio
    from app.storage.project_repository import ProjectRepository
    load_application_environment()
    async def run():
        with ProjectSession.create(args.workspace, repository_factory=ProjectRepository, name="OpenAI speech smoke", language="en") as session:
            audio = compose_installed_audio(session)
            audio.narration.save_project(settings)
            choice = audio.choices("en")[0]
            estimate = audio.estimate_preview(choice, args.text)
            audio.approve_operation(estimate, args.budget_usd, max_attempts=1)
            result = await audio.preview(choice, args.text)
            path = args.workspace.resolve() / "speech-preview.wav"
            path.write_bytes(result.payload)
            print(json.dumps({"result": result.label, "preview": str(path)}))
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
