"""Pipeline diagnosis is a read-only inspection of saved state."""

from types import SimpleNamespace

from app.application.pipeline_diagnostics import PipelineDiagnostics
from app.domain.video_plan import VideoFormat


def test_diagnose_empty_script_reports_blocked_without_provider_services(caplog):
    caplog.set_level("INFO", logger="aics.pipeline")
    session = SimpleNamespace(active_script=SimpleNamespace(id="script-0", sections=()))
    report = PipelineDiagnostics().inspect_project(session, unsaved_draft=False)
    assert report.section_count == 0
    assert not report.ready_for_timeline
    assert "No saved script sections." in caplog.text
    assert '[AICS][PIPELINE][EXPORT][BLOCKED]' in caplog.text


def test_diagnose_reports_invalid_saved_timeline_as_export_blocker(caplog):
    caplog.set_level("INFO", logger="aics.pipeline")

    class InvalidTimeline:
        def current_candidate_diagnostics(self, **_):
            return ()

        def current(self):
            raise ValueError("Timeline content differs from its immutable identity.")

    session = SimpleNamespace(active_script=SimpleNamespace(id="script-1", sections=()))
    report = PipelineDiagnostics().inspect_project(session, timeline=InvalidTimeline())
    export = next(item for item in report.diagnostics if item.stage == "EXPORT")
    assert not report.export_ready
    assert export.reason == "Timeline content differs from its immutable identity."
    assert "[AICS][PIPELINE][EXPORT][BLOCKED]" in caplog.text


def test_plan_summary_is_optional_and_reports_group_progress_without_blocking_legacy():
    session = SimpleNamespace(active_script=SimpleNamespace(id="script-1", sections=()))
    legacy = PipelineDiagnostics().inspect_project(session)
    assert "Plan: LEGACY / NOT USED" in legacy.summary
    plan = SimpleNamespace(format=VideoFormat.STANDARD, target_duration_seconds=600,
                           groups=(1, 2, 3))
    plans = SimpleNamespace(selected=lambda: plan)
    script = SimpleNamespace(progress=lambda _plan: ("complete", "pending", "complete"))
    report = PipelineDiagnostics().inspect_project(session, video_plans=plans, plan_script=script)
    assert "Plan: STANDARD · 10:00 · READY" in report.summary
    assert "Script groups: 2/3" in report.summary
    assert not report.ready_for_timeline
