"""Render a D046 proxy snapshot and decode it with the packaged D002 Qt player."""

import argparse
import asyncio
from fractions import Fraction
from hashlib import file_digest
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.application.preview import proxy_cache_key  # noqa: E402
from app.domain.scene_image import SceneImage  # noqa: E402
from app.domain.timeline import (  # noqa: E402
    AudioSpan, OutputTimebase, TimelineClip, TimelineMedia, TimelineRevision,
)
from app.providers.ffmpeg_render import FFmpegRenderer  # noqa: E402


def checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def write_audio(path):
    rate = 48000
    samples = (round(1800 * math.sin(2 * math.pi * (330 if i < rate else 550) * i / rate))
               for i in range(2 * rate))
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def timeline(image_path, audio_path):
    image = SceneImage("image-smoke", "project-smoke", "acceptance-smoke", "scene-smoke",
                       "revision-smoke", image_path.name, checksum(image_path), image_path.stat().st_size,
                       "PNG", 960, 540, "RGB")
    audio = AudioSpan("audio-smoke", checksum(audio_path), "original", 48000, 96000, 12000, 84000)
    media = TimelineMedia("project-smoke", "section-smoke", "revision-smoke", "scene-smoke",
                          "plan-smoke", "acceptance-smoke", "timing-smoke",
                          "measured_sentence_blocks", "selection-smoke", image, audio)
    timebase = OutputTimebase()
    duration = audio.duration
    return TimelineRevision("project-smoke", timebase, "fit",
                            (TimelineClip(media, Fraction(0), 0, timebase.frame_at(duration)),))


async def render_proxy(work, ffmpeg, ffprobe):
    image_path, audio_path = work / "image-0.png", work / "audio-0.wav"
    Image.new("RGB", (960, 540), (35, 90, 180)).save(image_path, format="PNG")
    write_audio(audio_path)
    snapshot = timeline(image_path, audio_path)
    renderer = FFmpegRenderer(ffmpeg, ffprobe, proxy=True)
    progress = []
    result = await renderer.render(snapshot, work, canceled=lambda: False,
                                   progress=lambda *value: progress.append(value))
    return snapshot, renderer, result, progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="D002 standalone .dist directory")
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    work = output / "renderer"
    work.mkdir()
    ffmpeg = shutil.which(args.ffmpeg) if not Path(args.ffmpeg).is_file() else args.ffmpeg
    ffprobe = shutil.which(args.ffprobe) if not Path(args.ffprobe).is_file() else args.ffprobe
    if not ffmpeg or not ffprobe:
        raise FileNotFoundError("FFmpeg and ffprobe are required for the D046 smoke.")
    snapshot, renderer, result, progress = asyncio.run(render_proxy(work, ffmpeg, ffprobe))

    fixtures = output / "fixtures"
    fixtures.mkdir()
    shutil.copyfile(work / "audio-0.wav", fixtures / "tone.wav")
    shutil.copyfile(work / "render.mp4", fixtures / "pattern.mp4")
    executable = args.bundle.resolve() / "spike.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    report = output / "qt-playback.json"
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "QT", "QML")) and key.upper() != "VIRTUAL_ENV"}
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    process = subprocess.run([str(executable), "--fixtures", str(fixtures), "--smoke", "--report", str(report)],
                             cwd=output, env=environment, capture_output=True, timeout=45)
    (output / "qt-playback.stderr.txt").write_bytes(process.stderr)
    playback = json.loads(report.read_text(encoding="utf-8"))
    if process.returncode != 0 or not playback.get("automated_pass"):
        raise RuntimeError(f"Packaged Qt playback failed: {process.returncode}: {playback}")
    evidence = {
        "version": 1,
        "automated_pass": True,
        "timeline_id": snapshot.id,
        "proxy_cache_key": proxy_cache_key(snapshot, renderer.identity()),
        "renderer_identity": renderer.identity(),
        "selected_audio_samples": [12000, 84000],
        "render": result.to_payload() | {"profile": "proxy-mp4-360p25-v1", "width": 640, "height": 360},
        "progress": progress,
        "packaged_qt": playback,
    }
    (output / "d046-smoke.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"PASS: exact proxy rendered and decoded by packaged Qt. Evidence: {output}")


if __name__ == "__main__":
    main()
