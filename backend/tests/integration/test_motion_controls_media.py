"""Real synthetic media: controlled motion, exact frames and audio range semantics."""

import asyncio
from array import array
from dataclasses import replace
import math
import shutil
import subprocess
import wave

from PIL import Image, ImageDraw
import pytest

from app.domain.scene_motion import MotionConfig, resolve_scene_motion
from app.providers.ffmpeg_render import FFmpegRenderer
from tests.unit.test_motion_compatibility import motion_image
from tests.unit.test_timeline import compile_values, media


@pytest.mark.parametrize("zoom,pan,direction,portrait", [
    ("off", "off", None, False), ("subtle", "off", None, False),
    ("medium", "off", None, False), ("off", "subtle", "right", False),
    ("off", "subtle", "up", False), ("off", "medium", "left", False),
    ("subtle", "subtle", "down", False), ("subtle", "subtle", "right", True),
])
def test_real_controlled_motion_dimensions_frames_audio_and_trajectory(tmp_path, zoom, pan, direction, portrait):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Synthetic motion acceptance requires FFmpeg/ffprobe."
    config = MotionConfig(zoom, pan)
    scene = next(str(i) for i in range(100) if resolve_scene_motion(str(i), config).pan_direction == direction)
    source = media(scene, start=1600, end=8000, total=9600)
    image = motion_image(source.image, portrait=portrait)
    timeline = compile_values(replace(source, image=image))
    raster = Image.new("RGB", (image.width, image.height), (60, 90, 130))
    draw = ImageDraw.Draw(raster)
    for x in range(0, image.width, 100):
        draw.line((x, 0, x, image.height), fill=(240, 130, 70), width=12)
    for y in range(0, image.height, 130):
        draw.line((0, y, image.width, y), fill=(70, 220, 100), width=10)
    raster.save(tmp_path / "image-0.png")
    with wave.open(str(tmp_path / "audio-0.wav"), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        samples = array("h", [int(12000 * math.sin(2 * math.pi * 440 * n / 8000))
                              if 1600 <= n < 8000 else 0 for n in range(9600)])
        audio.writeframes(samples.tobytes())
    renderer = FFmpegRenderer(ffmpeg, ffprobe, motion=config)
    result = asyncio.run(renderer.render(timeline, tmp_path, canceled=lambda: False, progress=lambda *_: None))
    assert (result.width, result.height) == ((1080, 1920) if portrait else (1920, 1080))
    assert result.fps == 25 and result.frame_count == 20
    assert result.video_duration == timeline.video_duration
    assert abs(float(result.audio_duration - timeline.duration)) <= 1025 / 48000
    decoded = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "render.mp4"),
        "-vf", "scale=96:54", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=30).stdout
    frame_bytes = 96 * 54 * 3
    assert len(decoded) == 20 * frame_bytes
    assert all(max(decoded[n * frame_bytes:n * frame_bytes + 3]) > 30 for n in range(20))
    first, last = decoded[:frame_bytes], decoded[-frame_bytes:]
    difference = sum(abs(a-b) for a,b in zip(first, last)) / frame_bytes
    assert difference < 1 if config.static else difference > 2
    # Verify audible selected source interval, not the silent prefix/suffix.
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "render.mp4"),
        "-map", "0:a:0", "-f", "s16le", "-ar", "48000", "-ac", "1", "-"],
        capture_output=True, check=True, timeout=30).stdout
    samples = array("h")
    samples.frombytes(raw)
    window = samples[4800:28800]
    assert abs(sum(a < 0 <= b for a,b in zip(window, window[1:])) * 2 - 440) <= 4
    assert max(map(abs, window)) > 8000
