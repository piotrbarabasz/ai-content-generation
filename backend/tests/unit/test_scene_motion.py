"""Independent family strengths, deterministic resolution and bounded trajectories."""

from dataclasses import replace
import json

import pytest

from app.application.automatic_workflow import AutomaticWorkflowConfig
from app.application.preview import proxy_cache_key
from app.domain.dependencies import RequestFingerprint
from app.domain.render_result import render_request
from app.domain.scene_motion import (BASE_ZOOM, MotionConfig, PAN_TRAVEL, ZOOM_STRENGTH,
                                     resolve_scene_motion)
from tests.unit.test_motion_compatibility import motion_image
from tests.unit.test_timeline import compile_values, media


CONFIGURATIONS = [("off", "off"), ("subtle", "off"), ("medium", "off"),
                  ("off", "subtle"), ("off", "medium"), ("subtle", "subtle"),
                  ("medium", "medium"), ("medium", "subtle"), ("subtle", "medium")]


def test_default_is_zoom_subtle_pan_off_including_automatic_workflow():
    config = AutomaticWorkflowConfig(object(), None, "landscape", "fhd")
    assert (config.zoom_intensity, config.pan_intensity) == ("subtle", "off")
    assert MotionConfig() == MotionConfig("subtle", "off")
    motion = resolve_scene_motion("scene-a")
    assert motion.zoom_direction in ("zoom_in", "zoom_out") and motion.pan_direction is None


@pytest.mark.parametrize("zoom,pan", CONFIGURATIONS)
@pytest.mark.parametrize("dimensions", [(4800, 2700), (2700, 4800)])
def test_motion_is_bounded_smooth_and_monotonic_for_all_directions(zoom, pan, dimensions):
    config = MotionConfig(zoom, pan)
    width, height = dimensions
    for scene in (f"scene-{i}" for i in range(32)):
        motion = resolve_scene_motion(scene, config)
        assert motion == resolve_scene_motion(scene, MotionConfig.from_payload(config.to_payload()))
        samples = [motion.sample(width, height, n / 100) for n in range(101)]
        for z, x, y in samples:
            assert BASE_ZOOM <= z <= BASE_ZOOM * (1 + ZOOM_STRENGTH[zoom])
            assert 0 <= x <= width - width / z and 0 <= y <= height - height / z
        zvalues = [z for z, _, _ in samples]
        assert zvalues == sorted(zvalues, reverse=motion.zoom_direction == "zoom_out")
        # Quantized optical centers can deviate less than one raster pixel.
        for axis, dimension, directions in ((1, width, ("right", "left")), (2, height, ("down", "up"))):
            centers = [s[axis] + dimension / s[0] / 2 for s in samples]
            differences = [b - a for a, b in zip(centers, centers[1:])]
            if motion.pan_direction == directions[0]:
                assert min(differences) > -1
            elif motion.pan_direction == directions[1]:
                assert max(differences) < 1
            else:
                assert max(centers) - min(centers) < 1.001
            assert max(abs(d) for d in differences) <= dimension * 0.2 * PAN_TRAVEL[pan] * 0.016 + 1
        if pan != "off":
            axis, dimension = (1, width) if motion.pan_direction in ("left", "right") else (2, height)
            centers = [s[axis] + dimension / s[0] / 2 for s in samples]
            assert abs(abs(centers[-1] - centers[0]) - dimension * 0.2 * PAN_TRAVEL[pan]) < 1
            assert abs(centers[1] - centers[0]) < abs(centers[50] - centers[49]) + 1


def test_family_direction_is_stable_when_the_other_family_changes():
    for i in range(20):
        assert resolve_scene_motion(str(i), MotionConfig("subtle", "off")).zoom_direction == (
            resolve_scene_motion(str(i), MotionConfig("subtle", "medium")).zoom_direction)
        assert resolve_scene_motion(str(i), MotionConfig("off", "subtle")).pan_direction == (
            resolve_scene_motion(str(i), MotionConfig("medium", "subtle")).pan_direction)


def test_controlled_request_cache_changes_without_touching_timeline_or_images():
    timeline = compile_values(replace(media(), image=motion_image(media().image)))
    requests, proxies = set(), set()
    for zoom, pan in CONFIGURATIONS:
        config = MotionConfig(zoom, pan)
        request = render_request(timeline, config.to_payload())
        assert request.algorithm_version == "3"
        assert json.loads(request.settings_json)["timeline"] == timeline.to_payload()
        assert {e.artifact_id for e in request.inputs} == {"audio-a", "image-a"}
        requests.add(request.fingerprint)
        proxies.add(proxy_cache_key(timeline, config.to_payload()))
    assert len(requests) == len(proxies) == len(CONFIGURATIONS)


def test_literal_d066_v1_request_keeps_exact_identity():
    timeline = compile_values(replace(media(), image=motion_image(media().image)))
    identity = {"adapter": "motion-mp4-v1", "motion_policy": "auto-subtle-v1"}
    request = render_request(timeline, identity, algorithm_version="2")
    historical = RequestFingerprint.create("timeline.render", "2", inputs=request.inputs,
        settings={"profile": "motion-mp4-v1", "delivery_profile": "fhd", "width": 1920, "height": 1080,
                  "motion_policy": "auto-subtle-v1", "timeline": timeline.to_payload(), "captions": None},
        effective_identity=identity)
    assert request == historical and request.fingerprint == historical.fingerprint
    assert render_request(timeline, MotionConfig().to_payload()).fingerprint != historical.fingerprint


@pytest.mark.parametrize("config", [{"zoom_intensity": "strong"}, {"pan_intensity": "shake"},
                                     {"motion_policy": "future"}])
def test_unknown_motion_values_are_rejected(config):
    with pytest.raises(ValueError):
        MotionConfig(**config)
