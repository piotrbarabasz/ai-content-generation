"""Qt persistence and render-only freshness through actual project services."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.application.projects import ProjectSession
from app.application.automatic_workflow import AutomaticWorkflowConfig
from app.desktop.scene_composition import compose_scenes
from app.desktop.scene_panel import ScenePanel
from app.desktop.pipeline_driver import DesktopPipelineDriver
from app.domain.scene_motion import MotionConfig
from app.storage.project_repository import ProjectRepository
from app.storage.scene_motion import ProjectMotionSettings
from app.storage.regeneration import ProjectRegeneration
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared  # noqa: F401
from tests.integration.test_video_render import render, claim  # noqa: F401
from tests.integration.test_preview import preview  # noqa: F401


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def test_qt_zoom_and_pan_controls_save_and_reopen_without_schema_migration(qt, tmp_path):
    root = tmp_path / "project"
    with ProjectSession.create(root, repository_factory=ProjectRepository, name="Motion") as session:
        services = compose_scenes(session)
        before = services.store.list_artifacts()
        panel = ScenePanel()
        panel.bind(services)
        assert services.store.list_artifacts() == before  # Opening is read-only for settings.
        assert panel.motion_config() == MotionConfig()
        for combo in (panel.zoom_intensity, panel.pan_intensity):
            assert [combo.itemText(i) for i in range(combo.count())] == ["Off", "Subtle", "Medium"]
            assert combo.isEnabled()
        notifications = []
        panel.motion_changed.connect(lambda: notifications.append(True))
        panel.zoom_intensity.setCurrentIndex(panel.zoom_intensity.findData("medium"))
        panel.pan_intensity.setCurrentIndex(panel.pan_intensity.findData("subtle"))
        assert services.motion_settings() == MotionConfig("medium", "subtle") and len(notifications) == 2
        assert len(services.store.list_artifacts()) == 2
        panel.close()
        pid = session.project.id
    with ProjectSession.open(root, repository_factory=ProjectRepository) as session:
        assert session.project.id == pid
        assert session.repository._connection.execute("PRAGMA user_version").fetchone()[0] == 2
        panel = ScenePanel()
        services = compose_scenes(session)
        panel.bind(services)
        assert panel.motion_config() == MotionConfig("medium", "subtle")
        # AutomaticWorkflow receives the reopened UI choices; its driver persists
        # those exact choices rather than applying another default on Resume.
        config = AutomaticWorkflowConfig(object(), None, "landscape", "fhd", "original",
                                        panel.zoom_intensity.currentData(), panel.pan_intensity.currentData())
        driver = DesktopPipelineDriver(SimpleNamespace(visuals=SimpleNamespace(services=services)), config)
        count = len(services.store.list_artifacts())
        driver.configure_motion(config)
        assert services.motion_settings() == panel.motion_config()
        assert len(services.store.list_artifacts()) == count
        panel.close()


def test_motion_change_stales_only_render_and_preserves_exact_media_and_timeline(render):
    r = render
    settings = ProjectMotionSettings(r.store, r.index.project_id)
    r.provider.motion_settings = settings.current
    session = SimpleNamespace(repository=r.index.repository, project=r.index.repository.project(),
                              active_script=r.index.repository.active_script())
    timeline = SimpleNamespace(current=lambda: SimpleNamespace(timeline=r.timeline))
    adapter = ProjectRegeneration(session, r.index, r.store, timeline=timeline, render=r.service)
    stage = next(s for s in adapter.build() if s.key == "project:video_render")
    owned = claim(r)
    request = r.coordinator.repository.get_job(owned.job_id).request
    assert request.algorithm_version == "3"
    assert json.loads(request.settings_json)["zoom_intensity"] == "subtle"
    result = asyncio.run(r.service.run(owned))
    assert result.selected_at_publication and stage.inspect().state == "fresh"
    before = {m.artifact_id: m.checksum for m in r.store.list_artifacts()}
    media = r.timeline.to_payload()
    selections = r.index.media.images.selected(r.timeline.clips[0].media.scene_id)
    settings.save(MotionConfig("medium", "subtle"))
    assert stage.inspect().state == "stale"
    assert not r.adapter.motion_current()
    assert r.timeline.to_payload() == media
    r.adapter.current(r.timeline)  # Image/audio/timing remain usable.
    assert r.index.media.images.selected(r.timeline.clips[0].media.scene_id) == selections
    after = {m.artifact_id: m.checksum for m in r.store.list_artifacts()}
    assert all(after[k] == v for k, v in before.items())
    assert r.index.selected()["project:video_render"] == result.artifact_id  # Retained history survives.
    newer = claim(r)
    newer_request = r.coordinator.repository.get_job(newer.job_id).request
    assert newer_request.fingerprint != request.fingerprint
    assert json.loads(newer_request.settings_json)["pan_intensity"] == "subtle"
    asyncio.run(r.service.run(newer))
    assert stage.inspect().state == "fresh"
    assert r.adapter.motion_current()


def test_motion_change_during_render_keeps_late_result_historical(render):
    r = render
    settings = ProjectMotionSettings(r.store, r.index.project_id)
    r.provider.motion_settings = settings.current
    owned = claim(r)
    r.process.during = lambda: settings.save(MotionConfig("off", "off"))
    result = asyncio.run(r.service.run(owned))
    assert not result.selected_at_publication
    assert "project:video_render" not in r.index.selected()
    assert r.coordinator.repository.get_attempt(owned.id).status == "completed"


def test_new_renderer_replays_historical_request_with_original_policy(render):
    r = render
    r.provider.motion = None
    owned = claim(r)  # Exact algorithm-v2 request is enqueued before controls.
    original = r.coordinator.repository.get_job(owned.job_id).request
    r.provider.motion_settings = lambda: MotionConfig("off", "medium")
    result = asyncio.run(r.service.run(owned))
    assert result.selected_at_publication
    assert r.coordinator.repository.get_job(owned.job_id).request == original
    assert json.loads(original.settings_json)["motion_policy"] == "auto-subtle-v1"


def test_proxy_rebuilds_only_for_changed_motion_and_reuses_matching_cache(preview):
    p = preview
    media = p.media.render_media
    settings = ProjectMotionSettings(media.store, media.index.project_id)
    p.service.renderer.motion_settings = settings.current
    paid_calls = list(p.prepared[2].calls)
    first = asyncio.run(p.service.proxy(p.timeline))
    assert first.current and not first.cached
    settings.save(MotionConfig("off", "subtle"))
    second = asyncio.run(p.service.proxy(p.timeline))
    assert second.current and not second.cached and second.cache_key != first.cache_key
    third = asyncio.run(p.service.proxy(p.timeline))
    assert third.current and third.cached and third.cache_key == second.cache_key
    assert len(p.process.calls) == 6 and p.prepared[2].calls == paid_calls


def test_proxy_changed_motion_during_encode_is_not_presented_as_current(preview):
    p = preview
    media = p.media.render_media
    settings = ProjectMotionSettings(media.store, media.index.project_id)
    p.service.renderer.motion_settings = settings.current
    original = p.process.run

    async def run(command, **kwargs):
        result = await original(command, **kwargs)
        if "-filter_complex_script" in command:
            settings.save(MotionConfig("off", "off"))
        return result

    p.process.run = run
    result = asyncio.run(p.service.proxy(p.timeline))
    assert not result.current


def test_editor_automatic_workflow_receives_visible_zoom_pan_choices(qt, tmp_path, monkeypatch):
    from app.desktop import editor as editor_module
    from app.desktop.__main__ import LocalProjects

    received = []

    class Workflow:
        def __init__(self, driver):
            self.driver = driver

        async def run(self, config):
            received.append(config)
            self.driver.configure_motion(config)
            return SimpleNamespace(sections=0, scenes=0, timeline_clips=0, duration="0", skipped=0,
                                   plan_script_groups_total=0, plan_script_groups_completed=0)

    monkeypatch.setattr(editor_module, "AutomaticWorkflow", Workflow)
    editor = editor_module.ProjectEditor(LocalProjects(),
        scene_factory=lambda session: compose_scenes(session, image_provider=object()))
    editor.load_project(tmp_path / "editor", create=True)
    try:
        services = editor.visuals.services
        services.prompts.pin_context("film_brief", "Brief")
        services.prompts.pin_context("visual_style", "Style")
        editor.audio.services = object()
        editor.audio.voices.addItem("Offline voice", SimpleNamespace(provider="fixture"))
        editor.timeline.services = SimpleNamespace(current=lambda: None)
        editor.timeline.refresh = lambda: None
        editor.workflow_mode.setCurrentIndex(editor.workflow_mode.findData("automatic"))
        editor.visuals.resolution.setCurrentIndex(editor.visuals.resolution.findData("draft"))
        editor.visuals.zoom_intensity.setCurrentIndex(editor.visuals.zoom_intensity.findData("off"))
        editor.visuals.pan_intensity.setCurrentIndex(editor.visuals.pan_intensity.findData("medium"))
        editor.run_automatic_workflow()
        assert editor.automatic_busy, editor.workflow_status.text()
        editor.automatic_timer.stop()
        editor._automatic_tick()
        assert len(received) == 1
        assert (received[0].zoom_intensity, received[0].pan_intensity) == ("off", "medium")
        assert services.motion_settings() == MotionConfig("off", "medium")
    finally:
        editor.audio.services = None
        editor.close()
