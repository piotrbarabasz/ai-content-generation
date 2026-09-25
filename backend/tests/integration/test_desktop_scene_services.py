"""D022 real project adapters: isolation, failures and persisted selection round-trip."""

from dataclasses import replace
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.desktop.scene_composition import compose_scenes
from app.desktop.scene_panel import ScenePanel
from app.providers.mock_image import MockImageProvider
from app.storage.project_repository import ProjectRepository
from app.tts.scene_sources import sentence_sources


class PromptProvider:
    def __init__(self):
        self.calls = []
        self.error = None

    def generate_structured(self, prompt, schema):
        self.calls.append((prompt, schema))
        if self.error:
            raise RuntimeError(self.error)
        return {"prompt": "Generated visual"}


class ImageProvider(MockImageProvider):
    def __init__(self):
        self.calls = []
        self.error = None

    def generate(self, request):
        self.calls.append(request)
        if self.error:
            raise RuntimeError(self.error)
        return super().generate(request)


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def setup_project(path):
    session = ProjectSession.create(path, name="Scenes", repository_factory=ProjectRepository)
    section = ScriptGenerationService(session).append_text(
        "First scene.\n\nSecond scene.", title="Story",
        expected_active_revision_id=session.active_script.id).sections[0]
    prompt_provider, image_provider = PromptProvider(), ImageProvider()
    services = compose_scenes(session, prompt_provider=prompt_provider,
                              prompt_identity={"provider": "fixture", "model": "v1"},
                              image_provider=image_provider)
    planning = ScenePlanningService(services.plans, sentence_sources)
    accepted = planning.accept(planning.suggest(section).id, reviewer_id="editor")
    brief = services.prompts.pin_context("film_brief", "Short factual film.")
    style = services.prompts.pin_context("visual_style", "Natural light.")
    for index, scene in enumerate(accepted.plan.scenes, 1):
        prompt = services.prompts.create_manual(accepted.id, scene.id, brief.id, style.id, f"Manual {index}")
        services.prompts.select(prompt.id, expected_selection_id=None)
    return session, section, services, prompt_provider, image_provider


def test_scene_local_prompt_and_image_changes_preserve_other_scene_and_audio(tmp_path):
    session, section, services, prompt_provider, image_provider = setup_project(tmp_path / "project")
    try:
        first, second = services.scenes(section)
        audio = services.store.save_artifact("retained.wav", b"retained audio")
        second_prompt = services.prompts.prompts.selected(second.id)
        changed = services.save_prompt(first.id, "Edited first")
        assert changed.prompt == "Edited first"
        assert services.prompts.prompts.selected(second.id) == second_prompt
        assert services.store.read_artifact(audio.storage_key) == b"retained audio"
        assert prompt_provider.calls == [] and image_provider.calls == []

        restored_prompt = services.select_prompt(first.id, first.prompt_id)
        assert (restored_prompt.prompt_id, restored_prompt.prompt) == (first.prompt_id, first.prompt)

        generated = services.generate_image(first.id, width=32, height=24, seed=1)
        second_image = services.generate_image(first.id, width=32, height=24, seed=2)
        assert generated.image_id != second_image.image_id and len(image_provider.calls) == 2
        restored_image = services.select_image(first.id, generated.image_id)
        assert restored_image.image_id == generated.image_id
        assert services.scene(second.id).image_id is None
        assert services.store.read_artifact(audio.storage_key) == b"retained audio"
    finally:
        session.close()


def test_failed_prompt_and_image_generation_preserve_manual_text_and_selection(tmp_path):
    session, section, services, prompt_provider, image_provider = setup_project(tmp_path / "project")
    try:
        first = services.scenes(section)[0]
        prompt_provider.error = "prompt unavailable"
        with pytest.raises(RuntimeError, match="prompt unavailable"):
            services.regenerate_prompt(first.id)
        after_prompt = services.scene(first.id)
        assert (after_prompt.prompt, after_prompt.prompt_id) == (first.prompt, first.prompt_id)

        selected = services.generate_image(first.id, width=32, height=24, seed=1)
        image_provider.error = "image unavailable"
        with pytest.raises(RuntimeError, match="image unavailable"):
            services.generate_image(first.id, width=32, height=24, seed=2)
        after_image = services.scene(first.id)
        assert (after_image.image_id, after_image.image_selection_id) == (
            selected.image_id, selected.image_selection_id)
    finally:
        session.close()


def test_selected_prompt_and_image_survive_reopen_in_panel(qt, tmp_path):
    root = tmp_path / "project"
    session, section, services, _, _ = setup_project(root)
    first = services.scenes(section)[0]
    selected = services.generate_image(first.id, width=32, height=24, seed=9)
    expected = (selected.prompt_id, selected.image_id)
    session.close()

    reopened = ProjectSession.open(root, repository_factory=ProjectRepository)
    panel = ScenePanel()
    try:
        restored = compose_scenes(reopened, prompt_provider=PromptProvider(),
                                  prompt_identity={"provider": "fixture", "model": "v1"},
                                  image_provider=ImageProvider())
        panel.bind(restored)
        panel.select_section(reopened.active_script.sections[0])
        assert panel.current is not None
        assert (panel.current.prompt_id, panel.current.image_id) == expected
        assert panel.prompt_variants.currentData() == expected[0]
        assert panel.image_variants.currentData() == expected[1]
        assert not panel.image.pixmap().isNull()
    finally:
        panel.close()
        reopened.close()


def test_scene_plan_presentation_requires_explicit_review_and_retains_history(tmp_path):
    session = ProjectSession.create(tmp_path / "project", name="Planning", repository_factory=ProjectRepository)
    try:
        section = ScriptGenerationService(session).append_text(
            "First scene. Second scene.", title="Story",
            expected_active_revision_id=session.active_script.id,
        ).sections[0]
        services = compose_scenes(session)

        assert services.plan_state(section).state == "none"
        proposal = services.suggest_scene_plan(section)
        assert proposal.state == "proposal" and proposal.scenes
        assert services.plans.acceptances(section.section_id) == ()

        newer = services.suggest_scene_plan(section)
        assert newer.state == "proposal" and newer.plan_id != proposal.plan_id
        assert len(services.plans.proposals(section.section_id)) == 2
        accepted = services.accept_scene_plan(section, newer.plan_id)
        assert accepted.state == "accepted" and accepted.acceptance_id
        assert all(scene.time_label == "Timing unavailable" for scene in accepted.scenes)
        assert len(services.plans.proposals(section.section_id)) == 2
        assert len(services.plans.acceptances(section.section_id)) == 1
    finally:
        session.close()
