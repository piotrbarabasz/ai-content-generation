"""D019 publication failures plus mandatory synthetic real FFmpeg smoke."""

import asyncio
from dataclasses import replace
from fractions import Fraction
import json
import shutil
from types import SimpleNamespace

import pytest

from app.application.result_publication import ResultPublicationService
from app.application.video_render import VideoRenderService
from app.domain.generation_job import AttemptStatus
from app.jobs.coordinator import JobCoordinator
from app.providers.ffmpeg_render import FFmpegRenderer
from app.runtime.media_process import RenderCanceled
from app.storage.local_store import LocalArtifactStore
from app.storage.video_render import ProjectVideoRender, RenderResultIndex
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared, snapshot  # noqa: F401


class Process:
    def __init__(self, timeline):
        self.timeline, self.calls, self.fail, self.during = timeline, [], None, lambda: None

    async def run(self, command, *, cwd, canceled=lambda: False, on_line=lambda _: None):
        self.calls.append(command)
        phase = "probe" if "-show_streams" in command else "decode" if "null" in command else "encode"
        if phase == "encode":
            (cwd / "render.mp4").write_bytes(b"fake process fixture")
            self.during()
            on_line("out_time_us=100000")
        if canceled():
            raise RenderCanceled("fixture canceled")
        if self.fail == phase:
            raise RuntimeError("fixture nonzero exit: " + phase)
        if phase != "probe":
            return b""
        if self.fail == "json":
            return b"{truncated"
        result = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
             "pix_fmt": "yuv420p", "avg_frame_rate": "25/1", "nb_read_frames": str(self.timeline.total_frames),
             "duration": str(float(self.timeline.video_duration))},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1,
             "nb_read_frames": "25", "duration": str(float(self.timeline.duration))}]}
        changes = {"width": 1920, "nb_read_frames": "1", "pix_fmt": "yuv444p", "duration": "999"}
        if self.fail in changes:
            result["streams"][0][self.fail] = changes[self.fail]
        if self.fail == "audio_duration":
            result["streams"][1]["duration"] = "0.001"
        if self.fail == "no_audio":
            result["streams"].pop()
        return json.dumps(result).encode()


@pytest.fixture
def render(setup, prepared, tmp_path):
    timeline = prepared[9].compile(setup[0].project.id, prepared[10])
    index = RenderResultIndex(setup[0].repository, setup[1])
    store = LocalArtifactStore(index.root, index=index)
    coordinator = JobCoordinator(setup[1])
    process = Process(timeline)
    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"not executed; injected process fixture")
    provider = FFmpegRenderer(executable, executable, process=process)
    adapter = ProjectVideoRender(index, store)
    service = VideoRenderService(ResultPublicationService(index, store), coordinator, adapter, provider)
    return SimpleNamespace(timeline=timeline, index=index, store=store, coordinator=coordinator, process=process,
                           provider=provider, adapter=adapter, service=service, prepared=prepared)


def claim(r):
    attempt = r.service.enqueue(r.timeline)
    owned = r.coordinator.claim_next("render-fixture")
    assert owned.id == attempt.id
    return owned


def test_render_publication_replay_retains_media_and_exact_snapshot(render):
    r = render
    before = snapshot(r.store)
    owned = claim(r)
    result = asyncio.run(r.service.run(owned))
    assert result.selected_at_publication
    assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.COMPLETED
    manifest = next(m for m in r.store.list_artifacts() if m.artifact_id == result.artifact_id)
    assert manifest.artifact_type == "video_render" and manifest.metadata["render"]["timeline_id"] == r.timeline.id
    assert manifest.metadata["render"]["fully_decoded"] is True
    assert {key: snapshot(r.store)[key] for key in before} == before
    assert len(r.process.calls) == 3
    assert asyncio.run(r.service.run(owned)) == result and len(r.process.calls) == 3
    job = r.coordinator.repository.get_job(owned.job_id)
    assert json.loads(job.request.settings_json)["timeline"] == r.timeline.to_payload()


@pytest.mark.parametrize("failure", ["encode", "probe", "decode", "json", "width", "nb_read_frames",
                                     "pix_fmt", "duration", "audio_duration", "no_audio"])
def test_nonzero_truncated_or_wrong_media_never_publishes_success(render, failure):
    r = render
    prior = asyncio.run(r.service.run(claim(r)))
    before = snapshot(r.store)
    owned = claim(r)
    r.process.fail = failure
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(r.service.run(owned))
    assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.FAILED
    assert r.index.selected()["project:video_render"] == prior.artifact_id
    assert snapshot(r.store) == before


@pytest.mark.parametrize("when", ["before", "during", "publication"])
def test_cancel_never_publishes_and_preserves_prior_result(render, when):
    r = render
    prior = asyncio.run(r.service.run(claim(r)))
    before = snapshot(r.store)
    owned = claim(r)
    if when == "before":
        r.coordinator.cancel(owned.id)
    elif when == "during":
        r.process.during = lambda: r.coordinator.cancel(owned.id)
    else:
        publish = r.service.publication.publish

        def late(*args, **kwargs):
            r.coordinator.cancel(owned.id)
            return publish(*args, **kwargs)

        r.service.publication.publish = late
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(r.service.run(owned))
    assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.CANCELED
    assert r.index.selected()["project:video_render"] == prior.artifact_id and snapshot(r.store) == before


@pytest.mark.parametrize("change", ["section", "image", "superseded"])
def test_late_changes_retain_valid_results_without_promotion(render, setup, change):
    r = render
    owned = claim(r)

    def during():
        if change == "section":
            setup[0].edit_section(r.prepared[0].section_id, text="Changed source.")
        elif change == "image":
            intake, source, image_choices = r.prepared[6], r.prepared[7], r.prepared[8]
            intake.import_and_select(r.prepared[4].id, r.timeline.clips[0].media.scene_id, source,
                                     expected_selection_id=image_choices[0][1].id)
        else:
            r.service.enqueue(r.timeline)

    r.process.during = during
    result = asyncio.run(r.service.run(owned))
    assert not result.selected_at_publication
    assert result.reason in ("obsolete_revisions", "obsolete_inputs", "superseded_generation")
    assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.COMPLETED
    assert any(m.artifact_id == result.artifact_id for m in r.store.list_artifacts())


def test_changed_executable_or_corrupted_source_fails_before_render(render):
    r = render
    owned = claim(r)
    r.provider.ffmpeg.write_bytes(b"replaced executable")
    with pytest.raises(ValueError, match="identity"):
        asyncio.run(r.service.run(owned))
    assert not r.process.calls
    owned = claim(r)
    image_id = r.timeline.clips[0].media.image.artifact_id
    manifest = next(m for m in r.store.list_artifacts() if m.artifact_id == image_id)
    (r.store.root / manifest.storage_key).write_bytes(b"damaged")
    with pytest.raises(ValueError):
        asyncio.run(r.service.run(owned))
    assert not r.process.calls


def test_output_mutation_after_probe_cannot_publish(render):
    r = render
    owned = claim(r)
    output = r.adapter.output

    def changed(root, timeline, result):
        (root / "render.mp4").write_bytes(b"tampered output")
        return output(root, timeline, result)

    r.adapter.output = changed
    with pytest.raises(ValueError, match="bytes changed"):
        asyncio.run(r.service.run(owned))
    assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.FAILED


def test_bytes_changed_during_publication_fail_checksum_gate(render):
    import io
    r = render
    owned = claim(r)
    original = r.store.import_stream
    def altered(name, source, metadata):
        return original(name, io.BytesIO(b"different valid-looking payload"), metadata)
    r.store.import_stream = altered
    with pytest.raises(ValueError, match="decoded render evidence"):
        asyncio.run(r.service.run(owned))
    assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.FAILED
    assert not any(m.artifact_type == "video_render" for m in r.store.list_artifacts())


def test_selected_old_generated_image_ignores_new_candidate_head(render, setup):
    from app.application.image_generation import ImageGenerationService
    from app.application.visual_prompts import VisualPromptService
    from app.providers.mock_image import MockImageProvider
    from app.storage.image_generation import ImageResultIndex, ProjectImageGeneration

    r = render
    image_index = ImageResultIndex(setup[0].repository, setup[1])
    image_store = LocalArtifactStore(image_index.root, index=image_index)
    prompts = VisualPromptService(image_index.prompts)
    brief = prompts.pin_context("film_brief", "Synthetic scene")
    style = prompts.pin_context("visual_style", "Test pattern")
    media = r.timeline.clips[0].media
    prompt = prompts.create_manual(media.acceptance_id, media.scene_id, brief.id, style.id, "A red tree")
    prompts.select(prompt.id, expected_selection_id=None)
    images = ImageGenerationService(ResultPublicationService(image_index, image_store), r.coordinator,
                                   ProjectImageGeneration(image_index, image_store), MockImageProvider())
    generated = []
    for _ in range(2):
        images.enqueue(prompt.id, width=16, height=16, force=True)
        generated.append(images.run(r.coordinator.claim_next("image-fixture")))
    images.select(generated[0].artifact_id, expected_selection_id=media.image_selection_id)
    r.timeline = r.prepared[9].compile(r.timeline.project_id, r.prepared[10])
    assert r.timeline.clips[0].media.image.artifact_id == generated[0].artifact_id
    assert image_index.selected()[f"scene:{media.scene_id}:image_candidate"] == generated[1].artifact_id
    assert asyncio.run(r.service.run(claim(r))).selected_at_publication


@pytest.mark.parametrize("when", ["transaction", "cleanup"])
def test_publication_failure_reports_actual_commit_and_retains_history(render, when):
    r = render
    owned = claim(r)
    if when == "transaction":
        def fail(*args):
            raise RuntimeError("fixture index failure")
        r.index._select = fail
        with pytest.raises(RuntimeError, match="index failure"):
            asyncio.run(r.service.run(owned))
        assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.FAILED
        assert not any(m.artifact_type == "video_render" for m in r.store.list_artifacts())
    else:
        original = r.store.import_stream
        def fail_after(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("fixture cleanup failure")
        r.store.import_stream = fail_after
        result = asyncio.run(r.service.run(owned))
        assert result.selected_at_publication
        assert r.coordinator.repository.get_attempt(owned.id).status == AttemptStatus.COMPLETED


def test_image_change_at_final_commit_is_retained_as_obsolete(render):
    from app.domain.base import utc_now
    r = render
    owned = claim(r)
    def clock():
        r.index.clock = utc_now
        r.prepared[6].import_and_select(r.prepared[4].id, r.timeline.clips[0].media.scene_id, r.prepared[7],
                                       expected_selection_id=r.prepared[8][0][1].id)
        return utc_now()
    r.index.clock = clock
    result = asyncio.run(r.service.run(owned))
    assert not result.selected_at_publication and result.reason == "obsolete_inputs"


@pytest.mark.parametrize("policy", ["fit", "fill"])
def test_real_ffmpeg_mp4_probe_full_decode_and_immutable_publication(render, setup, policy):
    r = render
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "D019 real smoke requires installed FFmpeg and ffprobe; do not skip."
    r.service.renderer = FFmpegRenderer(ffmpeg, ffprobe)
    r.timeline = replace(r.timeline, fit_policy=policy)
    before = snapshot(r.store)
    result = asyncio.run(r.service.run(claim(r)))
    manifest = next(m for m in r.store.list_artifacts() if m.artifact_id == result.artifact_id)
    measured = manifest.metadata["render"]
    assert measured["frame_count"] == r.timeline.total_frames
    assert Fraction(*measured["video_duration"]) == r.timeline.video_duration
    assert abs(Fraction(*measured["audio_duration"]) - r.timeline.duration) < Fraction(1, 25)
    assert measured["width"] == 1280 and measured["height"] == 720
    assert measured["fully_decoded"] is True and measured["size_bytes"] > 1000
    assert {key: snapshot(r.store)[key] for key in before} == before
    assert result.selected_at_publication
    from app.application.projects import ProjectSession
    from app.jobs.repository import JobRepository
    from app.storage.project_repository import ProjectRepository
    workspace = setup[0].repository.workspace
    payload = r.store.read_artifact(manifest.storage_key)
    setup[0].close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        index = RenderResultIndex(reopened.repository, JobRepository(reopened.repository))
        store = LocalArtifactStore(index.root, index=index)
        assert index.selected()["project:video_render"] == manifest.artifact_id
        assert store.read_artifact(manifest.storage_key) == payload


def test_redirected_work_directory_cannot_receive_staged_inputs(render, tmp_path):
    import os
    r = render
    outside = tmp_path / "outside-render"
    outside.mkdir()
    work = r.index.repository.workspace / "work"
    work.mkdir(exist_ok=True)
    redirected = work / "render"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(redirected))
    else:
        redirected.symlink_to(outside, target_is_directory=True)
    try:
        owned = claim(r)
        with pytest.raises(ValueError, match="link|reparse"):
            asyncio.run(r.service.run(owned))
        assert not list(outside.iterdir()) and not r.process.calls
    finally:
        if os.name == "nt":
            redirected.rmdir()
        else:
            redirected.unlink()
