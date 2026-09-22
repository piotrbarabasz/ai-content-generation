"""D026 real-provider composition over D014/D015 with an offline Responses transport."""

from __future__ import annotations

import json

import pytest

from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.application.visual_prompts import VisualPromptService
from app.desktop.llm_composition import compose_installed_llm
from app.providers.openai_llm import OpenAITransportError
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_plans import ProjectScenePlans
from app.storage.visual_prompts import ProjectVisualPrompts
from app.tts.scene_sources import sentence_sources


def api_response(payload):
    return {
        "id": "response-fixture", "model": "gpt-test", "status": "completed",
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(payload)}
        ]}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create_response(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def configured(transport):
    return compose_installed_llm(environment={
        "AICS_LLM_PROVIDER": "openai",
        "AICS_OPENAI_MODEL": "gpt-test",
        "OPENAI_API_KEY": "sk-integration-secret",
    }, transport=transport)


def test_configured_openai_output_becomes_editable_script_and_visual_prompt(tmp_path):
    transport = Transport(
        api_response({"sections": [
            {"title": "Opening", "role": "hook", "text": "Generated opening."},
            {"title": "Details", "role": "body", "text": "Generated details."},
        ]}),
        api_response({"prompt": "Wide cinematic view of the generated scene."}),
    )
    provider = configured(transport)
    with ProjectSession.create(tmp_path / "project", name="D026", language="en",
                               repository_factory=ProjectRepository) as session:
        scripts = ScriptGenerationService(session, provider)
        generated = scripts.generate("Create a concise script", expected_active_revision_id=session.active_script.id)
        edited = scripts.edit_text(generated.sections[1].section_id, "User edit retained.",
                                   expected_active_revision_id=generated.id)
        assert [section.text for section in edited.sections] == ["Generated opening.", "User edit retained."]

        store = LocalArtifactStore.for_project(session.repository)
        plans = ScenePlanningService(ProjectScenePlans(session.repository, store), sentence_sources)
        accepted = plans.accept(plans.suggest(edited.sections[0]).id, reviewer_id="editor")
        prompts = ProjectVisualPrompts(session.repository, store)
        service = VisualPromptService(prompts, provider, generation_identity=provider.generation_identity())
        brief = service.pin_context("film_brief", "A concise educational film.")
        style = service.pin_context("visual_style", "Natural cinematic light.")
        revision = service.generate(accepted.id, accepted.plan.scenes[0].id, brief.id, style.id)
        service.select(revision.id, expected_selection_id=None)
        assert service.selected(revision.inputs.scene_id).prompt == "Wide cinematic view of the generated scene."
        assert len(transport.calls) == 2
        assert all("sk-integration-secret" not in json.dumps(call["payload"]) for call in transport.calls)


@pytest.mark.parametrize("failure", [
    api_response({"sections": []}),
    OpenAITransportError("OpenAI request was rate limited.", status_code=429, retryable=False),
])
def test_malformed_or_failed_provider_response_preserves_current_script_revision(tmp_path, failure):
    provider = configured(Transport(failure))
    with ProjectSession.create(tmp_path / "project", name="D026", repository_factory=ProjectRepository) as session:
        current = ScriptGenerationService(session).append_text(
            "Existing user text.", title="Existing", expected_active_revision_id=session.active_script.id
        )
        with pytest.raises((ValueError, OpenAITransportError)):
            ScriptGenerationService(session, provider).generate(
                "Replace it", expected_active_revision_id=current.id
            )
        assert session.active_script == current


def test_installed_composition_is_opt_in_and_requires_explicit_model():
    assert compose_installed_llm(environment={}) is None
    with pytest.raises(ValueError, match="model"):
        compose_installed_llm(environment={"AICS_LLM_PROVIDER": "openai"})
    with pytest.raises(ValueError, match="Unsupported"):
        compose_installed_llm(environment={"AICS_LLM_PROVIDER": "other"})

