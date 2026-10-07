"""Render history uses existing validated-result selection, never another render."""

import asyncio

from app.application.projects import ProjectSession
from app.storage.history import compose_history
from app.storage.project_repository import ProjectRepository
from tests.integration.test_video_render import setup, prepared, render, claim
from tests.integration.test_project_history import choice


def test_render_restore_preserves_new_result_and_runs_no_renderer(render, setup):
    first = asyncio.run(render.service.run(claim(render)))
    second = asyncio.run(render.service.run(claim(render)))
    calls = len(render.process.calls)
    history = compose_history(setup[0])
    history.restore(choice(history, "result", first.artifact_id))
    assert render.adapter.selected().artifact_id == first.artifact_id
    assert len(render.process.calls) == calls
    assert len(render.index.history("project:video_render")) == 2
    with render.store.open_artifact_id(second.artifact_id) as source:
        assert source.read() == b"fake process fixture"
    workspace = setup[0].repository.workspace
    setup[0].close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        restored = compose_history(reopened)
        assert choice(restored, "result", first.artifact_id).selected
        assert not choice(restored, "result", second.artifact_id).selected
