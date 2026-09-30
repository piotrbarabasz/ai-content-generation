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


def test_motion_master_renders_fhd_with_deterministic_camera_motion(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = media("motion", rate=8000, end=16000, total=16000)
    master = replace(source.image, width=2400, height=1350, provenance="motion_master", lineage_version=3,
        source_artifact_id="source-motion", source_checksum="c" * 64, source_width=640, source_height=360,
        target_profile="fhd", master_width=2400, master_height=1350, delivery_width=1920, delivery_height=1080,
        overscan_policy="5:4", native_model_scale=4, native_width=2560, native_height=1440,
        final_resize_method="Lanczos")
    source = replace(source, image=master)
    timeline = compile_values(source, fit_policy="fill")
    # Asymmetric synthetic master makes camera movement visible after H.264 encoding.
    image = Image.new("RGB", (2400, 1350))
    pixels = image.load()
    for x in range(2400):
        color = (255, 30, 20) if x < 800 else ((20, 220, 40) if x < 1600 else (20, 40, 255))
        for y in range(1350):
            pixels[x, y] = color
    image.save(tmp_path / "image-0.png")
    with wave.open(str(tmp_path / "audio-0.wav"), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(array("h", [0] * 16000).tobytes())
    renderer = FFmpegRenderer(ffmpeg, ffprobe)
    assert FFmpegRenderer(ffmpeg, ffprobe, proxy=True).dimensions(timeline) == ("proxy", 640, 360)
    result = asyncio.run(renderer.render(timeline, tmp_path, canceled=lambda: False, progress=lambda *_: None))
    assert (result.width, result.height, result.fps) == (1920, 1080, 25)
    assert result.frame_count == 50 and result.profile == "fhd"
    decoded = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "render.mp4"),
        "-vf", "select='eq(n,0)+eq(n,49)',scale=64:36", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=30).stdout
    frame_bytes = 64 * 36 * 3
    assert len(decoded) == 2 * frame_bytes
    assert sum(a != b for a, b in zip(decoded[:frame_bytes], decoded[frame_bytes:])) > 100
    corners = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "render.mp4"),
        "-frames:v", "1", "-vf", "crop=2:2:0:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=30).stdout
    assert len(corners) == 12 and max(corners) > 15


def test_portrait_delivery_resolves_portrait_proxy_size(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = media("portrait")
    image = replace(source.image, width=1350, height=2400, provenance="motion_master", lineage_version=3,
        source_artifact_id="source-portrait", source_checksum="d" * 64, source_width=360, source_height=640,
        target_profile="fhd", master_width=1350, master_height=2400, delivery_width=1080, delivery_height=1920,
        overscan_policy="5:4", native_model_scale=4, native_width=1440, native_height=2560,
        final_resize_method="Lanczos")
    timeline = compile_values(replace(source, image=image))
    assert FFmpegRenderer(ffmpeg, ffprobe).dimensions(timeline) == ("fhd", 1080, 1920)
    assert FFmpegRenderer(ffmpeg, ffprobe, proxy=True).dimensions(timeline) == ("proxy", 360, 640)
