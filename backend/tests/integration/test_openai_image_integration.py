"""D027 OpenAI image adapter through D017 publication and selection."""

from __future__ import annotations

import base64
from io import BytesIO
import json

from PIL import Image
import pytest

from app.application.image_generation import ImageGenerationService
from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.application.visual_prompts import VisualPromptService
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.openai_image import OpenAIImageProvider, OpenAIImageSettings, OpenAIImageTransportError
from app.storage.image_generation import ImageResultIndex, ProjectImageGeneration
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_plans import ProjectScenePlans
from app.tts.scene_sources import sentence_sources


def image_response(color):
    target = BytesIO()
    Image.new("RGB", (1024, 1024), color).save(target, format="PNG")
    return {"created": 123, "data": [{
        "b64_json": base64.b64encode(target.getvalue()).decode("ascii"),
        "revised_prompt": f"safe {color}",
    }], "usage": {"total_tokens": 17}}


class Transport:
    def __init__(self, *results):
        self.results, self.calls = list(results), []
        self.during = lambda: None

    def create_image(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        self.during()
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def project(tmp_path):
    session = ProjectSession.create(tmp_path / "project", name="D027",
                                    repository_factory=ProjectRepository)
    ScriptGenerationService(session).append_text(
        "A scene for image generation.", title="Scene",
        expected_active_revision_id=session.active_script.id,
    )
    jobs = JobRepository(session.repository)
    index = ImageResultIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    artifacts = ProjectImageGeneration(index, store)
    coordinator = JobCoordinator(jobs)
    prompts = VisualPromptService(index.prompts)
    plans = ScenePlanningService(ProjectScenePlans(session.repository, store), sentence_sources)
    accepted = plans.accept(plans.suggest(session.active_script.sections[0]).id, reviewer_id="editor")
    brief = prompts.pin_context("film_brief", "A short film.")
    style = prompts.pin_context("visual_style", "Natural light.")
    prompt = prompts.create_manual(accepted.id, accepted.plan.scenes[0].id, brief.id, style.id,
                                   "A real generated landscape.")
    prompts.select(prompt.id, expected_selection_id=None)

    def services(transport):
        provider = OpenAIImageProvider(
            OpenAIImageSettings.from_mapping({"model": "gpt-image-fixture", "quality": "low"}),
            transport=transport, environment={"OPENAI_API_KEY": "sk-never-persist"},
        )
        return ImageGenerationService(ResultPublicationService(index, store), coordinator,
                                      artifacts, provider)

    yield session, jobs, index, store, artifacts, coordinator, prompt, services
    session.close()


def generate(project, service, *, force=False):
    _, _, _, _, _, coordinator, prompt, _ = project
    submission = service.enqueue(prompt.id, width=1024, height=1024, force=force)
    claim = coordinator.claim_next("d027-test")
    return service.run(claim), claim


def test_real_adapter_result_is_exportable_and_variants_remain_selectable(project):
    session, jobs, index, store, artifacts, _, prompt, services = project
    transport = Transport(image_response("red"), image_response("blue"))
    service = services(transport)
    first, _ = generate(project, service)
    first_choice = service.select(first.artifact_id, expected_selection_id=None)
    second, _ = generate(project, service, force=True)
    second_choice = service.select(second.artifact_id, expected_selection_id=first_choice.id)
    restored = service.select(first.artifact_id, expected_selection_id=second_choice.id)
    assert artifacts.images.selected(prompt.inputs.scene_id) == restored
    assert {item.artifact_id for item in artifacts.images.history(prompt.inputs.scene_id)} == {
        first.artifact_id, second.artifact_id,
    }
    assert store.read_artifact(next(m.storage_key for m in store.list_artifacts()
                                    if m.artifact_id == first.artifact_id)).startswith(b"\x89PNG")
    manifest = next(m for m in store.list_artifacts() if m.artifact_id == first.artifact_id)
    evidence = manifest.metadata["image_generation"]
    assert evidence["provider"]["provider"] == "openai"
    assert evidence["provider"]["settings"]["quality"] == "low"
    assert evidence["result"] == {"created": 123, "revisedPrompt": "safe red",
                                  "usage": {"total_tokens": 17}}
    assert "sk-never-persist" not in json.dumps(manifest.metadata)
    assert len(transport.calls) == 2 and len(jobs.jobs()) == 2


@pytest.mark.parametrize("failure", [
    OpenAIImageTransportError("OpenAI image request timed out.", retryable=False),
    {"data": [{"b64_json": base64.b64encode(b"corrupt").decode("ascii")}]},
])
def test_timeout_or_corrupt_output_cannot_replace_valid_selected_variant(project, failure):
    _, jobs, index, store, artifacts, coordinator, prompt, services = project
    initial_service = services(Transport(image_response("green")))
    valid, _ = generate(project, initial_service)
    selected = initial_service.select(valid.artifact_id, expected_selection_id=None)
    before = store.list_artifacts()
    failing = services(Transport(failure))
    submission = failing.enqueue(prompt.id, width=1024, height=1024, force=True)
    claim = coordinator.claim_next("d027-test")
    with pytest.raises((OpenAIImageTransportError, RuntimeError)):
        failing.run(claim)
    assert jobs.get_attempt(submission.attempt.id).status.value == "failed"
    assert store.list_artifacts() == before
    assert artifacts.images.selected(prompt.inputs.scene_id) == selected
    assert index.selected()["scene:" + prompt.inputs.scene_id + ":image_candidate"] == valid.artifact_id
    assert "sk-never-persist" not in jobs.get_attempt(submission.attempt.id).error


def test_cancellation_during_remote_call_discards_result_and_preserves_selection(project):
    _, jobs, index, store, artifacts, coordinator, prompt, services = project
    initial = services(Transport(image_response("green")))
    valid, _ = generate(project, initial)
    selected = initial.select(valid.artifact_id, expected_selection_id=None)
    before = store.list_artifacts()
    transport = Transport(image_response("yellow"))
    service = services(transport)
    submission = service.enqueue(prompt.id, width=1024, height=1024, force=True)
    claim = coordinator.claim_next("d027-test")
    transport.during = lambda: coordinator.cancel(claim.id)
    with pytest.raises(ValueError):
        service.run(claim)
    assert jobs.get_attempt(submission.attempt.id).status.value == "canceled"
    assert store.list_artifacts() == before
    assert artifacts.images.selected(prompt.inputs.scene_id) == selected
    assert index.selected()["scene:" + prompt.inputs.scene_id + ":image_candidate"] == valid.artifact_id
