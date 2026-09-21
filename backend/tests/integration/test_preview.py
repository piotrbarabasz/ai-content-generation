"""D046 exact scene range, proxy cache and selected-image invalidation."""

import asyncio
import json
from types import SimpleNamespace
import wave

import pytest

from app.application.preview import PreviewService
from app.jobs.repository import JobRepository
from app.providers.ffmpeg_render import FFmpegRenderer
from app.storage.local_store import LocalArtifactStore
from app.storage.preview import ProjectPreviewMedia
from app.storage.video_render import ProjectVideoRender, RenderResultIndex
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared  # noqa: F401


class Process:
    def __init__(self, timeline):
        self.timeline = timeline
        self.calls = []

    async def run(self, command, *, cwd, canceled=lambda: False, on_line=lambda _: None):
        self.calls.append(command)
        if "-show_streams" in command:
            return json.dumps({"format": {"format_name": "mov,mp4"}, "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360,
                 "pix_fmt": "yuv420p", "avg_frame_rate": "25/1",
                 "nb_read_frames": str(self.timeline.total_frames),
                 "duration": str(float(self.timeline.video_duration))},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1,
                 "nb_read_frames": "2", "duration": str(float(self.timeline.duration))}]}).encode()
        if "null" not in command:
            (cwd / "render.mp4").write_bytes(b"synthetic validated proxy")
            on_line("out_time_us=1000")
        return b""


@pytest.fixture
def preview(setup, prepared, tmp_path):
    timeline = prepared[9].compile(setup[0].project.id, prepared[10])
    index = RenderResultIndex(setup[0].repository, JobRepository(setup[0].repository))
    store = LocalArtifactStore(index.root, index=index)
    media = ProjectPreviewMedia(ProjectVideoRender(index, store))
    executable = tmp_path / "ffmpeg-fixture.exe"
    executable.write_bytes(b"fixture executable")
    process = Process(timeline)
    renderer = FFmpegRenderer(executable, executable, process=process, proxy=True)
    active = SimpleNamespace(value=SimpleNamespace(timeline=timeline))
    service = PreviewService(lambda: active.value, media, renderer)
    return SimpleNamespace(timeline=timeline, media=media, process=process, service=service,
                           active=active, prepared=prepared)


def test_scene_preview_uses_exact_selected_sample_range(preview):
    clip = preview.timeline.clips[-1]
    result = preview.service.scene(preview.timeline, clip.media.scene_id)
    assert result.timeline_id == preview.timeline.id
    assert result.frame_count == clip.media.audio.end_sample - clip.media.audio.start_sample
    with wave.open(str(result.audio_path), "rb") as source:
        assert source.getnframes() == result.frame_count
        assert source.getframerate() == clip.media.audio.sample_rate
        assert source.getnchannels() == 1 and source.getsampwidth() == 2
    assert result.image_path.is_file()


def test_proxy_cache_uses_lower_profile_and_image_change_invalidates_without_tts(preview):
    calls = list(preview.prepared[2].calls)
    first = asyncio.run(preview.service.proxy(preview.timeline))
    second = asyncio.run(preview.service.proxy(preview.timeline))
    assert first.current and not first.cached and second.current and second.cached
    assert len(preview.process.calls) == 3
    encode = preview.process.calls[0]
    script = (first.path.parent.parent / "missing")  # Cache path itself is the public result.
    assert "ultrafast" in encode and "28" in encode

    intake, source, choices = preview.prepared[6], preview.prepared[7], preview.prepared[8]
    source.write_bytes(source.read_bytes() + b"changed image bytes")
    # Use a new valid image instead of the deliberately corrupted byte sequence.
    from tests.integration.test_image_intake import encoded
    source.write_bytes(encoded(size=(23, 17)))
    intake.import_and_select(preview.prepared[4].id, preview.timeline.clips[0].media.scene_id, source,
                             expected_selection_id=choices[0][1].id)
    assert not preview.service.is_current(preview.timeline)
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(preview.service.proxy(preview.timeline))
    assert len(preview.process.calls) == 3
    assert preview.prepared[2].calls == calls

