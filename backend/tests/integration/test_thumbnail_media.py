"""Short real CPU FFmpeg frame extraction and local thumbnail layout acceptance."""

import asyncio
from hashlib import sha256
from io import BytesIO
import shutil
import subprocess

from PIL import Image

from app.providers.local_thumbnail import LocalThumbnailBuilder


def test_three_actual_distinct_frames_and_deterministic_readable_jpg_layouts(tmp_path):
    ffmpeg = shutil.which("ffmpeg"); assert ffmpeg
    source = tmp_path / "source.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=4",
                    "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", str(source)], check=True)
    before = sha256(source.read_bytes()).hexdigest()
    builder = LocalThumbnailBuilder(ffmpeg)
    frames = asyncio.run(builder.frames(tmp_path, [20, 50, 80]))
    assert len({sha256(raw).hexdigest() for raw in frames}) == 3
    options = dict(headline="The Internet Goes Dark", position="bottom", font_size=100)
    candidates = builder.layouts(frames[1], **options)
    assert candidates == builder.layouts(frames[1], **options)
    assert len({sha256(raw).hexdigest() for _, raw, _ in candidates}) == 3
    for style, raw, size in candidates:
        with Image.open(BytesIO(raw)) as image:
            image.load(); assert image.size == size == (640, 360) and image.mode == "RGB" and image.format == "JPEG"
    assert sha256(source.read_bytes()).hexdigest() == before
    (tmp_path / "seek").mkdir(); shutil.copyfile(source, tmp_path / "seek/source.mp4")
    fast = asyncio.run(builder.frames(tmp_path / "seek", [20, 50, 80], fps=25))
    assert fast == frames  # Accurate seeking selects the same decoded CFR frames.
