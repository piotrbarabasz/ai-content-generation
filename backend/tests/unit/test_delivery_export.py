import asyncio
from dataclasses import replace
from fractions import Fraction
from io import BytesIO
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from app.application.delivery_export import DeliveryExportService
from app.application.projects import ProjectSession
from app.domain.delivery import DeliveryConfig, delivery_status
from app.providers.ffmpeg_delivery import FFmpegDeliveryExporter, loudness_json
from app.runtime.media_process import RenderCanceled
from app.storage.delivery import ProjectDelivery
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from tests.unit.test_branding import finishing, probe_data


def loudness(integrated=-23, peak=-10, *, mode="linear"):
    return json.dumps({"input_i": str(integrated), "input_tp": str(peak), "input_lra": "4.0",
                      "input_thresh": "-33.0", "target_offset": "0.0", "normalization_type": mode}).encode()


class Process:
    def __init__(self):
        self.calls = []; self.channels = 1; self.failure = None
        self.before = loudness(); self.after = loudness(-16, -2.1)
        self.during = lambda: None

    async def run(self, command, *, cwd, canceled=lambda: False, on_line=lambda *_: None, **kwargs):
        self.calls.append((command, kwargs))
        if canceled(): raise RenderCanceled("fixture canceled")
        if "-show_streams" in command:
            data = probe_data(4, 100)
            data["streams"][0].update(profile="High", sample_aspect_ratio="1:1", bit_rate="12345", field_order="progressive")
            data["streams"][1].update(channels=self.channels, profile="LC", bit_rate="128000")
            if self.failure == "frames" and command[-1] == "delivery.mp4": data["streams"][0]["nb_read_frames"] = "99"
            if self.failure == "codec": data["streams"][0]["codec_name"] = "hevc"
            if self.failure == "hdr": data["streams"][0]["color_transfer"] = "smpte2084"
            return json.dumps(data).encode()
        if "-show_packets" in command:
            for i in reversed(range(100)): on_line(f"0,{i/25:.6f},0.040000")  # B frames reorder PTS.
            for i in range(188):
                pts = i * 1024 / 48000
                duration = min(1024 / 48000, 4 - pts)
                on_line(f"1,{pts:.6f},{duration:.6f}")
            if self.failure == "timestamps": on_line("0,999.000000,0.040000")
            return b""
        if command[-1] == "delivery.mp4":
            if self.failure == "encode": raise RuntimeError("fixture encoding failed")
            if self.failure != "missing": (cwd / "delivery.mp4").write_bytes(b"verified delivery fixture")
            self.during()
            if canceled(): raise RenderCanceled("fixture canceled")
            return self.before
        if "-af" in command:
            if self.failure == "analysis": return b"no measurements"
            return self.after if "delivery.mp4" in command else self.before
        if self.failure == "decode": raise RuntimeError("fixture decoding failed")
        return b""


@pytest.fixture
def delivery(finishing, tmp_path):
    f = finishing
    executable = tmp_path / "exporter.exe"; executable.write_bytes(b"fake exporter")
    process = Process()
    renderer = FFmpegDeliveryExporter(executable, executable, process=process)
    artifacts = ProjectDelivery(f.store, f.session.project.id)
    service = DeliveryExportService(f.session, artifacts, f.service.renders, renderer, finishing=f.service)
    return SimpleNamespace(f=f, process=process, renderer=renderer, artifacts=artifacts, service=service)


def test_source_preset_is_byte_identical_and_requires_no_encoder(delivery):
    d = delivery; d.service.renderer = None
    result = asyncio.run(d.service.build(DeliveryConfig(preset="source")))
    assert result.artifact_type == "delivery_video" and result.checksum == d.f.source.checksum
    assert d.f.store.read_artifact(result.storage_key) == d.f.store.read_artifact(d.f.source.storage_key)
    assert result.metadata["delivery"]["status"] == "SOURCE_COPY" and not d.process.calls
    assert d.artifacts.report(result)["output_artifact_id"] == result.artifact_id


@pytest.mark.parametrize("channels", [1, 2])
def test_hq_options_two_pass_and_verified_report(delivery, channels):
    d = delivery; d.process.channels = channels
    result = asyncio.run(d.service.build(DeliveryConfig()))
    command = next(c for c, _ in d.process.calls if c[-1] == "delivery.mp4" and "-c:v" in c)
    for flag, value in (("-preset", "medium"), ("-crf", "18"), ("-profile:v", "high"), ("-pix_fmt", "yuv420p"),
                        ("-c:v", "libx264"), ("-c:a", "aac"), ("-profile:a", "aac_low"), ("-ar", "48000"),
                        ("-ac", str(channels)), ("-b:a", "128000" if channels == 1 else "320000"), ("-movflags", "+faststart")):
        assert command[command.index(flag) + 1] == value
    assert "-b:v" not in command and "-minrate" not in command
    filters = command[command.index("-af") + 1]
    assert "measured_I=-23.0" in filters and "measured_TP=-10.0" in filters and "measured_LRA=4.0" in filters
    assert "measured_thresh=-33.0" in filters and "offset=0.0" in filters and "linear=true" in filters and "aresample=48000" in filters
    analyses = [c for c, _ in d.process.calls if "-af" in c and c[-1] == "-"]
    assert len(analyses) == 2 and "source.mp4" in analyses[0] and "delivery.mp4" in analyses[1]
    report = d.artifacts.report(result)
    assert report["status"] == "PASS" and report["before"]["integrated_lufs"] == -23 and report["after"]["integrated_lufs"] == -16
    assert report["channels"] == channels and report["fps"] == "25" and report["frame_count"] == 100
    assert report["source_artifact_id"] == d.f.source.artifact_id


@pytest.mark.parametrize("integrated,peak", [(-16, -.9), (-18.5, -2), ("-inf", "-inf")])
def test_post_aac_out_of_bounds_is_review_not_pass(delivery, integrated, peak):
    d = delivery; d.process.after = loudness(integrated, peak)
    result = asyncio.run(d.service.build(DeliveryConfig()))
    assert result.metadata["delivery"]["status"] == "REVIEW"
    assert result.metadata["delivery"]["reasons"]


@pytest.mark.parametrize("quiet", ["-inf", -55])
def test_silent_or_near_silent_source_is_not_amplified(delivery, quiet):
    d = delivery; d.process.before = loudness(quiet, "-inf" if quiet == "-inf" else -50)
    d.process.after = d.process.before
    result = asyncio.run(d.service.build(DeliveryConfig()))
    command = next(c for c, _ in d.process.calls if "-c:v" in c)
    assert "loudnorm=" not in command[command.index("-af") + 1]
    assert result.metadata["delivery"]["status"] == "REVIEW" and not result.metadata["delivery"]["normalization_applied"]


def test_normalization_can_be_disabled(delivery):
    d = delivery; d.process.after = loudness(-24, -4)
    result = asyncio.run(d.service.build(DeliveryConfig(normalize=False)))
    assert result.metadata["delivery"]["status"] == "PASS" and not result.metadata["delivery"]["normalization_applied"]
    command = next(c for c, _ in d.process.calls if "-c:v" in c)
    assert "loudnorm=" not in command[command.index("-af") + 1]


@pytest.mark.parametrize("failure", ["encode", "missing", "frames", "analysis", "decode", "timestamps", "codec", "hdr"])
def test_invalid_output_or_process_errors_never_publish(delivery, failure):
    d = delivery; d.process.failure = failure
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(d.service.build(DeliveryConfig()))
    assert not d.artifacts.records.history("delivery_video") and d.f.selected[0] == d.f.source


def test_cached_inputs_and_changed_crf_or_mastering_only_affect_delivery(delivery):
    d = delivery; config = DeliveryConfig()
    first = asyncio.run(d.service.build(config)); calls = len(d.process.calls)
    assert asyncio.run(d.service.build(config)) == first and len(d.process.calls) == calls
    for changed in (replace(config, crf=20), replace(config, target_lufs=-18), replace(config, normalize=False)):
        assert d.artifacts.cached(d.service.prepare(changed)[2]) is None
    assert d.f.selected[0] == d.f.source


def test_original_and_branded_sources_are_explicit_and_stale_outputs_rejected(delivery):
    d = delivery
    branded = asyncio.run(d.f.service.build(d.f.config))
    assert d.service.source("branded") == branded
    result = asyncio.run(d.service.build(DeliveryConfig(source="branded", preset="source")))
    assert result.checksum == branded.checksum
    d.f.branding.save(replace(d.f.config, cta="Changed CTA"))
    with pytest.raises(ValueError, match="current verified D068"):
        d.service.prepare(DeliveryConfig(source="branded"))
    with pytest.raises(ValueError, match="project-owned"):
        d.service.source("work/cache/arbitrary.mp4")
    assert d.f.selected[0] == d.f.source


def test_cancel_preserves_previously_selected_delivery_and_source(delivery):
    d = delivery; config = DeliveryConfig()
    retained = asyncio.run(d.service.build(config)); canceled = [False]
    d.process.during = lambda: canceled.__setitem__(0, True)
    with pytest.raises(RenderCanceled):
        asyncio.run(d.service.build(replace(config, crf=20), canceled=lambda: canceled[0]))
    assert d.artifacts.verify(retained) == retained
    assert len(d.artifacts.records.history("delivery_video")) == 1 and d.f.selected[0] == d.f.source


def test_reopen_retains_settings_history_and_verified_reports(delivery):
    d = delivery; config = DeliveryConfig(target_lufs=-18, crf=20)
    result = asyncio.run(d.service.build(config)); root = d.f.session.repository.workspace
    d.f.session.close()
    reopened = ProjectSession.open(root, repository_factory=ProjectRepository)
    try:
        artifacts = ProjectDelivery(LocalArtifactStore.for_project(reopened.repository), reopened.project.id)
        assert artifacts.current() == config and artifacts.cached(result.metadata["fingerprint"]) == result
        assert artifacts.report(result)["source_checksum"] == d.f.source.checksum
    finally:
        reopened.close()


def test_measurement_json_rejects_incomplete_analysis():
    with pytest.raises(ValueError): loudness_json(b'{"input_i": "-16"}')
    with pytest.raises(ValueError): loudness_json(loudness("nan", -2))


def test_new_original_render_marks_delivery_stale_without_affecting_history(delivery):
    d = delivery; config = DeliveryConfig()
    result = asyncio.run(d.service.build(config))
    raw = d.f.store.read_artifact(d.f.source.storage_key)
    d.f.selected[0] = d.f.store.save_artifact("new-original.mp4", raw,
        {"artifact_type": "video_render", "project_id": d.f.session.project.id, "render": d.f.source.metadata["render"]})
    assert d.artifacts.cached(d.service.prepare(config)[2]) is None
    assert d.artifacts.verify(result) == result


def test_new_branded_output_invalidates_only_its_delivery(delivery):
    d = delivery; config = DeliveryConfig(source="branded", preset="source")
    asyncio.run(d.f.service.build(d.f.config))
    result = asyncio.run(d.service.build(config))
    asyncio.run(d.f.service.build(replace(d.f.config, cta="New branding")))
    assert d.artifacts.cached(d.service.prepare(config)[2]) is None
    assert d.artifacts.verify(result) == result and d.f.selected[0] == d.f.source


def test_dynamic_normalization_fallback_is_reported(delivery):
    d = delivery; d.process.before = loudness(mode="dynamic")
    result = asyncio.run(d.service.build(DeliveryConfig()))
    assert result.metadata["delivery"]["normalization_type"] == "dynamic"


def test_excessive_gain_is_not_applied(delivery):
    d = delivery; d.process.before = loudness(-39, -35)
    d.process.after = d.process.before
    result = asyncio.run(d.service.build(DeliveryConfig(target_lufs=-10)))
    assert result.metadata["delivery"]["status"] == "REVIEW" and not result.metadata["delivery"]["normalization_applied"]
    assert any("24 dB" in reason for reason in result.metadata["delivery"]["reasons"])


def test_atomic_publication_rejects_changed_bytes_before_registering_output(delivery):
    d = delivery
    _, identity, fingerprint = d.service.prepare(DeliveryConfig(preset="source"))
    report = {"fully_decoded": True, "checksum": sha256(b"verified").hexdigest(), "size_bytes": len(b"verified")}
    with pytest.raises(ValueError, match="atomic publication"):
        d.service._publish(BytesIO(b"changed"), report, identity, fingerprint)
    assert not d.artifacts.records.history("delivery_video") and d.f.selected[0] == d.f.source
