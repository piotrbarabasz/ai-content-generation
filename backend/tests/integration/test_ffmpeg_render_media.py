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
from tests.unit.test_motion_compatibility import motion_image, final_image


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("motion", [False, True])
def test_real_colors_and_tones_follow_exact_reordered_source_ranges(tmp_path, reverse, motion):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Mandatory D019 synthetic smoke requires ffmpeg and ffprobe."
    sources = [("red", 44100, 220), ("blue", 48000, 440)]
    if reverse:
        sources.reverse()
    values = []
    for i, (color, rate, tone) in enumerate(sources):
        values.append(media(color, rate=rate, start=rate // 5, end=rate, total=rate * 6 // 5))
        if motion:
            values[-1] = replace(values[-1], image=motion_image(values[-1].image))
        size = (values[-1].image.width, values[-1].image.height)
        Image.new("RGB", size, color).save(tmp_path / f"image-{i}.png")
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
    assert (result.width, result.height) == ((1920, 1080) if motion else (1280, 720))
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
    master = motion_image(source.image)
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


def test_portrait_delivery_encodes_exact_portrait_proxy_size(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = media("portrait")
    image = motion_image(source.image, portrait=True)
    timeline = compile_values(replace(source, image=image))
    assert FFmpegRenderer(ffmpeg, ffprobe).dimensions(timeline) == ("fhd", 1080, 1920)
    assert FFmpegRenderer(ffmpeg, ffprobe, proxy=True).dimensions(timeline) == ("proxy", 360, 640)
    Image.new("RGB", (1350, 2400), "green").save(tmp_path / "image-0.png")
    with wave.open(str(tmp_path / "audio-0.wav"), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(array("h", [0] * 8000).tobytes())
    result = asyncio.run(FFmpegRenderer(ffmpeg, ffprobe, proxy=True).render(
        timeline, tmp_path, canceled=lambda: False, progress=lambda *_: None))
    assert (result.width, result.height, result.fps, result.frame_count) == (360, 640, 25, 25)
    assert result.video_duration == timeline.video_duration


@pytest.mark.parametrize("version", ["1", "2"])
def test_legacy_final_image_stays_static_and_historical_request_keeps_720p(tmp_path, version):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = media("static", end=3200, total=3200)
    timeline = compile_values(replace(source, image=final_image(source.image)))
    Image.new("RGB", (1920, 1080), "orange").save(tmp_path / "image-0.png")
    with wave.open(str(tmp_path / "audio-0.wav"), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(array("h", [0] * 3200).tobytes())
    renderer = FFmpegRenderer(ffmpeg, ffprobe).for_request(version)
    result = asyncio.run(renderer.render(timeline, tmp_path, canceled=lambda: False, progress=lambda *_: None))
    assert (result.width, result.height) == ((1280, 720) if version == "1" else (1920, 1080))
    assert result.frame_count == 10 and result.video_duration == timeline.video_duration
    assert "zoompan" not in (tmp_path / "filters.txt").read_text()
