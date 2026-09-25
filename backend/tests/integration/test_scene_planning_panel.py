"""Scene-plan proposal review stays behind a presentation service boundary."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.desktop.scene_planning_panel import ScenePlanningPanel
from app.desktop.scene_services import PlanSceneView, ScenePlanView


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


class Services:
    def __init__(self):
        self.calls = []
        self.view = ScenePlanView("none", None, None, ())

    def plan_state(self, section):
        self.calls.append(("read", section.id))
        return self.view

    def suggest_scene_plan(self, section):
        self.calls.append(("suggest", section.id))
        self.view = ScenePlanView(
            "proposal", "plan-1", None,
            (PlanSceneView(1, "Source sentence.", "A useful visual", "Timing unavailable"),),
        )
        return self.view

    def accept_scene_plan(self, section, plan_id):
        self.calls.append(("accept", section.id, plan_id))
        self.view = ScenePlanView("accepted", plan_id, "acceptance-1", self.view.scenes)
        return self.view

    def rebuild_scene_timing(self, section):
        self.calls.append(("retime", section.id))
        self.view = ScenePlanView(
            "accepted", self.view.plan_id, self.view.acceptance_id,
            (PlanSceneView(1, "Source sentence.", "A useful visual", "0.00–2.00 s"),),
        )
        return self.view


def test_no_proposal_generate_review_accept_and_retime(qt):
    services = Services()
    section = SimpleNamespace(id="revision-1", title="Opening")
    panel = ScenePlanningPanel()
    panel.bind(services)
    panel.select_section(section)
    try:
        assert "No proposal" in panel.state.text()
        assert panel.buttons["Generate scene plan"].isEnabled()
        assert not panel.buttons["Accept scene plan"].isEnabled()

        QTest.mouseClick(panel.buttons["Generate scene plan"], Qt.LeftButton)
        assert services.calls[-2][0] == "suggest"
        assert "awaiting review" in panel.state.text()
        assert "Source sentence" in panel.detail.text()
        assert not any(call[0] == "accept" for call in services.calls)

        QTest.mouseClick(panel.buttons["Accept scene plan"], Qt.LeftButton)
        assert ("accept", "revision-1", "plan-1") in services.calls
        assert "accepted scene plan" in panel.state.text()

        QTest.mouseClick(panel.buttons["Rebuild timing"], Qt.LeftButton)
        assert services.calls[-2][0] == "retime"
        assert "0.00–2.00 s" in panel.scenes.item(0).text()
    finally:
        panel.close()


def test_regenerate_keeps_review_explicit(qt):
    services = Services()
    services.view = ScenePlanView(
        "accepted", "plan-0", "acceptance-0",
        (PlanSceneView(1, "Old", "Old visual", "Timing unavailable"),),
    )
    panel = ScenePlanningPanel()
    panel.bind(services)
    panel.select_section(SimpleNamespace(id="revision-1", title="Opening"))
    try:
        QTest.mouseClick(panel.buttons["Regenerate scene plan"], Qt.LeftButton)
        assert panel.view.state == "proposal"
        assert not any(call[0] == "accept" for call in services.calls)
        assert "previous history was retained" in panel.status.text()
    finally:
        panel.close()
