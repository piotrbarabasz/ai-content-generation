"""Mandatory real media checks: cuts, sample ranges and mixed source rates."""

import asyncio
from array import array
from dataclasses import replace
import math
import shutil
import subprocess
import wave

from PIL import Image
import pytest

from app.providers.ffmpeg_render import FFmpegRenderer
from tests.unit.test_timeline import compile_values, media


@pytest.mark.parametrize("reverse", [False, True])
def test_real_colors_and_tones_follow_exact_reordered_source_ranges(tmp_path, reverse):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Mandatory D019 synthetic smoke requires ffmpeg and ffprobe."
    sources = [("red", 44100, 220), ("blue", 48000, 440)]
    if reverse:
        sources.reverse()
    values = []
    for i, (color, rate, tone) in enumerate(sources):
        values.append(media(color, rate=rate, start=rate // 5, end=rate, total=rate * 6 // 5))
        Image.new("RGB", (32, 48), color).save(tmp_path / f"image-{i}.png")
        # The selected interval has a tone; source prefix/suffix have silence.
        samples = array("h", [int(10000 * math.sin(2 * math.pi * tone * n / rate))
                              if rate // 5 <= n < rate else 0 for n in range(rate * 6 // 5)])
        with wave.open(str(tmp_path / f"audio-{i}.wav"), "wb") as target:
            target.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            target.writeframes(samples.tobytes())
    timeline = compile_values(*values, fit_policy="fill")
    provider = FFmpegRenderer(ffmpeg, ffprobe)
    progress = []
    result = asyncio.run(provider.render(timeline, tmp_path, canceled=lambda: False,
                                        progress=lambda *event: progress.append(event)))
    assert result.frame_count == 40 and result.video_duration == timeline.video_duration
    assert progress[0][0] == "encoding" and progress[-1] == ("validated", 1, 1)
    for time, (color, _, _) in zip(("0.1", "0.9"), sources):
        frame = subprocess.run([ffmpeg, "-v", "error", "-ss", time, "-i", str(tmp_path / "render.mp4"),
                                "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                               capture_output=True, check=True, timeout=20).stdout
        assert len(frame) == 3
        assert frame[0 if color == "red" else 2] > 230
        assert frame[2 if color == "red" else 0] < 20
    audio = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "render.mp4"),
                            "-map", "0:a:0", "-f", "s16le", "-ar", "48000", "-ac", "1", "-"],
                           capture_output=True, check=True, timeout=20).stdout
    samples = array("h")
    samples.frombytes(audio)
    for offset, (_, _, expected_tone) in zip((0, 38400), sources):
        window = samples[offset + 4800:offset + 28800]  # Interior half-second.
        crossings = sum(a < 0 <= b for a, b in zip(window, window[1:]))
        assert abs(crossings * 2 - expected_tone) <= 4
        assert max(map(abs, window)) > 5000
    # A truncated formerly valid MP4 cannot pass the same provider gate.
    path = tmp_path / "render.mp4"
    path.write_bytes(path.read_bytes()[:path.stat().st_size // 2])
    with pytest.raises((ValueError, RuntimeError, KeyError, StopIteration)):
        asyncio.run(provider.validate(timeline, tmp_path, canceled=lambda: False, progress=lambda *_: None))
