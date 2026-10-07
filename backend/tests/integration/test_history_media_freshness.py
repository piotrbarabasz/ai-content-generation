"""D033 restores audio/timeline choices without weakening current-input gates."""

from app.application.projects import ProjectSession
from app.desktop.timeline_composition import compose_timeline
from app.storage.history import compose_history
from app.storage.project_repository import ProjectRepository
from app.storage.section_tempo import SectionTempoArtifacts
from tests.integration.test_timeline import setup, prepared
from tests.integration.test_section_audio import enqueue, Provider
from app.runtime.section_synthesis import generate
from tests.integration.test_project_history import choice


def test_section_restore_invalidates_new_audio_and_can_reselect_old_without_generation(setup):
    session, jobs, index, store, output, service = setup
    original = session.active_script
    b1 = original.sections[1]
    provider = Provider()
    for section in (original.sections[0], b1):
        job, owned = enqueue(setup, section=section)
        generate(job, provider, output.root)
        service.complete(owned)
    a_key = "section:" + original.sections[0].section_id + ":audio:raw"
    b_key = "section:" + b1.section_id + ":audio:raw"
    old_audio = index.selected()[b_key]
    other_audio = index.selected()[a_key]
    b2 = session.edit_section(b1.section_id, text="Changed narration.").sections[1]
    job, owned = enqueue(setup, section=b2)
    generate(job, provider, output.root)
    newer = service.complete(owned)
    count = len(provider.calls)
    history = compose_history(session)
    history.restore(choice(history, "section", b1.id))
    assert SectionTempoArtifacts(index, store).selected(b1, "original") is None
    assert "stale" in choice(history, "result", newer.artifact_id).freshness
    assert "current" in choice(history, "result", old_audio).freshness
    history.restore(choice(history, "result", old_audio))
    assert SectionTempoArtifacts(index, store).selected(b1, "original").artifact_id == old_audio
    assert index.selected()[a_key] == other_audio
    assert len(provider.calls) == count  # Exactly zero synthesis calls while restoring.
    assert session.repository.get_section(b2.id) == b2
    workspace = session.repository.workspace
    session.close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        restored = compose_history(reopened)
        assert choice(restored, "result", old_audio).selected
        assert not choice(restored, "result", newer.artifact_id).selected


def test_timeline_restore_appends_selection_preserves_newer_and_restarts(setup, prepared):
    session = setup[0]
    timelines = compose_timeline(session)
    first = timelines.append(prepared[-1][0], expected=None)
    second = timelines.append(prepared[-1][1], expected=first.id)
    history = compose_history(session)
    history.restore(choice(history, "timeline", first.id))
    restored = timelines.current()
    assert restored.timeline == first.timeline and restored.id != first.id
    assert timelines.history.history() == (first, second, restored)
    workspace = session.repository.workspace
    session.close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        assert compose_timeline(reopened).current() == restored
