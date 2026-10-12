"""Temporary real project/caption storage; no LLM, uploader, video or voice network calls."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace as NS
import zipfile

from PIL import Image
import pytest

from app.application.publishing_package import PublishingPackageService
from app.application.projects import ProjectSession
from app.application.speech_alignment import SpeechAlignmentService
from app.domain.section_audio import SectionAudio
from app.providers.local_thumbnail import LocalThumbnailBuilder
from app.providers.whisperx_alignment import WhisperXAlignmentAdapter
from app.runtime.media_process import RenderCanceled
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.speech_alignment import ProjectSpeechAlignments
from tests.integration.test_section_audio import setup
from tests.integration.test_timeline import prepared
from tests.integration.test_synchronized_captions import complete_backend
from tests.unit.test_publishing_package import metadata


@pytest.fixture
def publishing(setup, prepared, tmp_path):
    session, jobs, index, store, *_ = setup
    section, manifest, _, _, _, _, _, _, _, compiler, inputs = prepared
    timeline = compiler.compile(session.project.id, inputs)
    original = b"verified immutable MP4 fixture"
    evidence = {"checksum": sha256(original).hexdigest(), "size_bytes": len(original), "fully_decoded": True,
        "timeline_id": timeline.id, "width": 640, "height": 360, "fps": 25, "frame_count": timeline.total_frames,
        "sample_rate": 8000, "channels": 1, "video_duration": [timeline.total_frames, 25],
        "audio_duration": [timeline.duration.numerator, timeline.duration.denominator]}
    source = store.save_artifact("final.mp4", original, {"artifact_type": "video_render", "project_id": session.project.id,
        "render": evidence, "desktop_dependencies": {"request": {"settings": {"timeline": timeline.to_payload()}}}})
    selected = [source]; current = [NS(timeline=timeline)]
    def resolve(*_, **__):
        if sha256(store.read_artifact(selected[0].storage_key)).hexdigest() != selected[0].checksum: raise ValueError("checksum mismatch")
        return selected[0]
    renders = NS(selected=resolve, motion_current=lambda *_: True, current=lambda t: None)
    service = PublishingPackageService(session, store, renders, NS(current=lambda: current[0]), NS(selected=lambda: None),
        NS(active_binding=lambda: None), LocalThumbnailBuilder())
    image = tmp_path / "image.png"; Image.new("RGB", (640, 400), "navy").save(image)
    yield NS(service=service, session=session, source=source, selected=selected, current=current, timeline=timeline,
        store=store, original=original, image=image, section=section, audio=SectionAudio.from_manifest(manifest))


def approved(f, *, subtitles=False):
    s = f.service
    candidate = asyncio.run(s.generate_metadata("original"))
    value = s.starting_draft("original")
    value.update(metadata=s.artifacts.read(candidate)["metadata"], candidate_id=candidate.artifact_id, approved=True, chapters_enabled=False)
    image = s.import_image(f.image)
    thumbnails = s.thumbnail_candidates(image.artifact_id, {"source_kind": "original", "headline": "Known project facts"})
    value["thumbnail_id"] = thumbnails[0].artifact_id
    if subtitles: value["subtitle_id"] = s.subtitles("original").artifact_id
    latest = s.artifacts.latest()[0]
    return s.save_draft(value, expected_revision_id=latest.artifact_id if latest else None)


def add_alignment(f):
    backend = complete_backend(f.audio, f.section)
    provider = WhisperXAlignmentAdapter(lambda raw, text, lang: backend(raw, text, "en"), model_name="fixture", runtime_version="fixture")
    aligner = SpeechAlignmentService(f.service.captions.alignments, provider)
    f.service.alignment = aligner
    return aligner.align(f.section, f.audio, language=f.session.project.language)


def test_no_llm_is_labelled_deterministic_and_calls_only_on_explicit_generation(publishing):
    f = publishing; s = f.service
    assert s.artifacts.history("candidates") == []
    assert s.sources()[0][0] == "original" and s.artifacts.history("candidates") == []
    m = asyncio.run(s.generate_metadata("original")); value = s.artifacts.read(m)
    assert "not AI" in value["origin"] and value["provider"] is None
    assert len(value["metadata"]["titles"]) == 3


def test_long_project_topic_and_film_brief_produce_a_bounded_honest_starting_draft(publishing, monkeypatch):
    s = publishing.service; context, _ = s.context("original")
    context.update(topic="Known topic. " * 2000, film_brief="Known source facts. " * 2000)
    from app.domain.dependencies import content_fingerprint
    monkeypatch.setattr(s, "context", lambda *_: (context, content_fingerprint(context)))
    result = s.artifacts.read(asyncio.run(s.generate_metadata("original")))
    assert len(result["metadata"]["description"]) <= 4102 and "not AI" in result["origin"]
    assert result["context"]["topic"] == context["topic"]
    assert "Known source facts" not in result["metadata"]["description"]
    assert publishing.section.title in result["metadata"]["description"]


def test_strict_llm_schema_context_and_successful_candidates_retained_without_edit_overwrite(publishing):
    f = publishing; s = f.service
    first = approved(f); saved = s.artifacts.read(first); saved["metadata"]["description"] = "Manual approved copy"
    second = s.save_draft(saved, expected_revision_id=first.artifact_id)
    class LLM:
        provider_name = "fixture"
        def generate_structured(self, prompt, schema):
            assert schema["additionalProperties"] is False
            assert all(k in prompt for k in ("saved_script", "film_brief", "format", "language", "duration_seconds", "video_plan"))
            return metadata()
    s.llm = LLM()
    asyncio.run(s.generate_metadata("original")); asyncio.run(s.generate_metadata("original"))
    assert len(s.artifacts.history("candidates")) == 3
    assert s.artifacts.latest()[0] == second and s.artifacts.latest()[1]["metadata"]["description"] == "Manual approved copy"


@pytest.mark.parametrize("result", [{"titles": []}, {**metadata(), "description": "https://hallucinated.example"}, {**metadata(), "description": "99% savings"}])
def test_bad_llm_payload_rejected_without_persistence(publishing, result):
    s = publishing.service
    s.llm = NS(provider_name="fake", generate_structured=lambda *_: result)
    with pytest.raises(ValueError): asyncio.run(s.generate_metadata("original"))
    assert not s.artifacts.history("candidates")


def test_provider_error_retains_manual_draft(publishing):
    s = publishing.service; before = approved(publishing)
    def error(*_): raise RuntimeError("provider unavailable")
    s.llm = NS(generate_structured=error)
    with pytest.raises(RuntimeError, match="unavailable"): asyncio.run(s.generate_metadata("original"))
    assert s.artifacts.latest()[0] == before


def test_valid_zip_files_identities_checksums_optional_omissions_and_cache(publishing):
    f = publishing; s = f.service; revision = approved(f)
    before = {m.artifact_id: m.checksum for m in f.store.list_artifacts()}
    output = s.build_package(revision); assert s.artifacts.verify_package(output) == output
    assert s.build_package(revision) == output and len(s.artifacts.history("package")) == 1
    with zipfile.ZipFile(BytesIO(s.artifacts.bytes(output))) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"publishing-package/" + n for n in ("video.mp4", "thumbnail.jpg", "description.txt", "tags.txt", "metadata.json", "README.txt")}
        assert archive.read("publishing-package/video.mp4") == f.original
        meta = json.loads(archive.read("publishing-package/metadata.json"))
        assert meta["selected_video"] == {"artifact_id": f.source.artifact_id, "checksum": f.source.checksum}
        assert meta["thumbnail_checksum"] == sha256(archive.read("publishing-package/thumbnail.jpg")).hexdigest()
        assert meta["subtitle_checksum"] is None and len(meta["omitted_components"]) == 3
        assert "credentials" not in archive.read("publishing-package/metadata.json").decode()
    assert all(next(m for m in f.store.list_artifacts() if m.artifact_id == k).checksum == v for k, v in before.items())


def test_synchronized_caption_artifacts_reused_utf8_and_no_original_caption_repair(publishing):
    f = publishing; s = f.service; add_alignment(f)
    first = s.subtitles("original"); before = len(f.store.list_artifacts())
    assert s.subtitles("original") == first and len(f.store.list_artifacts()) == before
    assert "First two three" in s.artifacts.bytes(first).decode("utf-8")
    revision = approved(f, subtitles=True); output = s.build_package(revision)
    with zipfile.ZipFile(BytesIO(s.artifacts.bytes(output))) as archive:
        raw = archive.read("publishing-package/subtitles.pl.srt")
        assert raw == s.artifacts.bytes(first)
        assert json.loads(archive.read("publishing-package/metadata.json"))["subtitle_checksum"] == first.checksum


def test_missing_alignment_blocks_and_explicit_configured_action_uses_existing_alignment_service(publishing):
    f = publishing; s = f.service
    with pytest.raises(ValueError, match="MISSING"): s.subtitles("original")
    with pytest.raises(ValueError, match="configure"): asyncio.run(s.generate_missing_alignment("original"))
    backend = complete_backend(f.audio, f.section)
    provider = WhisperXAlignmentAdapter(lambda raw, text, lang: backend(raw, text, "en"), model_name="fixture", runtime_version="fixture")
    s.alignment = SpeechAlignmentService(s.captions.alignments, provider)
    assert asyncio.run(s.generate_missing_alignment("original")) == 1
    assert asyncio.run(s.generate_missing_alignment("original")) == 0
    assert s.subtitles("original").size_bytes > 0


def test_stale_alignment_is_not_accepted(publishing):
    f = publishing; s = f.service; alignment = add_alignment(f)
    manifest = next(m for m in f.store.list_artifacts() if m.metadata.get("value_id") == alignment.id)
    f.store._artifact_path(manifest.storage_key).write_bytes(b"corrupt fixture alignment")
    with pytest.raises(ValueError, match="checksum"): s.subtitles("original")
    assert not s.artifacts.history("subtitles")


def test_thumbnail_aspect_format_candidates_cache_and_selection_retained(publishing):
    f = publishing; s = f.service; image = s.import_image(f.image)
    options = {"source_kind": "original", "headline": "Readable headline", "font_size": 100, "position": "top", "darkening": .5, "accent": "#FFCC44"}
    values = s.thumbnail_candidates(image.artifact_id, options)
    assert len(values) == 3 and s.thumbnail_candidates(image.artifact_id, options) == values
    for m in values:
        with Image.open(BytesIO(s.verify_thumbnail(m))) as decoded:
            assert decoded.mode == "RGB" and decoded.format == "JPEG" and decoded.size == (640, 360)
    options["headline"] = "Changed text"
    assert s.thumbnail_candidates(image.artifact_id, options)[0].artifact_id != values[0].artifact_id
    assert f.selected[0] == f.source


def test_changed_source_or_timeline_blocks_stale_packages_and_preserves_edits(publishing):
    f = publishing; s = f.service; revision = approved(f); output = s.build_package(revision)
    f.selected[0] = f.store.save_artifact("other.mp4", f.original, {**f.source.metadata, "artifact_type": "video_render"})
    with pytest.raises(ValueError, match="STALE"): s.build_package(revision)
    assert s.artifacts.latest()[0] == revision and s.artifacts.verify_package(output) == output
    f.selected[0] = f.source; f.current[0] = NS(timeline=replace(f.timeline, fit_policy="fill"))
    with pytest.raises(ValueError, match="STALE"): s.source("original")


def test_unapproved_missing_thumbnail_and_cancellation_publish_nothing(publishing):
    f = publishing; s = f.service; revision = approved(f)
    value = s.artifacts.read(revision); value["approved"] = False
    unapproved = s.save_draft(value, expected_revision_id=revision.artifact_id)
    with pytest.raises(ValueError, match="REVIEW"): s.build_package(unapproved)
    value["approved"] = True; value["thumbnail_id"] = None
    no_thumb = s.save_draft(value, expected_revision_id=unapproved.artifact_id)
    with pytest.raises(ValueError, match="thumbnail"): s.build_package(no_thumb)
    revision = approved(f)
    with pytest.raises(RenderCanceled): s.build_package(revision, canceled=lambda: True)
    assert not s.artifacts.history("package")


def test_project_reopen_recovers_approved_metadata_selection_and_package_history(publishing):
    f = publishing; s = f.service; revision = approved(f); output = s.build_package(revision)
    workspace = f.session.repository.workspace; f.session.close()
    from app.storage.publishing_package import ProjectPublishing
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        restored = ProjectPublishing(LocalArtifactStore.for_project(reopened.repository), reopened.project.id)
        assert restored.latest()[0] == revision and restored.latest()[1]["approved"]
        assert restored.latest()[1]["thumbnail_id"]
        assert restored.verify_package(output) == output


def test_corrupt_zip_not_reused_history_retained_and_rebuild_does_not_regenerate_media(publishing):
    f = publishing; s = f.service; revision = approved(f); old = s.build_package(revision)
    f.store._artifact_path(old.storage_key).write_bytes(b"corrupt")
    rebuilt = s.build_package(revision)
    assert rebuilt != old and len(s.artifacts.history("package")) == 2
    assert f.store.read_artifact(f.source.storage_key) == f.original


def test_no_youtube_publishing_provider_constructed_or_invoked(publishing, monkeypatch):
    import app.providers.publishing_factory as factory
    def forbidden(*_, **__): raise AssertionError("No upload provider may be constructed")
    monkeypatch.setattr(factory, "build_publishing_provider", forbidden)
    publishing.service.build_package(approved(publishing))


def branded_source(f, *, after=True):
    from app.domain.branding import BrandingConfig
    from app.domain.delivery import DeliveryConfig
    config = BrandingConfig(intro=True, placement="after_cold_open" if after else "before", outro=True, outro_seconds=10)
    clip = f.timeline.clips[0]
    boundary = {"timeline_id": f.timeline.id, "video_frame": clip.end_frame,
                "audio_sample": int((clip.audio_offset + clip.duration) * f.source.metadata["render"]["sample_rate"])} if after else None
    original = f.source.metadata["render"]
    duration = Fraction(*original["video_duration"]) + 13
    raw = b"verified branded fixture"
    evidence = {**original, "checksum": sha256(raw).hexdigest(), "size_bytes": len(raw),
                "frame_count": original["frame_count"] + 325,
                "video_duration": [duration.numerator, duration.denominator]}
    branded = f.store.save_artifact("brand.mp4", raw, {"artifact_type": "branded_video", "project_id": f.session.project.id,
        "finishing": evidence, "identity": {"source_id": f.source.artifact_id, "source_checksum": f.source.checksum,
            "settings": config.to_payload(), "boundary": boundary}})
    delivery_raw = b"verified delivery fixture"
    delivery_evidence = {**evidence, "status": "PASS", "checksum": sha256(delivery_raw).hexdigest(), "size_bytes": len(delivery_raw),
                        "source_artifact_id": branded.artifact_id, "source_checksum": branded.checksum}
    delivered = f.store.save_artifact("delivery.mp4", delivery_raw, {"artifact_type": "delivery_video", "project_id": f.session.project.id,
                                                                    "delivery": delivery_evidence})
    f.service.delivery = NS(source=lambda kind: branded, prepare=lambda _: (branded, {}, "fingerprint"),
                           artifacts=NS(current=lambda: DeliveryConfig(source="branded"), cached=lambda _: delivered))
    return branded, delivered


@pytest.mark.parametrize("after", [False, True])
def test_verified_branding_and_delivery_subtitle_offsets_preserve_base_tracks(publishing, after):
    f = publishing; s = f.service; add_alignment(f)
    original = s.subtitles("original"); tracks = s.captions.history(f.timeline.id)
    branded, delivered = branded_source(f, after=after)
    assert {kind for kind, *_ in s.sources()} == {"original", "branded", "delivery"}
    for kind in ("branded", "delivery"):
        adapted = s.subtitles(kind)
        expected = s.timing(kind).captions(tracks[0].track.segments)
        from app.domain.caption_track import serialize_srt
        assert s.artifacts.bytes(adapted) == serialize_srt(expected).encode("utf-8")
        if after:
            assert expected[0].start_ms == tracks[0].track.segments[0].start_ms
            assert expected[-1].start_ms == tracks[0].track.segments[-1].start_ms + 3000
        else: assert expected[0].start_ms == tracks[0].track.segments[0].start_ms + 3000
    assert s.captions.history(f.timeline.id) == tracks
    assert s.artifacts.bytes(original) == s.artifacts.bytes(s.subtitles("original"))


def test_missing_or_inconsistent_composition_mapping_blocks_timed_export(publishing):
    f = publishing; s = f.service; add_alignment(f)
    branded, _ = branded_source(f)
    branded.metadata["identity"]["boundary"] = None
    with pytest.raises(ValueError, match="boundary"): s.subtitles("branded")
    assert not s.artifacts.history("subtitles")


def test_manual_title_change_rebuilds_package_without_rebuilding_thumbnail_or_video(publishing):
    f = publishing; s = f.service; first = approved(f); old = s.build_package(first)
    before = [m.artifact_id for m in s.artifacts.history("thumbnail")]
    value = s.artifacts.read(first); value["metadata"]["titles"]["search"] = "Manually chosen title"
    second = s.save_draft(value, expected_revision_id=first.artifact_id)
    assert s.build_package(second) != old
    assert [m.artifact_id for m in s.artifacts.history("thumbnail")] == before and f.selected[0] == f.source


def test_explicit_frame_suggestions_are_three_distinct_verified_source_positions(publishing):
    f = publishing; s = f.service; calls = []
    async def frames(root, indices, **_):
        calls.append(indices)
        assert (root / "source.mp4").read_bytes() == f.original
        stream = BytesIO(); Image.new("RGB", (640, 360), "navy").save(stream, format="PNG")
        return [stream.getvalue()] * 3
    s.thumbnails.frames = frames
    result = asyncio.run(s.frame_suggestions("original"))
    assert len(result) == 3 and len(set(calls[0])) == 3
    assert all(m.metadata["source_id"] == f.source.artifact_id for m in result)
    assert all(m.metadata["source_checksum"] == f.source.checksum for m in result)


def test_optional_empty_tags_are_omitted_and_final_description_length_checked(publishing):
    f = publishing; s = f.service; revision = approved(f)
    value = s.artifacts.read(revision); value["metadata"]["keywords"] = []
    changed = s.save_draft(value, expected_revision_id=revision.artifact_id)
    output = s.build_package(changed)
    with zipfile.ZipFile(BytesIO(s.artifacts.bytes(output))) as archive:
        assert "publishing-package/tags.txt" not in archive.namelist()
        assert "tags omitted" in archive.read("publishing-package/README.txt").decode()
    value["metadata"]["description"] = "x" * 4998; value["metadata"]["hashtags"] = ["#Extra"]
    long = s.save_draft(value, expected_revision_id=changed.artifact_id)
    with pytest.raises(ValueError, match="description"): s.build_package(long)
    assert len(s.artifacts.history("package")) == 1


@pytest.mark.parametrize("missing", ["thumbnail", "source"])
def test_missing_files_never_publish_a_package(publishing, missing):
    f = publishing; s = f.service; revision = approved(f)
    manifest = f.source if missing == "source" else s.artifacts.one(s.artifacts.read(revision)["thumbnail_id"], "thumbnail")
    f.store._artifact_path(manifest.storage_key).unlink()
    with pytest.raises((ValueError, OSError)): s.build_package(revision)
    assert not s.artifacts.history("package")


def test_damaged_thumbnail_candidate_rebuilds_without_losing_history(publishing):
    f = publishing; s = f.service; image = s.import_image(f.image)
    options = {"source_kind": "original", "headline": "Known facts"}
    first = s.thumbnail_candidates(image.artifact_id, options)
    f.store._artifact_path(first[0].storage_key).write_bytes(b"damaged JPEG")
    second = s.thumbnail_candidates(image.artifact_id, options)
    assert second != first and len(s.artifacts.history("thumbnail")) == 6
    assert s.thumbnail_candidates(image.artifact_id, options) == second


def test_binding_verification_reused_only_with_unchanged_upstream_selection_catalog(publishing):
    f = publishing; s = f.service; calls = []
    s.renders.current = lambda t: calls.append(t.id)
    s.context("original"); s.context("original")
    assert calls == [f.timeline.id]
    asyncio.run(s.generate_metadata("original")); s.context("original")
    assert len(calls) == 1
    f.store.save_artifact("upstream-choice.json", "{}", {"artifact_type": "scene_image_selection", "project_id": f.session.project.id})
    s.context("original")
    assert len(calls) == 2


def test_social_chapters_optional_and_standard_chapters_cannot_be_silently_omitted(publishing):
    f = publishing; s = f.service
    from app.application.video_planning import VideoPlanningService
    from app.storage.video_plans import ProjectVideoPlans
    from tests.unit.test_video_planning import FakeProvider
    s.plans = ProjectVideoPlans(f.session.repository, f.store)
    VideoPlanningService(s.plans, FakeProvider("social")).generate(project_id=f.session.project.id, language="pl", video_format="social", target_duration_seconds=45, topic="Known facts")
    assert not s.starting_draft("original")["chapters_enabled"] and s.chapters("original") == []
    revision = approved(f)
    assert s.build_package(revision)
    VideoPlanningService(s.plans, FakeProvider("standard")).generate(project_id=f.session.project.id, language="pl", video_format="standard", target_duration_seconds=600, topic="Known facts")
    revision = approved(f)
    with pytest.raises(ValueError, match="Standard packages require"): s.build_package(revision)


def test_altered_zip_stream_fails_before_atomic_registration(publishing, monkeypatch):
    f = publishing; s = f.service; revision = approved(f)
    real = s.store.import_stream
    def altered(name, stream, metadata):
        class Altered:
            def read(self, count):
                block = stream.read(count)
                if block:
                    stream.digest = sha256(b"altered during publication")
                return block
        return real(name, Altered(), metadata)
    monkeypatch.setattr(s.store, "import_stream", altered)
    with pytest.raises(ValueError, match="atomic publication"): s.build_package(revision)
    assert not s.artifacts.history("package")


def test_partial_frame_set_never_becomes_thumbnail_suggestions(publishing):
    f = publishing; s = f.service
    async def frames(*_, **__): return []
    s.thumbnails.frames = frames
    with pytest.raises(ValueError, match="all three"): asyncio.run(s.frame_suggestions("original"))
    assert not s.artifacts.history("image")


def test_alignment_cancel_waits_for_provider_but_does_not_publish_its_result(publishing):
    f = publishing; s = f.service; canceled = [False]
    backend = complete_backend(f.audio, f.section)
    def canceling_backend(raw, text, language):
        result = backend(raw, text, "en"); canceled[0] = True; return result
    provider = WhisperXAlignmentAdapter(canceling_backend, model_name="fixture", runtime_version="fixture")
    s.alignment = SpeechAlignmentService(s.captions.alignments, provider)
    with pytest.raises(RenderCanceled): asyncio.run(s.generate_missing_alignment("original", canceled=lambda: canceled[0]))
    assert s.captions.alignments.history(f.section.section_id) == ()


def test_rebased_draft_cannot_package_a_frame_thumbnail_from_the_previous_video(publishing):
    f = publishing; s = f.service; revision = approved(f)
    stream = BytesIO(); Image.new("RGB", (640, 360), "navy").save(stream, format="PNG")
    frame = f.store.save_artifact("frame.png", stream.getvalue(), {"artifact_type": "publishing_image", "project_id": f.session.project.id,
        "source_id": f.source.artifact_id, "source_checksum": f.source.checksum})
    choice = s.thumbnail_candidates(frame.artifact_id, {"source_kind": "original", "headline": "Known source frame"})[0]
    f.selected[0] = f.store.save_artifact("other.mp4", f.original, {**f.source.metadata, "artifact_type": "video_render"})
    value = s.artifacts.read(revision); value.update(thumbnail_id=choice.artifact_id, context_fingerprint=s.context("original")[1])
    rebased = s.save_draft(value, expected_revision_id=revision.artifact_id)
    with pytest.raises(ValueError, match="thumbnail frame belongs"): s.build_package(rebased)
    assert not s.artifacts.history("package")
