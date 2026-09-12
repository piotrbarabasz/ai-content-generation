"""Generate original synthetic media; FFmpeg is required only on the build host."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import wave


def write_tone(path: Path) -> None:
    rate = 48000
    samples = (round(2000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(2 * rate))
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def generate(output: Path, ffmpeg: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_tone(output / "tone.wav")
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=2",
        "-i", str(output / "tone.wav"), "-t", "2", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-threads", "1", "-c:a", "aac", "-b:a", "96k",
        "-map_metadata", "-1", "-movflags", "+faststart", str(output / "pattern.mp4"),
    ], check=True, timeout=60)
    version = subprocess.run([ffmpeg, "-version"], check=True, capture_output=True,
                             text=True, timeout=10).stdout.splitlines()[0]
    manifest = {
        "provenance": "Original synthetic sine wave and FFmpeg testsrc2; no third-party recordings.",
        "permission": "Generated fixtures may be used, copied, modified and redistributed without restriction.",
        "tone": "440 Hz, 48000 Hz mono PCM16, amplitude 2000/32768, 2 seconds",
        "video": "testsrc2, 320x180, 24 fps, H.264 yuv420p and AAC, 2 seconds",
        "generator_ffmpeg": version,
        "sha256": {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                   for name in ("tone.wav", "pattern.mp4")},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="Generated fixture directory; named fixtures are replaced")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    generate(args.output.resolve(), args.ffmpeg)
