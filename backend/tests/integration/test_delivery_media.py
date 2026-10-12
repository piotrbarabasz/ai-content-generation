"""Real CPU FFmpeg mastering acceptance with short synthetic SDR media."""

import asyncio
from fractions import Fraction
from hashlib import sha256
import shutil
import subprocess

import pytest

from app.domain.delivery import DeliveryConfig
from app.domain.branding import BrandingConfig
from app.providers.ffmpeg_finishing import FFmpegFinisher
from PIL import Image
from app.providers.ffmpeg_delivery import FFmpegDeliveryExporter


@pytest.mark.parametrize("channels, silent, normalize, width, height, fps", [
    (1, False, True, 640, 360, 25), (2, False, True, 640, 360, 25),
    (1, True, True, 640, 360, 25), (1, False, False, 640, 360, 25),
    (1, False, True, 1920, 1080, 30)])
def test_real_delivery_preserves_media_and_measures_final_aac(tmp_path, channels, silent, normalize, width, height, fps):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "D069 requires existing FFmpeg for synthetic quality acceptance."
    audio = "anullsrc=r=48000:cl=mono" if silent else "sine=frequency=440:sample_rate=48000:duration=6"
    command = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"color=c=navy:s={width}x{height}:r={fps}:d=6",
        "-f", "lavfi", "-i", audio, "-t", "6", "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
        "-c:a", "aac", "-ar", "48000", "-ac", str(channels), "-movflags", "+faststart", str(tmp_path / "source.mp4")]
    subprocess.run(command, check=True)
    source_bytes = sha256((tmp_path / "source.mp4").read_bytes()).hexdigest()
    exporter = FFmpegDeliveryExporter(ffmpeg, ffprobe)
    source = asyncio.run(exporter.probe(tmp_path, "source.mp4", lambda: False))
    evidence = {"fully_decoded": True, "width": width, "height": height, "frame_count": 6 * fps, "fps": fps,
        "video_duration": [source["video_duration"].numerator, source["video_duration"].denominator],
        "audio_duration": [source["audio_duration"].numerator, source["audio_duration"].denominator]}
    result = asyncio.run(exporter.export(tmp_path, evidence, DeliveryConfig(normalize=normalize),
                                        canceled=lambda: False, progress=lambda *_: None))
    assert (result["width"], result["height"], result["fps"], result["frame_count"], result["channels"], result["sample_rate"]) == (width, height, str(fps), 6 * fps, channels, 48000)
    assert result["profile"] == "High" and result["progressive"] and result["packet_timestamps_verified"]
    assert Fraction(*result["video_duration"]) == 6
    assert abs(Fraction(*result["audio_duration"]) - 6) <= Fraction(1024, 48000)
    assert result["color"] == source["color"]
    assert result["video_bitrate"] > 0 and result["audio_bitrate"] > 0
    assert sha256((tmp_path / "source.mp4").read_bytes()).hexdigest() == source_bytes
    if silent:
        assert result["status"] == "REVIEW" and not result["normalization_applied"]
        assert result["after"]["integrated_lufs"] is None
    else:
        assert result["status"] == "PASS"
        assert result["normalization_applied"] == normalize
        assert result["after"]["true_peak_dbtp"] <= -1
        if normalize:
            assert abs(result["after"]["integrated_lufs"] + 16) <= 1
            assert result["normalization_type"] in {"linear", "dynamic"}


def test_real_delivery_accepts_d068_branding_output_without_rebuilding_it(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=teal:s=640x360:r=25:d=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6", "-t", "6", "-c:v", "libx264",
        "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(tmp_path / "source.mp4")], check=True)
    Image.new("RGBA", (100, 100), (255, 255, 255, 150)).save(tmp_path / "logo.png")
    finisher = FFmpegFinisher(ffmpeg, ffprobe)
    measured = asyncio.run(finisher.probe(tmp_path, "source.mp4", lambda: False))
    evidence = {"fully_decoded": True, "width": 640, "height": 360, "frame_count": 150, "fps": 25,
        "video_duration": [measured["video_duration"].numerator, measured["video_duration"].denominator],
        "audio_duration": [measured["audio_duration"].numerator, measured["audio_duration"].denominator]}
    branded = asyncio.run(finisher.finish(tmp_path, evidence, BrandingConfig(channel_name="Atlas", intro=True,
        intro_seconds=2, outro=True, outro_seconds=10), None, canceled=lambda: False, progress=lambda *_: None))
    before = sha256((tmp_path / "branded.mp4").read_bytes()).hexdigest()
    stage = tmp_path / "delivery-stage"; stage.mkdir()
    shutil.copyfile(tmp_path / "branded.mp4", stage / "source.mp4")
    exporter = FFmpegDeliveryExporter(ffmpeg, ffprobe)
    result = asyncio.run(exporter.export(stage, branded, DeliveryConfig(source="branded"),
                                        canceled=lambda: False, progress=lambda *_: None))
    assert result["status"] == "PASS" and result["frame_count"] == branded["frame_count"]
    assert abs(Fraction(*result["audio_duration"]) - Fraction(*branded["audio_duration"])) <= Fraction(1024, 48000)
    assert sha256((tmp_path / "branded.mp4").read_bytes()).hexdigest() == before
