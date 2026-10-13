import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication,QFileDialog
from PySide6.QtTest import QTest
from app.desktop.channel_library_panel import ChannelLibraryDialog
from app.desktop.branding_panel import BrandingPanel
from app.application.projects import ProjectSession
from app.application.video_finishing import VideoFinishingService
from app.storage.branding import ProjectBranding
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from tests.integration.test_reusable_branding_media import shared,source


def wait(widget):
    for _ in range(1000):
        QApplication.instance().processEvents()
        if not widget.busy:return
        QTest.qWait(10)
    pytest.fail("Channel operation did not complete")


def test_channel_manager_profiles_versions_and_local_import_are_responsive(shared,monkeypatch):
    qt=QApplication.instance() or QApplication([]);f=shared
    dialog=ChannelLibraryDialog(f.library,f.renderer)
    try:
        dialog.name.setText("Second channel");dialog.create()
        assert len(f.library.profiles())==2
        dialog.set_default();assert f.library.default().channel_name=="Second channel"
        monkeypatch.setattr(QFileDialog,"getOpenFileName",lambda *_:(str(f.root/"logo.png"),"PNG"))
        dialog.import_asset("logo");assert dialog.busy
        wait(dialog)
        assert dialog.profile.logo_version and "retained" in dialog.status.text()
        dialog.tagline.setText("Learn something new");dialog.save()
        assert dialog.profile.tagline=="Learn something new"
        dialog.create_clip("intro");wait(dialog)
        assert dialog.profile.intro_version
        seen=[]
        monkeypatch.setattr("app.desktop.channel_library_panel.QDesktopServices.openUrl",lambda url:seen.append(url.toLocalFile()) or True)
        dialog.preview("intro");assert seen
    finally:dialog.close()


def test_project_reusable_controls_apply_explicitly_and_keep_global_defaults(shared,tmp_path):
    qt=QApplication.instance() or QApplication([]);f=shared
    session=ProjectSession.create(tmp_path/"film",repository_factory=ProjectRepository,name="Film")
    try:
        store=LocalArtifactStore.for_project(session.repository)
        source=store.save_artifact("original.mp4",(f.root/"source.mp4").read_bytes(),{"artifact_type":"video_render","project_id":session.project.id,"render":f.evidence})
        service=VideoFinishingService(session,ProjectBranding(store,session.project.id),NS(selected=lambda **_:source),f.renderer,channel_library=f.library)
        panel=BrandingPanel();panel.bind(service)
        assert panel.mode.currentData()=="legacy"
        panel.mode.setCurrentIndex(panel.mode.findData("reusable"));panel.apply_profile()
        assert isinstance(panel.config(),type(f.library.selection(f.profile)))
        assert "missing" in panel.mapping.text()
        # The default after-cold-open choice stays blocked until explicitly changed.
        panel.placement.setCurrentIndex(panel.placement.findData("before"));panel.save()
        assert "Estimated branded delivery: 6.52s" in panel.details.text()
        panel.intro.setChecked(False)
        assert service.branding.current().intro is False and f.library.profile(f.profile.channel_id).intro is True
        original=service.branding.current()
        updated=f.library.edit(f.profile,tagline="Global update")
        f.library.set_default(updated.channel_id)
        panel.refresh();assert service.branding.current()==original
        count=len(service.branding.history("branding_settings"))
        restored=BrandingPanel();restored.bind(service)
        assert len(service.branding.history("branding_settings"))==count
        assert restored.mode.currentData()=="reusable" and restored.config()==original
        panel.close();restored.close()
    finally:session.close()


def test_application_library_opens_without_a_film_project_and_survives_switches(tmp_path):
    from app.desktop.editor import ProjectEditor
    from app.desktop.__main__ import LocalProjects
    from app.storage.channel_library import ChannelLibrary
    qt=QApplication.instance() or QApplication([])
    editor=ProjectEditor(LocalProjects())
    library=ChannelLibrary(tmp_path/"application-channels")
    editor.export_panel.channel_library=library
    try:
        assert editor.session is None
        editor.show_channel_library()
        dialog=editor.channel_library_dialog
        assert dialog is not None
        dialog.name.setText("Independent channel");dialog.create();dialog.set_default()
        editor.load_project(tmp_path/"A",create=True)
        editor.load_project(tmp_path/"B",create=True)
        assert library.default().channel_name=="Independent channel"
        assert dialog.library.root==library.root
        assert editor.minimumSizeHint().height()<=768
    finally:editor.close()


def test_missing_library_preserves_pinned_selection_and_allows_explicit_disable_or_legacy(shared,tmp_path):
    from app.storage.channel_library import ChannelLibrary
    from app.domain.branding import BrandingConfig
    qt=QApplication.instance() or QApplication([]);f=shared
    with ProjectSession.create(tmp_path/"missing-library-film",repository_factory=ProjectRepository,name="Missing library") as session:
        store=LocalArtifactStore.for_project(session.repository)
        source=store.save_artifact("source.mp4",(f.root/"source.mp4").read_bytes(),{"artifact_type":"video_render","project_id":session.project.id,"render":f.evidence})
        branding=ProjectBranding(store,session.project.id);config=f.library.selection(f.profile,placement="before");branding.save(config)
        service=VideoFinishingService(session,branding,NS(selected=lambda **_:source),f.renderer,channel_library=ChannelLibrary(tmp_path/"empty-library"))
        panel=BrandingPanel();panel.bind(service)
        assert panel.service==service and branding.current()==config and "missing" in panel.status.text().lower()
        panel.intro.setChecked(False);panel.outro.setChecked(False)
        assert service.prepare(panel.config())[0]==source
        panel.mode.setCurrentIndex(panel.mode.findData("legacy"))
        assert isinstance(branding.current(),BrandingConfig)
        panel.close()
