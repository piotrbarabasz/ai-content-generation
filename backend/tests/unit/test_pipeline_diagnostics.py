"""Pipeline diagnosis is a read-only inspection of saved state."""

from types import SimpleNamespace

from app.application.pipeline_diagnostics import PipelineDiagnostics


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
