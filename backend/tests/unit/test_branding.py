import asyncio
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.application.projects import ProjectSession
from app.application.video_finishing import VideoFinishingService, cold_open_boundary
from app.domain.branding import BrandingConfig
from app.domain.plan_script import planned_section_identity
from app.domain.video_plan import VideoFormat
from app.providers.ffmpeg_finishing import FFmpegFinisher
from app.runtime.media_process import RenderCanceled
from app.storage.branding import ProjectBranding
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from tests.unit.test_timeline import compile_values, media


def probe_data(seconds, frames, width=640, height=360):
    return {"format": {"format_name": "mov,mp4"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": width, "height": height, "pix_fmt": "yuv420p",
         "avg_frame_rate": "25/1", "nb_read_frames": str(frames), "duration": str(seconds)},
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1,
         "nb_read_frames": "200", "duration": str(seconds)}]}


class Process:
    def __init__(self): self.calls, self.extra, self.fail, self.during = [], 18, None, lambda: None
    async def run(self, command, *, cwd, canceled=lambda: False, **_):
        self.calls.append(command)
        phase = "probe" if "-show_streams" in command else "decode" if "null" in command else "encode"
        if phase == "encode":
            (cwd / "branded.mp4").write_bytes(b"verified fixture branded bytes")
            self.during()
        if canceled(): raise RenderCanceled("fixture canceled")
        if self.fail == phase: raise RuntimeError(f"{phase} failed")
        if phase != "probe": return b""
        extra = self.extra if command[-1] == "branded.mp4" else 0
        data = probe_data(4 + extra, 100 + extra * 25)
        if self.fail == "frames" and extra: data["streams"][0]["nb_read_frames"] = "1"
        return json.dumps(data).encode()


@pytest.fixture
def finishing(tmp_path):
    session = ProjectSession.create(tmp_path / "project", repository_factory=ProjectRepository, name="Finishing")
    store = LocalArtifactStore.for_project(session.repository)
    raw = b"immutable original video fixture"
    source = store.save_artifact("original.mp4", raw, {"artifact_type": "video_render", "project_id": session.project.id,
        "render": {"fully_decoded": True, "checksum": sha256(raw).hexdigest(), "size_bytes": len(raw),
                   "frame_count": 100, "width": 640, "height": 360, "fps": 25, "sample_rate": 48000,
                   "video_duration": [4, 1], "audio_duration": [4, 1]}})
    selected = [source]
    def resolve(*args, verify_bytes=False):
        current = selected[0]
        if verify_bytes:
            assert sha256(store.read_artifact(current.storage_key)).hexdigest() == current.checksum
        return current
    renders = SimpleNamespace(selected=resolve)
    branding = ProjectBranding(store, session.project.id)
    logo_path = tmp_path / "logo.png"
    Image.new("RGBA", (80, 50), (255, 255, 255, 100)).save(logo_path)
    logo = branding.import_logo(logo_path)
    executable = tmp_path / "fake.exe"; executable.write_bytes(b"fake executable")
    process = Process()
    renderer = FFmpegFinisher(executable, executable, process=process)
    service = VideoFinishingService(session, branding, renders, renderer)
    config = BrandingConfig(channel_name="Atlas", logo_id=logo.artifact_id, intro=True, outro=True)
    yield SimpleNamespace(session=session, store=store, source=source, selected=selected, branding=branding,
                          renderer=renderer, process=process, service=service, config=config, logo_path=logo_path)
    session.close()


@pytest.mark.parametrize("intro,outro", [(False, False), (True, False), (False, True), (True, True)])
def test_optional_segments_preserve_original_and_have_exact_duration(finishing, intro, outro):
    f = finishing
    config = replace(f.config, intro=intro, outro=outro)
    f.process.extra = config.extra_seconds
    before = f.store.read_artifact(f.source.storage_key)
    result = asyncio.run(f.service.build(config))
    assert f.store.read_artifact(f.source.storage_key) == before and f.selected[0] == f.source
    if not config.enabled:
        assert result == f.source and not f.process.calls and not f.branding.history("branded_video")
    else:
        evidence = result.metadata["finishing"]
        assert Fraction(*evidence["video_duration"]) == 4 + config.extra_seconds
        assert evidence["frame_count"] == 100 + config.extra_seconds * 25
        root = next((f.session.repository.workspace / "work/finishing").iterdir())
        filters = (root / "filters.txt").read_text()
        order = ("[vintro]" if intro else "") + "[vmain0]" + ("[voutro]" if outro else "")
        assert order + f"concat=n={1+int(intro)+int(outro)}:v=1:a=0" in filters


def test_same_inputs_reuse_and_text_or_source_changes_stale_only_branded_output(finishing):
    f = finishing
    first = asyncio.run(f.service.build(f.config)); count = len(f.process.calls)
    assert asyncio.run(f.service.build(f.config)) == first and len(f.process.calls) == count
    changed = replace(f.config, cta="Watch another story")
    assert f.branding.cached(f.service.prepare(changed)[2]) is None
    second = asyncio.run(f.service.build(changed))
    assert second.artifact_id != first.artifact_id
    assert f.selected[0] == f.source and len(f.branding.history("branded_video")) == 2
    raw = b"new source"
    other = f.store.save_artifact("new.mp4", raw, {"artifact_type": "video_render", "project_id": f.session.project.id,
                                                  "render": f.source.metadata["render"]})
    f.selected[0] = other
    assert f.branding.cached(f.service.prepare(changed)[2]) is None


@pytest.mark.parametrize("failure", ["encode", "probe", "decode", "frames", "cancel"])
def test_failure_or_cancellation_publishes_no_partial_and_keeps_original(finishing, failure):
    f = finishing; canceled = [False]
    if failure == "cancel": f.process.during = lambda: canceled.__setitem__(0, True)
    else: f.process.fail = failure
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(f.service.build(f.config, canceled=lambda: canceled[0]))
    assert not f.branding.history("branded_video")
    assert f.selected[0] == f.source
    assert f.store.read_artifact(f.source.storage_key) == b"immutable original video fixture"


def test_transparent_logo_composes_without_black_rectangle(finishing):
    f = finishing
    card = f.renderer.card(f.config, f.logo_path, 1920, 1080)
    assert card.size == (1920, 1080)
    # Transparent white logo blends into the dark background at its centered placement.
    pixel = card.getpixel((960, 270))
    assert 16 < pixel[0] < 255


def test_missing_or_invalid_logo_fails_closed(finishing, tmp_path):
    f = finishing
    with pytest.raises(ValueError, match="import a PNG logo"):
        asyncio.run(f.service.build(replace(f.config, logo_id=None)))
    with pytest.raises(ValueError, match="missing"):
        f.branding.logo("missing")
    path = tmp_path / "not-png.jpg"; Image.new("RGB", (10, 10)).save(path)
    with pytest.raises(ValueError, match="PNG"):
        f.branding.import_logo(path)


def mapping_fixture():
    planned = [SimpleNamespace(id=f"planned-{i}") for i in range(8)]
    plan = SimpleNamespace(id="plan", project_id="project", format=VideoFormat.STANDARD,
        groups=[SimpleNamespace(kind="cold_open" if i == 0 else "chapter", sections=(p,)) for i, p in enumerate(planned)])
    values, sections = [], []
    for i, p in enumerate(planned):
        value = media(str(i), rate=48000, end=48000)
        sid = planned_section_identity("project", p.id)
        values.append(replace(value, section_id=sid))
        sections.append(SimpleNamespace(section_id=sid, id=value.section_revision_id))
    timeline = compile_values(*values)
    binding = SimpleNamespace(id="binding", video_plan_revision_id="plan")
    return plan, binding, sections, timeline


def test_after_cold_open_uses_only_confirmed_group_boundary():
    plan, binding, sections, timeline = mapping_fixture()
    result = cold_open_boundary(plan, binding, sections, timeline, {"timeline": timeline.to_payload()}, 48000)
    assert result["video_frame"] == 25 and result["audio_sample"] == 48000


@pytest.mark.parametrize("issue", ["missing", "reordered", "stale", "timeline", "binding"])
def test_missing_or_ambiguous_cold_open_mapping_never_guesses(issue):
    plan, binding, sections, timeline = mapping_fixture()
    request = {"timeline": timeline.to_payload()}
    if issue == "missing": sections.pop()
    elif issue == "reordered": sections.reverse()
    elif issue == "stale": sections[0].id = "older"
    elif issue == "timeline": request = {}
    elif issue == "binding": binding.video_plan_revision_id = "other"
    with pytest.raises(ValueError): cold_open_boundary(plan, binding, sections, timeline, request, 48000)


def test_branding_cannot_hide_failed_standard_narration(finishing):
    f = finishing
    f.service.video_plans = SimpleNamespace(selected=lambda: SimpleNamespace(format=VideoFormat.STANDARD))
    with pytest.raises(ValueError, match="PLAN_DURATION BLOCKED"):
        asyncio.run(f.service.build(f.config))
    assert not f.process.calls and not f.branding.history("branded_video")


def test_logo_and_renderer_changes_only_invalidate_finishing(finishing):
    f = finishing
    first = asyncio.run(f.service.build(f.config))
    replacement = f.branding.import_logo(f.logo_path)
    changed = replace(f.config, logo_id=replacement.artifact_id)
    assert f.branding.cached(f.service.prepare(changed)[2]) is None
    original_identity = f.renderer.identity
    f.renderer.identity = lambda: {**original_identity(), "adapter": "next-finishing-version"}
    assert f.branding.cached(f.service.prepare(f.config)[2]) is None
    assert f.selected[0] == f.source and f.branding.verify_output(first) == first


def test_settings_and_verified_outputs_survive_reopen(finishing):
    f = finishing
    result = asyncio.run(f.service.build(f.config))
    root = f.session.repository.workspace
    f.session.close()
    reopened = ProjectSession.open(root, repository_factory=ProjectRepository)
    try:
        branding = ProjectBranding(LocalArtifactStore.for_project(reopened.repository), reopened.project.id)
        assert branding.current() == f.config
        assert branding.cached(result.metadata["fingerprint"]) == result
        assert branding.logo(f.config.logo_id)[0].artifact_id == f.config.logo_id
    finally:
        reopened.close()


def test_changed_preferences_during_encoding_never_publish_late_result(finishing):
    f = finishing
    f.process.during = lambda: f.branding.save(replace(f.config, cta="Changed while encoding"))
    with pytest.raises(ValueError, match="inputs changed"):
        asyncio.run(f.service.build(f.config))
    assert not f.branding.history("branded_video") and f.selected[0] == f.source


def test_damaged_cached_video_is_preserved_but_not_reused(finishing):
    f = finishing
    result = asyncio.run(f.service.build(f.config))
    f.store._artifact_path(result.storage_key).write_bytes(b"damaged fixture")
    second = asyncio.run(f.service.build(f.config))
    assert second.artifact_id != result.artifact_id
    assert len(f.branding.history("branded_video")) == 2 and f.selected[0] == f.source
