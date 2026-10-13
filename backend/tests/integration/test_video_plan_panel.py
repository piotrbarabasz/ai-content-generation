import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from app.desktop.__main__ import LocalProjects
from app.desktop.editor import ProjectEditor


class Provider:
    def __init__(self): self.group_calls = []
    def generate_structured(self, _prompt, schema):
        if schema["$id"] == "urn:aics:planned-script-group:v1":
            import json
            payload = json.loads(_prompt)
            self.group_calls.append(payload["group"]["id"])
            return {"sections": [{"planned_section_id": item["id"],
                                   "text": " ".join(["word"] * item["target_word_count"])}
                                  for item in payload["group"]["sections"]]}
        assert schema["$id"] == "urn:aics:video-plan:v1"
        standard = schema["properties"]["groups"]["maxItems"] == 10
        kinds = (["cold_open", "introduction", "chapter", "chapter", "chapter", "chapter", "chapter", "conclusion"]
                 if standard else ["hook", "body", "explanation", "payoff"])
        from backend.tests.unit.test_video_planning import narrative_metadata
        return {**({"narrative": narrative_metadata()} if standard else {}), "working_title": "Plan title", "film_brief": "Plan brief", "visual_style": "Plan style",
                "groups": [{"kind": k, "title": k.title(), "purpose": "Purpose", "weight": 1,
                            "sections": [{"title": k.title(), "role": k, "purpose": "Purpose", "weight": 1}]}
                           for k in kinds]}


def test_plan_tab_profiles_rendering_and_context_application(tmp_path):
    qt = QApplication.instance() or QApplication([])
    provider = Provider()
    editor = ProjectEditor(LocalProjects(), provider=provider)
    editor.load_project(tmp_path / "project", create=True)
    try:
        panel = editor.video_plan
        assert editor.tabs.tabText(0) == "Plan"
        assert panel.duration.minimum() == 30 and panel.duration.maximum() == 60 and panel.duration.value() == 45
        panel.topic.setPlainText("A topic")
        panel.generate()
        assert panel.plan is not None and "Hook:" in panel.outline.toPlainText()
        panel.format.setCurrentIndex(panel.format.findData("standard"))
        assert panel.duration.minimum() == 540 and panel.duration.maximum() == 660 and panel.duration.value() == 600
        panel.generate()
        assert panel.plan is not None and len(panel.plan.groups) == 8
        assert "Chapter:" in panel.outline.toPlainText()
        current = panel.context_service.prompts
        assert current.current_context("film_brief") is None
        panel.apply_visual_context()
        assert current.current_context("film_brief").text == "Plan brief"
        assert current.current_context("visual_style").text == "Plan style"
        # Explicit repeated apply makes child revisions in existing context history.
        panel.apply_visual_context()
        assert current.current_context("film_brief").parent_revision_id is not None
        assert editor.session.active_script.sections == ()
    finally:
        editor.close()


def test_plan_panel_resumes_group_script_and_explicit_replace_uses_cached_groups(tmp_path):
    qt = QApplication.instance() or QApplication([])
    provider = Provider()
    editor = ProjectEditor(LocalProjects(), provider=provider)
    editor.load_project(tmp_path / "project", create=True)
    try:
        panel = editor.video_plan
        panel.topic.setPlainText("A topic")
        panel.generate()
        plan = panel.plan
        panel.resume_script_generation()
        for _ in range(500):
            qt.processEvents()
            if not panel.busy:
                break
            QTest.qWait(10)
        assert not panel.busy
        assert panel.script_progress.text() == f"Script groups: {len(plan.groups)}/{len(plan.groups)} complete"
        assert len(provider.group_calls) == len(plan.groups)
        original = editor.session.active_script
        assert [section.section_id for section in original.sections] == [
            panel.script_service.section_id(plan.project_id, section.id)
            for group in plan.groups for section in group.sections]
        panel.resume_script_generation()
        assert not panel.busy and len(provider.group_calls) == len(plan.groups)
        panel.start_new_script_confirmed()
        for _ in range(500):
            qt.processEvents()
            if not panel.busy:
                break
            QTest.qWait(10)
        assert not panel.busy
        assert editor.session.active_script.script_id != original.script_id
        assert editor.session.repository.get_script(original.id) == original
        assert len(provider.group_calls) == len(plan.groups)
    finally:
        editor.close()


def wait_for_narrative(qt, panel):
    for _ in range(800):
        qt.processEvents()
        if not panel.busy:
            break
        QTest.qWait(10)
    assert not panel.busy


def test_narrative_panel_preview_review_regenerate_approve_and_no_open_paid_calls(tmp_path):
    qt = QApplication.instance() or QApplication([])
    class EditorialProvider(Provider):
        def __init__(self):
            super().__init__()
            self.editorial_calls = 0
        def generate_structured(self, prompt, schema):
            if schema["$id"] == "urn:aics:narrative-review:v1":
                self.editorial_calls += 1
                import json
                source = json.loads(prompt)["sources"][0]
                return {"findings": [{"issue_type": "repetition", "severity": "REVIEW",
                    "group_id": source["group_id"], "section_id": source["section_id"],
                    "revision_id": source["revision_id"], "supporting_excerpt": "word",
                    "explanation": "Consider a more engaging opening.", "suggested_correction": "Edit the hook."}]}
            return super().generate_structured(prompt, schema)
    provider = EditorialProvider()
    editor = ProjectEditor(LocalProjects(), provider=provider)
    editor.load_project(tmp_path / "project", create=True)
    try:
        panel = editor.video_plan
        assert provider.group_calls == [] and provider.editorial_calls == 0
        panel.format.setCurrentIndex(panel.format.findData("standard"))
        panel.topic.setPlainText("Internet outage")
        panel.generate()
        assert "Narrative promise" in panel.details.text()
        panel.resume_script_generation()
        wait_for_narrative(qt, panel)
        before = editor.session.active_script
        assert "word" in panel.narrative_preview.toPlainText()
        panel.review_narrative()
        assert provider.editorial_calls == 0
        panel.review_editorial()
        wait_for_narrative(qt, panel)
        assert provider.editorial_calls == 1
        assert "repetition" in panel.continuity_findings.toPlainText()
        assert editor.session.active_script == before
        panel.approve_selected_group()
        assert "APPROVED" in panel.outline.toPlainText()
        panel.group_selection.setCurrentIndex(panel.group_selection.count() - 1)
        panel.regenerate_selected_group()
        wait_for_narrative(qt, panel)
        assert editor.session.active_script.sections[:-1] == before.sections[:-1]
        assert editor.session.active_script.sections[-1].id != before.sections[-1].id
        assert "STALE" in panel.continuity_findings.toPlainText()
        assert len(provider.group_calls) == len(panel.plan.groups) + 1
        # Editing in the existing Script editor refreshes the narrative overview.
        editor.session.edit_section(before.sections[0].section_id, text="An edited hook.")
        editor._refresh()
        assert "CONTEXT STALE" in panel.outline.toPlainText()
        paid_calls = provider.editorial_calls
    finally:
        editor.close()
    reopened = ProjectEditor(LocalProjects(), provider=provider)
    reopened.load_project(tmp_path / "project")
    try:
        assert provider.editorial_calls == paid_calls
        assert "STALE" in reopened.video_plan.continuity_findings.toPlainText()
        reopened.video_plan.resume_script_generation()
        wait_for_narrative(qt, reopened.video_plan)
        assert len(provider.group_calls) == 9
    finally:
        reopened.close()
