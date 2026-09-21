"""D046 exact scene range, proxy cache and selected-image invalidation."""

import asyncio
import json
from types import SimpleNamespace
import wave

import pytest

from app.application.preview import PreviewService
from app.application.result_publication import ResultPublicationService
from app.application.video_render import VideoRenderService
from app.desktop.timeline_composition import compose_timeline
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.ffmpeg_render import FFmpegRenderer
from app.storage.local_store import LocalArtifactStore
from app.storage.preview import ProjectPreviewMedia
from app.storage.video_render import ProjectVideoRender, RenderResultIndex
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared  # noqa: F401


class Process:
    def __init__(self, timeline, width=640, height=360):
        self.timeline = timeline
        self.width, self.height = width, height
        self.calls = []

    async def run(self, command, *, cwd, canceled=lambda: False, on_line=lambda _: None):
        self.calls.append(command)
        if "-show_streams" in command:
            return json.dumps({"format": {"format_name": "mov,mp4"}, "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": self.width, "height": self.height,
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
    assert "ultrafast" in encode and "28" in encode

    first.path.write_bytes(b"corrupt cache")
    repaired = asyncio.run(preview.service.proxy(preview.timeline))
    assert repaired.current and not repaired.cached and len(preview.process.calls) == 6

    intake, source, choices = preview.prepared[6], preview.prepared[7], preview.prepared[8]
    from tests.integration.test_image_intake import encoded
    source.write_bytes(encoded(size=(23, 17)))
    intake.import_and_select(preview.prepared[4].id, preview.timeline.clips[0].media.scene_id, source,
                             expected_selection_id=choices[0][1].id)
    assert not preview.service.is_current(preview.timeline)
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(preview.service.proxy(preview.timeline))
    assert len(preview.process.calls) == 6
    assert preview.prepared[2].calls == calls


def test_measured_trim_is_shared_by_proxy_and_final_export(setup, tmp_path):
    from app.application.image_intake import ImageIntakeService
    from app.application.scene_planning import ScenePlanningService
    from app.application.timeline import TimelineSceneInput
    from app.domain.section_audio import SectionAudio
    from app.storage.scene_images import ProjectSceneImages
    from app.storage.scene_plans import ProjectScenePlans
    from app.tts.scene_sources import sentence_sources
    from tests.integration.test_image_intake import encoded
    from tests.integration.test_speech_boundary import publish

    section, manifest, _, _ = publish(setup, text="First two three four five six. Second sentence!")
    plans = ScenePlanningService(ProjectScenePlans(setup[0].repository, setup[3]), sentence_sources)
    accepted = plans.accept(plans.suggest(section).id, reviewer_id="editor")
    audio = SectionAudio.from_manifest(manifest)
    timing = plans.retime(accepted.id, section, audio)
    scene_id = timing.scenes[0].scene_id
    source = tmp_path / "trimmed.png"
    source.write_bytes(encoded())
    ImageIntakeService(ProjectSceneImages(setup[0].repository, setup[3])).import_and_select(
        accepted.id, scene_id, source, expected_selection_id=None)
    timeline_service = compose_timeline(setup[0])
    edit = timeline_service.append(TimelineSceneInput(timing.id, scene_id, "original"), expected=None)
    start = audio.speech_boundary_map.blocks[1].start_frame
    edit = timeline_service.set_range(scene_id, start, audio.frame_count, expected=edit.id)
    assert edit.timeline.clips[0].media.audio.start_sample == start

    index = RenderResultIndex(setup[0].repository, setup[1])
    store = LocalArtifactStore(index.root, index=index)
    render_media = ProjectVideoRender(index, store)
    executable = tmp_path / "trim-renderer.exe"
    executable.write_bytes(b"fixture executable")
    proxy_process = Process(edit.timeline)
    preview = PreviewService(timeline_service.current, ProjectPreviewMedia(render_media),
                             FFmpegRenderer(executable, executable, process=proxy_process, proxy=True))
    result = asyncio.run(preview.proxy(edit.timeline))
    assert result.current and "start_sample=" + str(start) in next(
        path.read_text(encoding="utf-8") for path in (setup[0].repository.workspace / "work/render").glob("*/filters.txt"))

    final_process = Process(edit.timeline, 1280, 720)
    final = VideoRenderService(ResultPublicationService(index, store), JobCoordinator(setup[1]), render_media,
                               FFmpegRenderer(executable, executable, process=final_process))
    attempt = final.enqueue(edit.timeline)
    claim = final.coordinator.claim_next("trimmed-final")
    assert claim.id == attempt.id
    publication = asyncio.run(final.run(claim))
    assert publication.selected_at_publication
