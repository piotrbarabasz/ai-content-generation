"""The five-tab coordinator shell follows retained diagnostics and one selection."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.desktop.__main__ import LocalProjects
from app.desktop.editor import ProjectEditor
from app.desktop.project_outline import ProjectOutline
from app.desktop.project_header import ProjectHeader
from app.domain.plan_script import planned_section_identity
from app.domain.video_plan import PlannedSection, VideoFormat, VideoPlanGroup, VideoPlanRevision


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def _plan(fmt):
    social = fmt is VideoFormat.SOCIAL
    count = 5 if social else 9
    group_names = (["Opening hook", "What contrails are", "How they form", "Interesting fact", "Close"]
                   if social else ["Cold Open", "Introduction", "Chapter 1", "Chapter 2", "Chapter 3",
                                  "Chapter 4", "Chapter 5", "Chapter 6", "Conclusion"])
    durations = [9] * 5 if social else [40, 40, 80, 80, 80, 80, 80, 80, 40]
    groups = []
    for i, (name, duration) in enumerate(zip(group_names, durations)):
        planned = PlannedSection(f"section-{i}", name, "hook" if i == 0 else "body", name,
                                 duration, max(1, duration * 2), 1)
        kind = ("hook" if i == 0 else "body") if social else (
            "cold_open" if i == 0 else "chapter" if 2 <= i <= 7 else name.lower().replace(" ", "_"))
        groups.append(VideoPlanGroup(f"group-{i}", kind,
            name, name, duration, (planned,)))
    return VideoPlanRevision("plan-1", "project-1", "en", fmt,
        45 if social else 600, "Topic", "Working title", "Brief", "Style", tuple(groups))


def test_social_outline_is_compact_and_standard_outline_has_collapsible_chapters(qt):
    outline = ProjectOutline()
    social = _plan(VideoFormat.SOCIAL)
    outline.populate((), social)
    assert outline.topLevelItemCount() == 5
    assert all(outline.topLevelItem(i).childCount() == 0 for i in range(5))
    standard = _plan(VideoFormat.STANDARD)
    outline.populate((), standard)
    assert outline.topLevelItemCount() == 9
    chapters = [outline.topLevelItem(i) for i in range(2, 8)]
    assert all(item.childCount() == 1 for item in chapters)
    chapters[0].setExpanded(False)
    assert not chapters[0].isExpanded()
    section_id = planned_section_identity(standard.project_id, standard.groups[2].sections[0].id)
    assert outline.select_section(section_id)
    assert outline.currentItem().data(0, outline.SECTION_ROLE) == section_id
    outline.close()


def test_social_header_shows_selected_format_target_and_measured_duration(qt):
    header = ProjectHeader()
    report = type("Report", (), {
        "narration_duration_review": "OK · measured 43.0s",
        "script_group_progress": "5/5", "section_count": 5,
        "voice_ready": 5, "visuals_ready": 6, "visual_count": 7,
        "timeline_expected": 0, "timeline_rejected": 0,
        "final_render_ready": False, "export_ready": False,
        "plan_summary": "SOCIAL · 0:45 · READY",
    })()
    header.show_diagnostics(report, project_name="Social test", language="en", plan=_plan(VideoFormat.SOCIAL))
    assert "SOCIAL" in header.project_summary.text()
    assert "Target 0:45" in header.project_summary.text()
    assert "Actual 43.0s" in header.project_summary.text()
    assert "5/5" in header.stage_labels["Script"].text()
    assert "6/7" in header.stage_labels["Visuals"].text()
    header.close()


def test_outline_selection_syncs_script_audio_and_internal_combo(qt, tmp_path):
    editor = ProjectEditor(LocalProjects())
    editor.load_project(tmp_path / "project", create=True)
    try:
        for title, text in (("Opening", "First"), ("Body", "Second")):
            editor.new_section()
            editor.title.setText(title)
            editor.text.setPlainText(text)
            editor.save()
        chosen = editor.snapshot.sections[0]
        first_item = editor.outline.topLevelItem(0)
        QTest.mouseClick(editor.outline.viewport(), Qt.LeftButton,
                         pos=editor.outline.visualItemRect(first_item).center())
        qt.processEvents()
        assert editor.selected_id == chosen.section_id
        assert editor.title.text() == "Opening"
        assert editor.audio.section == chosen
        assert editor.section_choice.currentData() == chosen.section_id
        assert editor.tabs.currentIndex() == 1
        editor.section_choice.setCurrentIndex(1)
        assert editor.selected_id == editor.section_choice.currentData()
    finally:
        editor.dirty = False
        editor.close()


def test_busy_controls_disable_navigational_surfaces_and_project_switch_resets_state(qt, tmp_path):
    editor = ProjectEditor(LocalProjects())
    editor.load_project(tmp_path / "first", create=True)
    try:
        editor.set_plan_script_busy(True)
        assert not editor.outline.isEnabled()
        assert all(not editor.tabs.isTabEnabled(index) for index in range(1, editor.tabs.count()))
        editor.set_plan_script_busy(False)
        editor.automatic_busy = True
        editor._update_workflow_controls()
        assert not editor.workflow_mode.isEnabled()
        assert not editor.diagnose_button.isEnabled()
        assert not editor.auto_run_button.isEnabled()
        editor.automatic_busy = False
        editor._update_workflow_controls()
        editor.new_section()
        editor.title.setText("Draft")
        editor.text.setPlainText("Text")
        editor.save()
        editor.load_project(tmp_path / "second", create=True)
        assert editor.selected_id is None
        assert editor.outline.topLevelItemCount() == 0
        assert editor.storyboard.scene_count == 0
        assert editor.audio.section is None
        assert [editor.tabs.tabText(i) for i in range(editor.tabs.count())] == [
            "Plan", "Script", "Storyboard", "Timeline", "Export"]
        assert editor.minimumSizeHint().height() <= 768
        assert editor.storyboard.scroll.widgetResizable()
    finally:
        editor.dirty = False
        editor.close()


def test_stage_summary_comes_from_pipeline_diagnostics(qt, tmp_path):
    editor = ProjectEditor(LocalProjects())
    editor.load_project(tmp_path / "project", create=True)
    try:
        report = editor.diagnose_pipeline()
        assert "BLOCKED" in editor.header.stage_labels["Script"].text() or "Pending" in editor.header.stage_labels["Script"].text()
        assert "Not used" in editor.header.stage_labels["Plan"].text()
        assert "Pending" in editor.header.stage_labels["Export"].text()
        assert report.final_render_ready is False
    finally:
        editor.close()


def test_storyboard_scene_selection_is_forwarded_to_existing_scene_inspector(qt, tmp_path):
    editor = ProjectEditor(LocalProjects())
    editor.load_project(tmp_path / "project", create=True)
    try:
        from types import SimpleNamespace
        view = SimpleNamespace(id="scene-1", index=1, time_label="0.0–6.0 s", text="Excerpt",
            image_id=None, images=(), prompt_id=None)
        section = SimpleNamespace(section_id="section-1", title="Opening")
        editor.storyboard.set_scene_views(section, (view,))
        selected = []
        editor.visuals.select_scene_id = selected.append
        card = editor.storyboard._cards["scene-1"][0]
        QTest.mouseClick(card, Qt.LeftButton)
        assert selected == ["scene-1"]
        editor.visuals.scene_selected.emit("scene-1")
        assert card.property("selected") is True
    finally:
        editor.close()
