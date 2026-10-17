"""D080: the real Export action consumes retained media, without proxy/AI prerequisites."""

import asyncio
from functools import partial
from hashlib import sha256
import os
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from app.desktop.__main__ import LocalProjects
from app.desktop.cloud_audio import CloudAudioServices
from app.desktop.editor import ProjectEditor
from app.desktop.regeneration_composition import compose_regeneration
from app.desktop.timeline_composition import compose_timeline
from app.providers.openai_tts import OpenAITTSProvider
from app.storage.video_render import ProjectVideoRender
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared, snapshot  # noqa: F401
from tests.integration.test_video_render import Process


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def wait(qt, condition, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Timed out waiting for render lifecycle")


@pytest.fixture
def project(setup, prepared, request):
    session = setup[0]
    if getattr(request, "param", None) == "fhd":
        from app.application.image_upscale import ImageUpscaleService
        from tests.integration.test_final_image_presets import TargetProvider, png
        # Pre-existing synthetic motion masters, made before any editor action.
        upscale = ImageUpscaleService(prepared[6].images, setup[3], TargetProvider())
        prepared[7].write_bytes(png((640, 360)))
        for image, selection in prepared[8]:
            wide, _ = prepared[6].import_and_select(prepared[4].id, image.scene_id,
                prepared[7], expected_selection_id=selection.id)
            upscale.create_motion_master_selected(wide.artifact_id, "landscape", "fhd")
    timelines = compose_timeline(session)
    timeline = prepared[9].compile(session.project.id, prepared[10])
    edit = timelines.history.save(timeline, None)
    before = snapshot(setup[3])
    heads = setup[2].selected()
    root, script_id = session.repository.workspace, session.active_script.id
    proxy = root / "cache/previews/retained.mp4"
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b"retained proxy fixture; never selected as final")
    session.close()
    return SimpleNamespace(root=root, timeline=timeline, edit=edit, before=before,
                           heads=heads, script_id=script_id, proxy=proxy)


@pytest.fixture
def window(qt, project, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Final rendering must not call OpenAI or regenerate assets")
    monkeypatch.setattr(OpenAITTSProvider, "synthesize", forbidden)
    from app.providers.openai_llm import OpenAILLMProvider
    from app.providers.openai_image import OpenAIImageProvider
    monkeypatch.setattr(OpenAILLMProvider, "generate_structured", forbidden)
    monkeypatch.setattr(OpenAIImageProvider, "generate", forbidden)
    from app.desktop.scene_services import SceneServices
    from app.application.script_generation import ScriptGenerationService
    monkeypatch.setattr(ScriptGenerationService, "generate", forbidden)
    for name in ("generate_image", "regenerate_prompt", "rebuild_scene_timing"):
        monkeypatch.setattr(SceneServices, name, forbidden)
    process = Process(project.timeline)
    executable = tmp_path / "not-executed.exe"
    executable.write_bytes(b"injected offline process fixture")
    w = ProjectEditor(LocalProjects(), audio_factory=CloudAudioServices,
        timeline_factory=compose_timeline,
        regeneration_factory=partial(compose_regeneration, ffmpeg=executable,
                                     ffprobe=executable, process=process))
    w.show()
    w.load_project(project.root)
    wait(qt, lambda: not w._render_checks)
    w.tabs.setCurrentWidget(w.export_tab)
    w.test_process = process
    yield w
    if w.regeneration.busy:
        w.regeneration.cancel()
        wait(qt, lambda: not w.regeneration.busy)
    wait(qt, lambda: not w._render_checks)
    w.dirty = False
    w.close()


def assert_preserved(window, project):
    assert window.session.active_script.id == project.script_id
    assert window.timeline.services.current() == project.edit
    heads = window.export_panel.index.selected()
    assert all(heads[key] == value for key, value in project.heads.items())
    store = window.export_panel.store
    for manifest in store.list_artifacts():
        if manifest.artifact_id in project.before:
            assert store.read_artifact(manifest.storage_key) == project.before[manifest.artifact_id]
    assert project.proxy.read_bytes() == b"retained proxy fixture; never selected as final"


def test_cloud_audio_and_saved_timeline_without_preview_enable_actual_button(window, project):
    assert isinstance(window.audio.services, CloudAudioServices)
    assert not hasattr(window.audio.services, "production")  # Original failing composition.
    assert window.preview.services is None
    assert window.timeline.edit.timeline == project.timeline
    assert window.final_render_button.isEnabled()
    assert "Ready for final rendering" in window.final_render_status.text()
    assert not window.advanced_content.isVisible()
    assert not window.export_panel.copy_button.isEnabled()
    assert_preserved(window, project)


def test_missing_timeline_and_timeline_refresh_synchronize_button(window, qt):
    current = window.timeline.edit
    window.timeline.edit = None
    window._timeline_changed(None)
    assert not window.final_render_button.isEnabled()
    assert "Timeline missing" in window.final_render_status.text()
    window.timeline.refresh()
    wait(qt, lambda: not window._render_checks)
    assert window.timeline.edit == current
    assert window.final_render_button.isEnabled()


def test_missing_tools_and_initialization_errors_visible_outside_advanced(window, qt, monkeypatch):
    from app.desktop import regeneration_composition
    window.regeneration_factory = compose_regeneration
    monkeypatch.setattr(regeneration_composition, "media_executables", lambda: (None, None))
    QTest.mouseClick(window.render_refresh_button, Qt.LeftButton)
    assert not window.final_render_button.isEnabled()
    assert "FFmpeg and ffprobe" in window.final_render_status.text()
    monkeypatch.setattr(regeneration_composition, "media_executables",
                        lambda: ("missing-ffmpeg.exe", "missing-ffprobe.exe"))
    QTest.mouseClick(window.render_refresh_button, Qt.LeftButton)
    assert not window.final_render_button.isEnabled()
    assert "service unavailable" in window.final_render_status.text()
    assert not window.advanced_content.isVisible()
    def broken(*args):
        raise RuntimeError("injected job infrastructure initialization failure")
    window.regeneration_factory = broken
    QTest.mouseClick(window.render_refresh_button, Qt.LeftButton)
    assert "job infrastructure initialization failure" in window.final_render_status.text()
    assert not window.final_render_button.isEnabled()


def test_stale_timeline_disables_render_without_rebuilding_it(window, qt, project):
    section = window.session.active_script.section(project.timeline.clips[0].media.section_id)
    window.session.edit_section(section.section_id, text="A newer saved narrative revision.")
    window._refresh()
    wait(qt, lambda: not window._render_checks)
    assert not window.final_render_button.isEnabled()
    assert "invalid or stale" in window.final_render_status.text()
    assert window.timeline.services.current() == project.edit
    assert not window.test_process.calls


def test_reopen_restores_render_availability(window, project, qt):
    window.session.close()
    window.session = None
    window.load_project(project.root)
    wait(qt, lambda: not window._render_checks)
    assert window.final_render_button.isEnabled()
    assert window.timeline.edit == project.edit
    assert_preserved(window, project)


def test_automatic_completion_refreshes_render_availability(window, qt):
    window.timeline.edit = None
    window._timeline_changed(None)
    assert not window.final_render_button.isEnabled()
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    future.set_result(SimpleNamespace(timeline_clips=1, sections=1, scenes=1, duration="1s",
        skipped=1, plan_script_groups_completed=0, plan_script_groups_total=0))
    window.automatic_loop, window.automatic_task = loop, future
    window.automatic_busy = True
    window._automatic_tick()
    wait(qt, lambda: not window._render_checks)
    assert not window.automatic_busy
    assert window.final_render_button.isEnabled()


def test_render_failure_keeps_assets_and_exposes_error(window, qt, project):
    window.test_process.fail = "encode"
    QTest.mouseClick(window.final_render_button, Qt.LeftButton)
    assert window.regeneration.busy
    assert not window.final_render_button.isEnabled()
    wait(qt, lambda: not window.regeneration.busy and not window._render_checks)
    assert "failed" in window.final_render_result.text()
    assert "encode" in window.final_render_result.text()
    assert window.final_render_button.isEnabled()
    assert not window.export_panel.play_button.isEnabled()
    assert_preserved(window, project)


def test_staging_runs_without_sqlite_on_worker_and_cancel_keeps_qt_alive(window, qt, project, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    original = ProjectVideoRender._stage_clip
    threads = []
    def stage(self, *args):
        threads.append(threading.get_ident())
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test staging barrier timed out")
        return original(self, *args)
    monkeypatch.setattr(ProjectVideoRender, "_stage_clip", stage)
    beats = []
    heartbeat = QTimer(); heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
    heartbeat.start()
    try:
        QTest.mouseClick(window.final_render_button, Qt.LeftButton)
        wait(qt, entered.is_set)
        previous = len(beats)
        wait(qt, lambda: len(beats) >= previous + 3, timeout=2)
        assert window.regeneration.busy  # Staging barrier is still held by the worker.
        assert window.final_render_cancel.isEnabled()
        QTest.mouseClick(window.final_render_cancel, Qt.LeftButton)
    finally:
        release.set()
        heartbeat.stop()
    wait(qt, lambda: not window.regeneration.busy and not window._render_checks)
    assert threads and all(thread != owner for thread in threads)
    assert "canceled" in window.final_render_result.text().lower()
    assert not window.test_process.calls
    assert_preserved(window, project)


@pytest.mark.parametrize("project", ["fhd"], indirect=True)
def test_short_real_ffmpeg_button_publishes_and_reuses_verified_original(window, project, qt, monkeypatch, tmp_path):
    from app.desktop import export_panel
    from app.providers.ffmpeg_render import FFmpegRenderer
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Short synthetic final-render smoke requires local FFmpeg/ffprobe"
    final = window.regeneration.services.final_render
    final.render.renderer = FFmpegRenderer(ffmpeg, ffprobe)
    progress = []
    window.regeneration.progress_changed.connect(progress.append)
    QTest.mouseClick(window.final_render_button, Qt.LeftButton)
    wait(qt, lambda: not window.regeneration.busy and not window._render_checks, timeout=60)
    panel = window.export_panel
    assert panel.state.text() == "Final video ready", window.final_render_result.text()
    assert panel.play_button.isEnabled() and panel.copy_button.isEnabled()
    manifest = panel.manifest
    assert manifest.metadata["render"]["fully_decoded"] is True
    assert (manifest.metadata["render"]["width"], manifest.metadata["render"]["height"]) == (1920, 1080)
    assert any("encoding" in text or "staging" in text for text in progress)
    opened = []
    monkeypatch.setattr(export_panel.QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    QTest.mouseClick(panel.play_button, Qt.LeftButton)
    assert opened and Path(opened[0].toLocalFile()) == panel._path()
    destination = tmp_path / "exported.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(destination), "MP4"))
    QTest.mouseClick(panel.copy_button, Qt.LeftButton)
    assert sha256(destination.read_bytes()).hexdigest() == manifest.checksum
    # Delivery and publishing/Shorts resolve the same published original, not a proxy.
    assert panel.delivery.service.source("original").artifact_id == manifest.artifact_id
    publishing = window.publish_panel.service
    assert publishing.source("original")[0].artifact_id == manifest.artifact_id
    assert window.shorts_panel.service.publishing.source("original")[0].artifact_id == manifest.artifact_id
    quality_source = window.quality_panel.service.sources.capture(manifest.artifact_id)
    assert quality_source.source_kind == "original" and not quality_source.facts["stale"]
    assert_preserved(window, project)
    jobs_before = len(final.jobs.jobs())
    QTest.mouseClick(window.final_render_button, Qt.LeftButton)
    wait(qt, lambda: not window.regeneration.busy and not window._render_checks)
    assert "reused" in window.final_render_result.text()
    assert len(final.jobs.jobs()) == jobs_before
    window.session.close(); window.session = None
    window.load_project(project.root)
    wait(qt, lambda: not window._render_checks)
    assert window.final_render_button.isEnabled()
    assert window.export_panel.manifest.artifact_id == manifest.artifact_id
    assert "Verified final render available" in window.final_render_status.text()
