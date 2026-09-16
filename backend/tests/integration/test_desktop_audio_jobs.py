"""D021 adapter with real queue, section synthesis, publication and stale edits."""

import asyncio
import threading

import pytest

from app.desktop.audio_services import AudioServices
from app.jobs.coordinator import JobCoordinator
from app.runtime.section_synthesis import generate
from tests.integration.test_section_audio import setup, Provider
from tests.unit.test_t086 import _catalog


@pytest.mark.parametrize("edit_during_generation", [False, True])
def test_adapter_keeps_database_on_owner_and_stale_output_historical(setup, tmp_path, edit_during_generation):
    session, jobs, index, store, outputs, production = setup
    owner = threading.get_ident()
    coordinator = JobCoordinator(jobs)
    prepare = production.voices.prepare
    prepare_threads = []

    def prepared(*args):
        prepare_threads.append(threading.get_ident())
        return prepare(*args)

    production.voices.prepare = prepared
    section = session.active_script.sections[1]

    class Supervisor:
        async def run_next(self, owner_name):
            assert threading.get_ident() == owner
            claim = coordinator.claim_next(owner_name)
            job = jobs.get_job(claim.job_id)
            if edit_during_generation:
                session.edit_section(section.section_id, text="Edited while worker runs")
            await asyncio.to_thread(generate, job, Provider(), outputs.root)
            assert threading.get_ident() == owner
            production.complete(claim)

    adapter = AudioServices(catalog=_catalog(), production=production, coordinator=coordinator,
                            supervisor=Supervisor(), index=index, store=store, preview_root=tmp_path / "previews",
                            preview_builder=lambda config: Provider(), providers=("mock",))
    result = asyncio.run(adapter.generate(section, adapter.choices("pl")[0]))
    assert result == "completed"
    assert prepare_threads and all(t != owner for t in prepare_threads)
    assert len(store.list_artifacts()) == 1
    assert bool(index.selected()) == (not edit_during_generation)
    assert adapter.attempt is None
    if not edit_during_generation:
        assert not adapter.playback(section, "original").stale
        new = session.edit_section(section.section_id, text="Later edit").section(section.section_id)
        assert adapter.playback(new, "original").stale


def test_failure_before_worker_claim_cancels_queued_request(setup, tmp_path):
    session, jobs, index, store, outputs, production = setup
    class Supervisor:
        async def run_next(self, owner):
            raise RuntimeError("Worker unavailable")
    adapter = AudioServices(catalog=_catalog(), production=production, coordinator=JobCoordinator(jobs),
                            supervisor=Supervisor(), index=index, store=store, preview_root=tmp_path,
                            preview_builder=lambda config: Provider(), providers=("mock",))
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(adapter.generate(session.active_script.sections[0], adapter.choices("pl")[0]))
    assert jobs.attempts(jobs.jobs()[0].id)[0].status == "canceled"
    assert adapter.attempt is None and not index.selected()


def test_cancel_during_preparation_leaves_no_job(setup, tmp_path):
    session, jobs, index, store, outputs, production = setup
    adapter = AudioServices(catalog=_catalog(), production=production, coordinator=JobCoordinator(jobs),
                            supervisor=None, index=index, store=store, preview_root=tmp_path,
                            preview_builder=lambda config: Provider(), providers=("mock",))
    adapter.cancel()
    result = asyncio.run(adapter.generate(session.active_script.sections[0], adapter.choices("pl")[0]))
    assert result == "Canceled before enqueue" and not jobs.jobs()
