"""D032 immutable exports and mandatory synthetic libass/FFmpeg smoke."""

import asyncio
from array import array
from hashlib import sha256
import io
import math
import shutil
import subprocess
import wave

from PIL import Image

from app.application.speech_alignment import SpeechAlignmentService
from app.domain.caption_track import (
    PublishedCaptionTrack,
    build_synchronized_caption_track,
    serialize_ass,
    serialize_srt,
)
from app.domain.dependencies import canonical_json
from app.domain.section_audio import SectionAudio
from app.domain.speech_alignment import source_words
from app.providers.ffmpeg_render import FFmpegRenderer
from app.providers.whisperx_alignment import WhisperXAlignmentAdapter
from app.storage.captions import ProjectCaptionTracks
from app.storage.local_store import LocalArtifactStore
from app.storage.speech_alignment import ProjectSpeechAlignments
from app.storage.video_render import ProjectVideoRender, RenderResultIndex
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared  # noqa: F401
from tests.unit.test_timeline import compile_values, media


def complete_backend(audio, section):
    boundary = audio.speech_boundary_map

    def backend(_audio_bytes, text, language):
        assert text == section.text and language == "en"
        result = []
        ranges = source_words(text)
        for block in boundary.blocks:
            words = [span for span in ranges
                     if block.source_start <= span[0] < span[1] <= block.source_end]
            width = block.end_frame - block.start_frame
            for index, span in enumerate(words):
                start = block.start_frame + width * (4 * index + 1) // (4 * len(words))
                end = block.start_frame + width * (4 * index + 3) // (4 * len(words))
                result.append({"word": text[slice(*span)],
                               "start": start / audio.sample_rate,
                               "end": end / audio.sample_rate, "score": .99})
        return {"segments": [{"words": result}]}

    return backend


def test_project_exports_verified_srt_ass_and_json_from_selected_alignment(setup, prepared):
    section, manifest, _, _, _, _, _, _, _, compiler, inputs = prepared
    timeline = compiler.compile(section.project_id, inputs)
    audio = SectionAudio.from_manifest(manifest)
    alignment_store = ProjectSpeechAlignments(setup[0].repository, setup[3])
    alignment = SpeechAlignmentService(
        alignment_store,
        WhisperXAlignmentAdapter(
            complete_backend(audio, section), model_name="fixture-aligner",
            runtime_version="fixture-1"), confidence_threshold=.6,
    ).align(section, audio, language="en")
    captions = ProjectCaptionTracks(setup[0].repository, setup[3])
    published = captions.create(timeline, [alignment.id], language="en")
    restored = captions.published(published.track.id)
    assert restored == published
    assert [value.text for value in restored.track.segments] == [
        "First two three four five six.", "Second sentence!"]
    manifests = {item.artifact_type: item for item in setup[3].list_artifacts()
                 if item.metadata.get("value_id") == restored.track.id}
    assert {captions.track_type, captions.srt_type, captions.ass_type} <= set(manifests)
    assert setup[3].read_artifact(manifests[captions.srt_type].storage_key).endswith(b"\r\n")
    assert b"Dialogue: 0," in setup[3].read_artifact(
        manifests[captions.ass_type].storage_key)
    assert captions.history(timeline.id) == (published,)
    render_index = RenderResultIndex(setup[0].repository, setup[1])
    render_store = LocalArtifactStore(render_index.root, index=render_index)
    staged = ProjectVideoRender(render_index, render_store).stage(
        timeline, captions=published)
    assert (staged / "captions.ass").read_bytes() == serialize_ass(
        published.track.segments).encode("utf-8")


def _synthetic_track(timeline):
    text = "Measured captions"
    span = timeline.clips[0].media.audio
    from app.domain.speech_alignment import AlignedWord, SpeechAlignment
    ranges = source_words(text)
    words = (AlignedWord(0, *ranges[0], "sentence", "aligned", 4_000, 7_000, .99),
             AlignedWord(1, *ranges[1], "sentence", "aligned", 7_200, 12_000, .99))
    alignment = SpeechAlignment(
        "alignment-real", timeline.project_id, "section-a", "revision-a",
        sha256(text.encode()).hexdigest(), len(text), span.artifact_id, span.checksum,
        span.sample_rate, span.frame_count, "fixture", "fixture", "en", .6, words)
    track = build_synchronized_caption_track(
        timeline, {"section-a": text}, {"section-a": alignment})
    ass, srt = serialize_ass(track.segments).encode(), serialize_srt(track.segments).encode()
    return track, ass, srt


def test_real_ffmpeg_burns_measured_ass_and_keeps_playable_profile(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Mandatory D032 synthetic smoke requires ffmpeg and ffprobe."
    timeline = compile_values(media(rate=8_000, end=16_000, total=16_000))
    Image.new("RGB", (64, 64), "black").save(tmp_path / "image-0.png")
    samples = array("h", [int(2_000 * math.sin(2 * math.pi * 220 * n / 8_000))
                          for n in range(16_000)])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setparams((1, 2, 8_000, 0, "NONE", "not compressed"))
        target.writeframes(samples.tobytes())
    (tmp_path / "audio-0.wav").write_bytes(buffer.getvalue())
    track, ass, srt = _synthetic_track(timeline)
    (tmp_path / "captions.ass").write_bytes(ass)
    published = PublishedCaptionTrack(
        track, "track-real", sha256(canonical_json(track.to_payload()).encode()).hexdigest(),
        "srt-real", sha256(srt).hexdigest(), "ass-real", sha256(ass).hexdigest())
    result = asyncio.run(FFmpegRenderer(ffmpeg, ffprobe).render(
        timeline, tmp_path, captions=published, canceled=lambda: False,
        progress=lambda *_: None))
    assert result.timeline_id == timeline.id and result.frame_count == 50

    def luminance(at):
        return subprocess.run(
            [ffmpeg, "-v", "error", "-ss", at, "-i", str(tmp_path / "render.mp4"),
             "-frames:v", "1", "-vf", "crop=640:240:320:440,format=gray",
             "-f", "rawvideo", "-"], capture_output=True, check=True,
            timeout=20).stdout

    before, during = luminance("0.20"), luminance("1.00")
    assert max(before) < 20
    assert max(during) > 200 and sum(during) > sum(before) + 20_000
