"""D033 offline restoration through the same service used by the desktop."""

import os

import pytest

from app.application.history import HistoryService
from app.application.projects import ProjectSession
from app.storage.history import ProjectHistory, compose_history
from app.storage.project_repository import ProjectRepository
from tests.unit.test_project_repository import seed
from tests.integration.test_visual_prompts import project as prompt_project, args
from tests.integration.test_image_intake import project as image_project, selected
from tests.integration.test_result_publication import setup, enqueue, claim, publish, composition


def choice(service, kind, identity):
    return next(c for c in service.choices() if c.kind == kind and c.identity == identity)


def test_section_restore_retains_newer_revision_and_conflicts_then_restarts(tmp_path):
    with ProjectSession.create(tmp_path, name="History", repository_factory=ProjectRepository) as session:
        initial = seed(session)
        b1 = initial.sections[1]
        changed = session.edit_section(b1.section_id, text="B2")
        service = compose_history(session)
        selected = choice(service, "section", b1.id)
        assert selected.created == selected.source == "Not recorded"
        service.restore(selected)
        restored = session.active_script
        assert restored.sections == initial.sections
        assert restored.id not in (initial.id, changed.id)
        assert session.repository.get_script(changed.id) == changed
        assert session.repository.section_history(b1.section_id) == (b1, changed.sections[1])
        with pytest.raises(ValueError, match="Script changed"):
            service.restore(selected)
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert reopened.active_script == restored
        assert reopened.repository.get_script(changed.id) == changed


def test_restore_prompt_uses_selection_chain_without_provider_calls(prompt_project):
    session, store, adapter, prompts, provider, *_ = prompt_project
    first = prompts.generate(*args(prompt_project))
    initial = prompts.select(first.id, expected_selection_id=None)
    second = prompts.edit_manual(first.id, "Newer prompt")
    prompts.select(second.id, expected_selection_id=initial.id)
    calls = len(provider.calls)
    history = compose_history(session)
    selected = choice(history, "prompt", first.id)
    history.restore(selected)
    assert adapter.selected(first.inputs.scene_id).revision_id == first.id
    assert adapter.revision(second.id) == second
    assert len(provider.calls) == calls  # Restoration invokes zero providers.
    assert len(adapter.selection_history(first.inputs.scene_id)) == 3
    with pytest.raises(ValueError, match="Selection changed"):
        history.restore(selected)
    workspace = session.repository.workspace
    session.close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        restored = ProjectHistory(reopened.repository)
        assert restored.prompts.selected(first.inputs.scene_id).revision_id == first.id


def test_restore_image_preserves_bytes_and_newer_variant_and_restart(image_project):
    session, store, adapter, intake, accepted, source = image_project
    first, selection = selected(image_project)
    second, _ = selected(image_project, expected=selection.id)
    before = {m.artifact_id: store.read_artifact(m.storage_key) for m in store.list_artifacts()}
    history = compose_history(session)
    history.restore(choice(history, "image", first.artifact_id))
    assert adapter.selected(first.scene_id).artifact_id == first.artifact_id
    assert adapter.image(second.artifact_id) == second
    assert all(store.read_artifact(m.storage_key) == before[m.artifact_id]
               for m in store.list_artifacts() if m.artifact_id in before)
    workspace = session.repository.workspace
    session.close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        assert ProjectHistory(reopened.repository).images.selected(first.scene_id).artifact_id == first.artifact_id


def test_result_restore_rejects_stale_selection_and_supersedes_inflight_job(setup, tmp_path):
    session, jobs, index, store, service = setup
    enqueue(setup)
    first = publish(setup, claim(setup))
    enqueue(setup)
    second = publish(setup, claim(setup), b"newer")
    enqueue(setup)
    running = claim(setup)
    index.restore("B:raw", first.artifact_id, expected_artifact_id=second.artifact_id)
    late = publish(setup, running, b"late")
    assert not late.selected_at_publication and late.reason == "superseded_generation"
    assert index.selected()["B:raw"] == first.artifact_id
    assert len(index.history("B:raw")) == 3
    with pytest.raises(ValueError, match="selection changed"):
        index.restore("B:raw", second.artifact_id, expected_artifact_id=second.artifact_id)
    session.close()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert composition(reopened)[1].selected()["B:raw"] == first.artifact_id


def test_history_dialog_restores_real_section(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.desktop.history_dialog import HistoryDialog
    qt = QApplication.instance() or QApplication([])
    with ProjectSession.create(tmp_path, name="UI", repository_factory=ProjectRepository) as session:
        initial = seed(session)
        session.edit_section(initial.sections[1].section_id, text="B2")
        dialog = HistoryDialog(compose_history(session))
        for row in range(dialog.items.count()):
            from PySide6.QtCore import Qt
            if dialog.items.item(row).data(Qt.UserRole).identity == initial.sections[1].id:
                dialog.items.setCurrentRow(row)
                break
        dialog.restore_button.click()
        assert session.active_script.sections == initial.sections
        assert "Restored" in dialog.status.text()
        dialog.close()
