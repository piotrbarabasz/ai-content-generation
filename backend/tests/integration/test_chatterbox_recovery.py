"""Real queue/publication/chunk validation with fake worker fault injection."""
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.pipeline_diagnostics import PipelineDiagnostics
from app.application.automatic_workflow import AutomaticWorkflow, AutomaticWorkflowBlocked, AutomaticWorkflowConfig
from app.application.projects import ProjectSession
from app.application.section_audio import SectionAudioService
from app.desktop.audio_services import AudioServices
from app.desktop.pipeline_driver import DesktopPipelineDriver
from app.domain.narrative_segment import SectionRevision
from app.domain.base import new_id
from app.domain.video_plan import VideoFormat
from app.jobs.coordinator import JobCoordinator
from app.runtime.section_synthesis import generate
from app.runtime.supervisor import WorkerLaunch, WorkerRunResult, WorkerSupervisor
from app.storage.local_store import LocalArtifactStore
from app.storage.result_publication import ResultArtifactIndex
from app.jobs.repository import JobRepository
from app.storage.project_repository import ProjectRepository
from tests.integration.test_section_audio import setup as audio_project, Provider, Voices, Outputs
from tests.unit.test_t086 import _catalog


class InjectedWorker(WorkerSupervisor):
    def __init__(self, coordinator, production, root, failures):
        super().__init__(coordinator, WorkerLaunch(Path("unused")))
        self.production, self.root, self.failures = production, root, list(failures)
        self.calls = []
        self.providers = []

    async def run_next(self, owner="test"):
        claim = self.coordinator.claim_next(owner)
        job = self.coordinator.repository.get_job(claim.job_id)
        self.calls.append(job)
        failure = self.failures.pop(0) if self.failures else None
        provider = Provider(fail="Two." if failure else None)
        self.providers.append(provider)
        try:
            generate(job, provider, self.root)
        except ValueError:
            assert failure
        self.process = SimpleNamespace(pid=1000 + len(self.calls), returncode=0)
        if failure:
            outcome = self.coordinator.fail(claim, failure + ": injected failure")
        else:
            self.production.complete(claim)
            outcome = self.coordinator.repository.get_attempt(claim.id)
        return WorkerRunResult(outcome, self.process.pid, 0, "", cleanup_confirmed=True)


def adapter(values, failures=()):
    session, jobs, index, store, output, production = values
    coordinator = JobCoordinator(jobs)
    worker = InjectedWorker(coordinator, production, output.root, failures)
    services = AudioServices(catalog=_catalog(), production=production, coordinator=coordinator,
        supervisor=worker, index=index, store=store, preview_root=output.root,
        preview_builder=None, providers=("mock",), transient_retries=1)
    return services, worker, services.choices("pl")[0]


def test_transient_retry_reuses_exact_job_and_valid_chunks_without_duplicate_publication(audio_project):
    services, worker, choice = adapter(audio_project, ["WORKER_EXECUTION_TIMEOUT", None])
    section = audio_project[0].active_script.sections[1]
    assert asyncio.run(services.generate(section, choice)).startswith("completed")
    assert len(worker.calls) == 2 and worker.calls[0] == worker.calls[1]
    assert worker.providers[1].calls == ["Two."]
    assert len(audio_project[1].jobs()) == 1
    assert len(audio_project[1].attempts(worker.calls[0].id)) == 2
    assert len(audio_project[3].list_artifacts()) == 1
    assert not services.playback(section, "original", choice).stale


@pytest.mark.parametrize("code,count", [("WORKER_EXECUTION_TIMEOUT", 2), ("handler_error", 1)])
def test_exhaustion_and_permanent_failure_stop_without_publishing(audio_project, code, count):
    services, worker, choice = adapter(audio_project, [code, code, None])
    section = audio_project[0].active_script.sections[1]
    assert asyncio.run(services.generate(section, choice)).startswith("failed")
    assert len(worker.calls) == count
    assert not audio_project[2].selected()
    assert not audio_project[3].list_artifacts()


def test_cancellation_between_retry_and_launch_cancels_queued_replacement(audio_project):
    services, worker, choice = adapter(audio_project, ["WORKER_EXECUTION_TIMEOUT", None])
    original = worker.retry_transient
    def cancel_after_enqueue(result):
        retry = original(result)
        services.cancel()
        return retry
    worker.retry_transient = cancel_after_enqueue
    result = asyncio.run(services.generate(audio_project[0].active_script.sections[1], choice))
    assert result.startswith("canceled")
    assert len(worker.calls) == 1 and not audio_project[2].selected()
    assert audio_project[1].attempts(worker.calls[0].id)[-1].status == "canceled"
    # Only a subsequent explicit Run / Resume re-enables work after cancellation.
    services.canceled = False
    assert asyncio.run(services.generate(audio_project[0].active_script.sections[1], choice)).startswith("completed")
    assert worker.calls[0] == worker.calls[1]
    assert worker.providers[1].calls == ["Two."]


def test_restart_recovers_original_failed_workspace_and_verified_voice_count(audio_project):
    session, _, _, _, output, _ = audio_project
    services, worker, choice = adapter(audio_project, ["WORKER_EXECUTION_TIMEOUT"] * 2)
    section = session.active_script.sections[1]
    assert asyncio.run(services.generate(section, choice)).startswith("failed")
    original_job = worker.calls[0]
    project = session.repository.workspace
    session.close()
    with ProjectSession.open(project, repository_factory=ProjectRepository) as reopened:
        jobs = JobRepository(reopened.repository)
        index = ResultArtifactIndex(reopened.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        from app.application.result_publication import ResultPublicationService
        production = SectionAudioService(ResultPublicationService(index, store), jobs, Voices(), Outputs(output.root))
        restored, worker2, choice = adapter((reopened, jobs, index, store, output, production))
        assert asyncio.run(restored.generate(section, choice)).startswith("completed")
        assert worker2.calls == [original_job] and worker2.providers[0].calls == ["Two."]
        assert len(store.list_artifacts()) == 1
        report = PipelineDiagnostics().inspect_project(reopened, audio=SimpleNamespace(services=restored), audio_choice=choice)
        assert report.voice_ready == 1
    with ProjectSession.open(project, repository_factory=ProjectRepository) as reopened:
        jobs = JobRepository(reopened.repository)
        index = ResultArtifactIndex(reopened.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        production = SectionAudioService(None, jobs, Voices(), Outputs(output.root))
        restored, _, choice = adapter((reopened, jobs, index, store, output, production))
        report = PipelineDiagnostics().inspect_project(reopened, audio=SimpleNamespace(services=restored), audio_choice=choice)
        assert report.voice_ready == 1
        pytest.importorskip("PySide6")
        from PySide6.QtWidgets import QApplication
        from app.desktop.project_header import ProjectHeader
        application = QApplication.instance() or QApplication([])
        header = ProjectHeader()
        header.show_diagnostics(report, project_name="Recovery", language="pl")
        assert header.stage_labels["Voice"].text().endswith("1/3")
        reopened.edit_section(section.section_id, text="Changed narration.")
        report = PipelineDiagnostics().inspect_project(reopened, audio=SimpleNamespace(services=restored), audio_choice=choice)
        header.show_diagnostics(report, project_name="Recovery", language="pl")
        assert header.stage_labels["Voice"].text().endswith("0/3")
        header.close()


def test_automatic_resume_skips_five_verified_sections_and_refreshes_all_eighteen_before_duration_gate(audio_project):
    session, jobs, index, store, _, _ = audio_project
    current = session.active_script
    sections = tuple(SectionRevision.create(project_id=session.project.id, title=f"Narration {i}",
        role="body", text="One. Two. Three.") for i in range(18))
    session.save_script(replace(current, id=new_id("script_revision"), parent_revision_id=current.id, sections=sections),
                        expected_active_revision_id=current.id)
    services, worker, choice = adapter(audio_project)
    for section in sections[:5]:
        assert asyncio.run(services.generate(section, choice)).startswith("completed")
    retained = tuple(store.list_artifacts())
    old_bytes = {m.artifact_id: store.read_artifact(m.storage_key) for m in retained}
    worker.failures = ["WORKER_EXECUTION_TIMEOUT", None]
    counts = []
    audio = SimpleNamespace(services=services, select_section=lambda _: None)
    def refresh():
        report = PipelineDiagnostics().inspect_project(session, audio=audio, audio_choice=choice)
        counts.append(report.voice_ready)
    editor = SimpleNamespace(session=session, audio=audio, dirty=False, worker=None, diagnose_pipeline=refresh)
    config = AutomaticWorkflowConfig(choice, None, "landscape", "draft")
    driver = DesktopPipelineDriver(editor, config)
    driver.configure_motion = lambda *_: None
    plan = SimpleNamespace(id="test-plan", format=VideoFormat.STANDARD, target_duration_seconds=600, groups=tuple(range(9)))
    driver.selected_video_plan = lambda: plan
    driver.validate_plan_run = lambda *_: None
    driver.ensure_plan_visual_context = lambda *_: None
    driver.ensure_plan_script = lambda *_: None
    driver.validate_narration = lambda *_: None
    downstream = []
    driver.plan_state = lambda *_: downstream.append("images")
    with pytest.raises(AutomaticWorkflowBlocked) as blocked:
        asyncio.run(AutomaticWorkflow(driver).run(config))
    assert blocked.value.stage == "PLAN_DURATION"
    assert counts == list(range(6, 19))
    assert len(worker.calls) == 19  # eighteen jobs, one replacement attempt
    assert len(jobs.jobs()) == 18 and len(store.list_artifacts()) == 18
    assert not downstream
    assert all(store.read_artifact(m.storage_key) == old_bytes[m.artifact_id] for m in retained)
    assert session.active_script.sections == sections
