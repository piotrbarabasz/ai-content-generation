import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from app.desktop.__main__ import LocalProjects
from app.desktop.editor import ProjectEditor


class Provider:
    def generate_structured(self, _prompt, schema):
        standard = schema["properties"]["groups"]["maxItems"] == 10
        kinds = (["cold_open", "introduction", "chapter", "chapter", "chapter", "chapter", "chapter", "conclusion"]
                 if standard else ["hook", "body", "explanation", "payoff"])
        return {"working_title": "Plan title", "film_brief": "Plan brief", "visual_style": "Plan style",
                "groups": [{"kind": k, "title": k.title(), "purpose": "Purpose", "weight": 1,
                            "sections": [{"title": k.title(), "role": k, "purpose": "Purpose", "weight": 1}]}
                           for k in kinds]}


def test_plan_tab_profiles_rendering_and_context_application(tmp_path):
    qt = QApplication.instance() or QApplication([])
    editor = ProjectEditor(LocalProjects(), provider=Provider())
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
