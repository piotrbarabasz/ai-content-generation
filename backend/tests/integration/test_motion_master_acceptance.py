"""D066 retained source safety, immutable lineage and paid-provider-free resume."""

import asyncio
from dataclasses import replace
from io import BytesIO
from random import Random
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from app.application.automatic_workflow import (
    AutomaticWorkflow, AutomaticWorkflowCanceled, AutomaticWorkflowConfig,
)
from app.application.image_intake import ImageIntakeService
from app.application.projects import ProjectSession
from app.desktop.pipeline_driver import DesktopPipelineDriver
from app.desktop.scene_composition import compose_scenes
from app.desktop.timeline_composition import compose_timeline
from app.storage.project_repository import ProjectRepository
from app.storage.section_tempo import SectionTempoArtifacts
from tests.integration.test_final_image_presets import TargetProvider, png, project  # noqa: F401
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared, snapshot  # noqa: F401


@pytest.mark.parametrize("failure", ["inference", "truncated", "dimensions", "diagnostics"])
def test_failed_motion_master_keeps_prior_valid_final_and_no_authoritative_intermediate(project, failure):
    _, store, images, source, _, provider, service = project
    final = service.create_final_selected(source.artifact_id, "landscape", "fhd")
    selected = images.selected(source.scene_id)
    before = snapshot(store)
    original = provider.upscale

    def fail(request):
        # Private worker bytes are never registered as scene artifacts.
        private = store.root.parent / "work" / "upscale"
        private.mkdir(parents=True, exist_ok=True)
        (private / "partial.png").write_bytes(b"partial")
        if failure == "inference":
            raise RuntimeError("synthetic inference failed")
        result = original(request)
        if failure == "truncated":
            return replace(result, image_bytes=result.image_bytes[:40])
        if failure == "dimensions":
            return replace(result, width=100)
        return replace(result, metadata={"diagnostics": {}})

    provider.upscale = fail
    with pytest.raises((ValueError, RuntimeError)):
        service.create_motion_master_selected(final, "landscape", "fhd")
    assert images.selected(source.scene_id) == selected
    assert snapshot(store) == before
    assert images.image(final).provenance == "final"
    assert images.image(source.artifact_id) == source


def test_stale_motion_master_does_not_publish_or_replace_new_selection(project, tmp_path):
    _, store, images, source, selected, provider, service = project
    request = service.prepare_motion_master(source.artifact_id, "landscape", "fhd")
    alternative_path = tmp_path / "alternative.png"
    alternative_path.write_bytes(png((640, 360)))
    alternative = images.import_file(source.acceptance_id, source.scene_id, alternative_path)
    ImageIntakeService(images).select(alternative.artifact_id, expected_selection_id=selected.id)
    before = snapshot(store)
    with pytest.raises(ValueError, match="stale motion master"):
        service.publish_motion_master(request, provider.upscale(request[2]))
    assert snapshot(store) == before
    assert images.selected(source.scene_id).artifact_id == alternative.artifact_id
    assert images.image(source.artifact_id) == source


def test_master_lineage_and_cache_cover_source_profile_and_upscaler_identity(project, tmp_path):
    _, store, images, source, _, provider, service = project
    request = service.prepare_motion_master(source.artifact_id, "landscape", "fhd")
    master_id = service.publish_motion_master(request, provider.upscale(request[2]))
    master = images.image(master_id)
    assert (master.source_artifact_id, master.source_checksum, master.source_width, master.source_height) == (
        source.artifact_id, source.checksum, 640, 360)
    assert (master.target_profile, master.delivery_width, master.delivery_height) == ("fhd", 1920, 1080)
    assert (master.master_width, master.master_height, master.overscan_policy) == (2400, 1350, "5:4")
    assert (master.native_model_scale, master.native_width, master.native_height,
            master.final_resize_method) == (4, 2560, 1440, "Lanczos")
    assert all(master.upscaler[key] == provider.capabilities().to_payload()[key]
               for key in ("provider", "model", "version", "runtime", "dtype", "tile"))
    assert service.prepare_motion_master(master_id, "landscape", "fhd")[3] == request[3]
    assert service.prepare_motion_master(master_id, "landscape", "qhd")[3] != request[3]
    original_caps = provider.capabilities
    for field in ("provider", "model", "version", "runtime"):
        provider.capabilities = lambda field=field: replace(original_caps(), **{field: "changed"})
        changed = service.prepare_motion_master(master_id, "landscape", "fhd")
        assert changed[3] != request[3] and service.cached_motion_master(changed) is None
    provider.capabilities = original_caps
    other_path = tmp_path / "other.png"
    other_path.write_bytes(png((1280, 720)))
    other = images.import_file(source.acceptance_id, source.scene_id, other_path)
    service.intake.select(other.artifact_id, expected_selection_id=images.selected(source.scene_id).id)
    assert service.prepare_motion_master(other.artifact_id, "landscape", "fhd")[3] != request[3]
    # Reading a retained master validates original source bytes, not just its own PNG.
    manifest = next(m for m in store.list_artifacts() if m.artifact_id == source.artifact_id)
    (store.root / manifest.storage_key).write_bytes(b"corrupt original")
    with pytest.raises(ValueError, match="retained measurements"):
        images.image(master_id)


def test_motion_master_from_chained_legacy_upscales_resolves_original(project):
    _, _, images, source, _, _, service = project
    first = service.upscale_selected(source.scene_id, 2)
    second = service.upscale_selected(source.scene_id, 2)
    assert images.image(second).source_artifact_id == first
    master = images.image(service.create_motion_master_selected(second, "landscape", "fhd"))
    assert master.source_artifact_id == source.artifact_id


def test_high_detail_4k_master_uses_derivative_byte_limit(project):
    _, _, images, source, _, provider, service = project
    original = provider.upscale

    def detailed(request):
        result = original(request)
        size = (request.target_width, request.target_height)
        output = BytesIO()
        Image.frombytes("RGB", size, Random(66).randbytes(size[0] * size[1] * 3)).save(output, format="PNG")
        payload = output.getvalue()
        assert 16 * 1024 * 1024 < len(payload) < 64 * 1024 * 1024
        return replace(result, image_bytes=payload)

    provider.upscale = detailed
    master = images.image(service.create_motion_master_selected(source.artifact_id, "landscape", "uhd4k"))
    assert (master.width, master.height) == (4800, 2700)
    assert images.selected(source.scene_id).artifact_id == master.artifact_id


def test_upscaler_identity_changed_during_inference_preserves_selection(project):
    _, store, images, source, choice, provider, service = project
    request = service.prepare_motion_master(source.artifact_id, "landscape", "fhd")
    result = provider.upscale(request[2])
    original = provider.capabilities()
    provider.capabilities = lambda: replace(original, runtime="changed")
    before = snapshot(store)
    with pytest.raises(ValueError, match="identity changed"):
        service.publish_motion_master(request, result)
    assert snapshot(store) == before and images.selected(source.scene_id) == choice


@pytest.mark.parametrize("orientation,size,master_size", [
    ("landscape", (800, 450), (3200, 1800)), ("portrait", (450, 800), (1800, 3200)),
])
def test_exact_native_master_retains_no_resize_and_qhd_delivery(project, tmp_path, orientation, size, master_size):
    _, _, images, old, choice, _, service = project
    path = tmp_path / "native.png"
    path.write_bytes(png(size))
    source = images.import_file(old.acceptance_id, old.scene_id, path)
    service.intake.select(source.artifact_id, expected_selection_id=choice.id)
    master = images.image(service.create_motion_master_selected(source.artifact_id, orientation, "qhd"))
    assert (master.width, master.height) == master_size
    assert (master.native_width, master.native_height) == master_size
    assert master.final_resize_method is None


def test_selection_changed_on_coordinator_while_worker_runs_rejects_late_master(project, tmp_path):
    session, store, images, source, choice, provider, service = project
    services = compose_scenes(session, upscale_provider=provider)
    editor = SimpleNamespace(visuals=SimpleNamespace(services=services))
    config = AutomaticWorkflowConfig(SimpleNamespace(provider="fake"), None, "landscape", "fhd")
    driver = DesktopPipelineDriver(editor, config)
    path = tmp_path / "new-selection.png"
    path.write_bytes(png((640, 360)))
    alternative = images.import_file(source.acceptance_id, source.scene_id, path)
    completed = threading.Event()
    original = provider.upscale
    before = None

    async def run():
        loop = asyncio.get_running_loop()

        def select_on_owner():
            nonlocal before
            try:
                service.intake.select(alternative.artifact_id, expected_selection_id=choice.id)
                before = snapshot(store)
            finally:
                completed.set()

        def inference(request):
            loop.call_soon_threadsafe(select_on_owner)
            assert completed.wait(10), "Selection change must commit on the coordinator"
            return original(request)

        provider.upscale = inference
        with pytest.raises(ValueError, match="stale motion master"):
            await driver.create_motion_master(session.active_script.sections[0], SimpleNamespace(id=source.scene_id), config)

    asyncio.run(run())
    assert before is not None and snapshot(store) == before
    assert images.selected(source.scene_id).artifact_id == alternative.artifact_id
    assert images.image(source.artifact_id) == source


@pytest.mark.parametrize("field,value", [("source_checksum", "d" * 64), ("source_width", 639)])
def test_retained_master_rejects_source_lineage_mismatch(project, field, value):
    _, store, images, source, _, _, service = project
    master_id = service.create_motion_master_selected(source.artifact_id, "landscape", "fhd")
    manifest = next(m for m in store.list_artifacts() if m.artifact_id == master_id)
    lineage = {**manifest.metadata["scene_image"], field: value}
    if field == "source_width":
        lineage["native_width"] = value * 4
    forged = store.save_artifact("inconsistent-master.png", store.read_artifact(manifest.storage_key),
        {"artifact_type": "scene_image", **manifest.metadata, "scene_image": lineage})
    with pytest.raises(ValueError, match="source lineage differs"):
        images.image(forged.artifact_id)
    assert images.selected(source.scene_id).artifact_id == master_id


class PaidProviderSpy:
    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        raise AssertionError("Existing project images must not call the paid provider")


def editor_for(session, paid, upscaler):
    services = compose_scenes(session, image_provider=paid, upscale_provider=upscaler)
    timeline = compose_timeline(session)

    def playback(section, variant, choice):
        audio = SectionTempoArtifacts(services.store._index, services.store).selected(section, variant)
        assert audio is not None and audio.revision_id == section.id
        return SimpleNamespace(stale=False)

    return SimpleNamespace(session=session, dirty=False, worker=None,
        video_plan=SimpleNamespace(plans=None),
        audio=SimpleNamespace(services=SimpleNamespace(playback=playback, attempt=None)),
        visuals=SimpleNamespace(services=services),
        timeline=SimpleNamespace(services=timeline, refresh=lambda: None), _bind_regeneration=lambda: None)


@pytest.mark.parametrize("cancel_result", ["late_success", "worker_error", "no_cancel_hook"])
def test_existing_project_automatic_resume_reuses_completed_master_with_zero_paid_calls(setup, prepared, tmp_path, cancel_result):
    session = setup[0]
    section, _, _, _, accepted, _, _, _, _, _, _ = prepared
    current = session.active_script
    session.save_script(replace(current, id="script_revision_d066_existing", sections=(section,),
                                parent_revision_id=current.id), expected_active_revision_id=current.id)
    paid, provider = PaidProviderSpy(), TargetProvider()
    editor = editor_for(session, paid, provider)
    services = editor.visuals.services
    brief = services.prompts.pin_context("film_brief", "Synthetic offline film")
    style = services.prompts.pin_context("visual_style", "Plain colors")
    sources, finals = [], []
    for scene in accepted.plan.scenes:
        prompt = services.prompts.create_manual(accepted.id, scene.id, brief.id, style.id, "Retained manual prompt")
        services.prompts.select(prompt.id, expected_selection_id=None)
        path = tmp_path / f"{scene.id}.png"
        path.write_bytes(png((640, 360)))
        source = services.images.import_file(accepted.id, scene.id, path)
        services.intake.select(source.artifact_id, expected_selection_id=services.images.selected(scene.id).id)
        sources.append(source)
        finals.append(services.upscale.create_final_selected(source.artifact_id, "landscape", "fhd"))
    assert len(sources) == 2
    provider.calls.clear()
    config = AutomaticWorkflowConfig(SimpleNamespace(provider="fake"), None, "landscape", "fhd")
    driver = DesktopPipelineDriver(editor, config)
    workflow = AutomaticWorkflow(driver)
    original = provider.upscale
    canceled = threading.Event()
    if cancel_result != "no_cancel_hook":
        provider.cancel = canceled.set

    async def interrupt():
        loop = asyncio.get_running_loop()

        def cancel_on_owner():
            workflow.cancel()
            canceled.set()

        def infer(request):
            result = original(request)
            if len(provider.calls) == 2:
                loop.call_soon_threadsafe(cancel_on_owner)
                assert canceled.wait(10), "Coordinator must process cancellation"
                if cancel_result == "worker_error":
                    raise RuntimeError("worker acknowledged cancellation")
            return result  # Even a late, valid worker result must not publish after cancellation.

        provider.upscale = infer
        with pytest.raises(AutomaticWorkflowCanceled, match="MOTION_MASTER"):
            await workflow.run(config)

    asyncio.run(interrupt())
    first_master = services.images.selected(sources[0].scene_id).artifact_id
    assert services.images.image(first_master).provenance == "motion_master"
    assert services.images.selected(sources[1].scene_id).artifact_id == finals[1]
    masters = [m for m in services.store.list_artifacts() if m.module_name == "desktop_image_motion_master"]
    assert len(masters) == 1 and len(provider.calls) == 2 and paid.calls == 0
    root = session.repository.workspace
    session.close()
    provider.upscale = original
    with ProjectSession.open(root, repository_factory=ProjectRepository) as reopened:
        editor = editor_for(reopened, paid, provider)
        result = asyncio.run(AutomaticWorkflow(DesktopPipelineDriver(editor, config)).run(config))
        assert result.scenes == result.timeline_clips == 2
        assert len(provider.calls) == 3 and paid.calls == 0
        images = editor.visuals.services.images
        assert images.selected(sources[0].scene_id).artifact_id == first_master
        assert all(images.image(images.selected(source.scene_id).artifact_id).source_artifact_id == source.artifact_id
                   for source in sources)
        timeline = editor.timeline.services.current().timeline
        before = snapshot(editor.visuals.services.store)
        asyncio.run(AutomaticWorkflow(DesktopPipelineDriver(editor, config)).run(config))
        assert snapshot(editor.visuals.services.store) == before
        assert editor.timeline.services.current().timeline == timeline
        assert len(provider.calls) == 3 and paid.calls == 0
        # Zoom/Pan changes affect render/proxy requests only: the same retained
        # sources, masters, audio and timeline are reused by AutomaticWorkflow.
        changed = replace(config, zoom_intensity="medium", pan_intensity="subtle")
        asyncio.run(AutomaticWorkflow(DesktopPipelineDriver(editor, changed)).run(changed))
        from app.domain.scene_motion import MotionConfig
        assert editor.visuals.services.motion_settings() == MotionConfig("medium", "subtle")
        assert editor.timeline.services.current().timeline == timeline
        assert images.selected(sources[0].scene_id).artifact_id == first_master
        assert len(provider.calls) == 3 and paid.calls == 0
