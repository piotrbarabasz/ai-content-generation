"""Short synthetic FFmpeg checks, without narration/image providers or a GPU."""

import asyncio
from array import array
from fractions import Fraction
from hashlib import sha256
import json
import shutil
import subprocess

from PIL import Image
import pytest

from app.domain.branding import BrandingConfig
from app.providers.ffmpeg_finishing import FFmpegFinisher


@pytest.fixture
def source(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Synthetic finishing acceptance requires existing FFmpeg/FFprobe."
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=2", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]", "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(tmp_path / "source.mp4")], check=True)
    Image.new("RGBA", (100, 100), (255, 255, 255, 120)).save(tmp_path / "logo.png")
    renderer = FFmpegFinisher(ffmpeg, ffprobe)
    measured = asyncio.run(renderer.probe(tmp_path, "source.mp4", lambda: False))
    evidence = {"fully_decoded": True, "width": 640, "height": 360, "frame_count": 100, "fps": 25,
                "video_duration": [measured["video_duration"].numerator, measured["video_duration"].denominator],
                "audio_duration": [measured["audio_duration"].numerator, measured["audio_duration"].denominator]}
    return tmp_path, renderer, evidence


@pytest.mark.parametrize("intro,outro,after", [(True, False, False), (False, True, False), (True, True, False), (True, True, True)])
def test_real_finish_duration_order_and_complete_narration(source, intro, outro, after):
    root, renderer, evidence = source
    before = sha256((root / "source.mp4").read_bytes()).hexdigest()
    config = BrandingConfig(channel_name="Atlas", intro=intro, intro_seconds=3, outro=outro, outro_seconds=10)
    boundary = {"video_frame": 50, "audio_sample": 96000} if after else None
    result = asyncio.run(renderer.finish(root, evidence, config, boundary, canceled=lambda: False, progress=lambda *_: None))
    assert result["frame_count"] == (4 + config.extra_seconds) * 25
    assert Fraction(*result["video_duration"]) == 4 + config.extra_seconds
    assert sha256((root / "source.mp4").read_bytes()).hexdigest() == before
    # Decode interior main-video frames: no truncation, duplication or reordering.
    for color, second in (("red", 1 + (3 if intro and not after else 0)), ("blue", 3 + (3 if intro else 0))):
        raw = subprocess.check_output([str(renderer.ffmpeg), "-v", "error", "-ss", str(second), "-i", str(root / "branded.mp4"),
            "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        dominant = 0 if color == "red" else 2
        assert raw[dominant] > 200 and raw[2 if dominant == 0 else 0] < 40
    payload = subprocess.check_output([str(renderer.ffmpeg), "-v", "error", "-i", str(root / "branded.mp4"),
        "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"])
    samples = array("h"); samples.frombytes(payload)
    main_interiors = [1 + (3 if intro and not after else 0), 3 + (3 if intro else 0)]
    for second, frequency in zip(main_interiors, (220, 440)):
        window = samples[int((second - .25) * 48000):int((second + .25) * 48000)]
        # AAC can ring near zero; count full-amplitude cycles with hysteresis.
        crossings, negative = 0, False
        for sample in window:
            if sample < -300:
                negative = True
            elif sample > 300 and negative:
                crossings += 1
                negative = False
        assert abs(crossings * 2 - frequency) <= 5 and max(map(abs, window)) > 1000
    if intro:
        silent_second = 3 if after else 1
        window = samples[silent_second * 48000:(silent_second + 1) * 48000]
        assert max(map(abs, window)) < 50


def test_source_delivery_dimensions_and_rate_are_preserved_at_1080p(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=navy:s=1920x1080:r=30:d=1",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", "-c:v", "libx264", "-preset", "ultrafast",
        "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(tmp_path / "source.mp4")], check=True)
    Image.new("RGBA", (80, 80), "white").save(tmp_path / "logo.png")
    renderer = FFmpegFinisher(ffmpeg, ffprobe)
    m = asyncio.run(renderer.probe(tmp_path, "source.mp4", lambda: False))
    evidence = {"fully_decoded": True, "width": 1920, "height": 1080, "frame_count": 30, "fps": 30,
        "video_duration": [m["video_duration"].numerator, m["video_duration"].denominator],
        "audio_duration": [m["audio_duration"].numerator, m["audio_duration"].denominator]}
    result = asyncio.run(renderer.finish(tmp_path, evidence, BrandingConfig(channel_name="Atlas", intro=True, intro_seconds=2),
        None, canceled=lambda: False, progress=lambda *_: None))
    assert (result["width"], result["height"], result["fps"], result["frame_count"]) == (1920, 1080, "30", 90)
