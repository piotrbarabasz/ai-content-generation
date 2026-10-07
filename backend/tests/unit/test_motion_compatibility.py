"""D066 historical snapshots and strict v3 master metadata."""

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from app.application.preview import proxy_cache_key
from app.domain.dependencies import RequestFingerprint
from app.domain.render_result import delivery_profile, render_request
from app.domain.scene_image import SceneImage
from app.domain.timeline import TimelineRevision
from tests.unit.test_timeline import compile_values, legacy_timeline_payload, media


def motion_image(image, *, portrait=False):
    source = (360, 640) if portrait else (640, 360)
    delivery = (1080, 1920) if portrait else (1920, 1080)
    master = tuple(n * 5 // 4 for n in delivery)
    return replace(image, width=master[0], height=master[1], provenance="motion_master", lineage_version=3,
        source_artifact_id="source-" + image.scene_id, source_checksum="c" * 64,
        source_width=source[0], source_height=source[1], target_profile="fhd",
        master_width=master[0], master_height=master[1], delivery_width=delivery[0], delivery_height=delivery[1],
        overscan_policy="5:4", native_model_scale=4, native_width=source[0] * 4, native_height=source[1] * 4,
        final_resize_method="Lanczos", upscaler={"provider": "fixture", "model": "x4", "version": "1", "runtime": "fake"})


def final_image(image):
    return replace(image, width=1920, height=1080, provenance="final", lineage_version=2,
        source_artifact_id="source-" + image.scene_id, source_checksum="c" * 64,
        source_width=640, source_height=360, target_profile="fhd", target_width=1920, target_height=1080,
        native_model_scale=4, native_width=2560, native_height=1440, final_resize_method="Lanczos")


@pytest.mark.parametrize("version", [1, 2, 3])
def test_scene_image_manifest_versions_keep_dimensions_and_provenance(version):
    image = media().image
    if version == 1:
        image = replace(image, width=24, height=16, provenance="upscaled", source_artifact_id="source-a",
                        source_checksum="c" * 64, source_width=12, source_height=8, scale=2, upscaler={})
    elif version == 2:
        image = final_image(image)
    else:
        image = motion_image(image)
    lineage = {"version": version, **asdict(image)}
    for key in ("artifact_id", "checksum", "size_bytes"):
        lineage.pop(key)
    if version < 3:
        for key in ("master_width", "master_height", "delivery_width", "delivery_height", "overscan_policy"):
            lineage.pop(key)
    if version == 1:
        for key in ("lineage_version", "target_profile", "target_width", "target_height", "native_model_scale",
                    "native_width", "native_height", "final_resize_method"):
            lineage.pop(key)
    manifest = SimpleNamespace(metadata={"scene_image": lineage}, artifact_type="scene_image",
                               artifact_id=image.artifact_id, checksum=image.checksum, size_bytes=image.size_bytes)
    restored = SceneImage.from_manifest(manifest)
    assert restored == image
    assert (restored.width, restored.height, restored.provenance) == (image.width, image.height, image.provenance)


@pytest.mark.parametrize("changes", [
    {"master_width": 2399, "width": 2399}, {"delivery_width": 1919}, {"target_profile": "qhd"},
    {"source_width": 512}, {"source_checksum": "z" * 64}, {"upscaler": None},
    {"final_resize_method": None}, {"lineage_version": 2},
    {"overscan_policy": "4:3"}, {"upscaler": {"provider": "fixture"}},
])
def test_v3_master_rejects_inconsistent_overscan_profile_resize_and_identity(changes):
    with pytest.raises(ValueError, match="[Mm]otion.master"):
        replace(motion_image(media().image), **changes)


def test_literal_historical_timeline_and_request_preserve_original_identity_and_720p():
    payload = legacy_timeline_payload()
    timeline = TimelineRevision.from_payload(deepcopy(payload))
    assert timeline.to_payload() == payload
    assert delivery_profile(timeline) == ("legacy-720p", 1280, 720)
    assert timeline.clips[0].media.image.provenance == "generated"
    request = render_request(timeline, {"adapter": "static-mp4-v1"}, algorithm_version="1")
    assert request == RequestFingerprint.create("timeline.render", "1", inputs=request.inputs,
        settings={"profile": "static-mp4-720p25-v1", "timeline": payload, "captions": None},
        effective_identity={"adapter": "static-mp4-v1"})


def test_motion_policy_changes_final_request_and_proxy_cache_identity(monkeypatch):
    import app.domain.render_result as contract
    timeline = compile_values(replace(media(), image=motion_image(media().image)))
    original = contract.render_request(timeline, {})
    one = proxy_cache_key(timeline, {"motion_policy": "auto-subtle-v1"})
    monkeypatch.setattr(contract, "MOTION_POLICY_VERSION", "auto-subtle-v2")
    assert contract.render_request(timeline, {}).fingerprint != original.fingerprint
    assert proxy_cache_key(timeline, {"motion_policy": "auto-subtle-v2"}) != one
    assert proxy_cache_key(timeline, {"motion_policy": "auto-subtle-v1"}) == one


def test_historical_request_cannot_silently_enable_motion():
    timeline = compile_values(replace(media(), image=motion_image(media().image)))
    with pytest.raises(ValueError, match="Historical render requests"):
        render_request(timeline, {}, algorithm_version="1")
