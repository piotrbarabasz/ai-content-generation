"""D015 offline revision, selection and consumed-context behavior in real projects."""

from copy import deepcopy
from dataclasses import replace
import json
import subprocess
import sys

import pytest

from app.application.invalidation import Freshness, evaluate_freshness
from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.application.visual_prompts import VisualPromptService
from app.domain.dependencies import ArtifactDependency, DependencyDeclaration, InputEdge, Provenance, RequestFingerprint
from app.domain.visual_prompt import VisualPromptRevision, visual_prompt_schema
from app.providers.mock_llm import MockLLMProvider
from app.storage.dependency_index import ArtifactDependencyIndex
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_plans import ProjectScenePlans
from app.storage.visual_prompts import ProjectVisualPrompts
from app.tts.scene_sources import sentence_sources
from tests.integration.test_section_tempo import wav


class Provider:
    def __init__(self, output=None):
        self.output = {"prompt": "  Exact generated prompt.\nPreserve whitespace.  "} if output is None else output
        self.calls = []
        self.during = lambda: None

    def generate_structured(self, prompt, schema):
        self.calls.append((json.loads(prompt), deepcopy(schema)))
        self.during()
        return self.output

    def generate_text(self, *args, **kwargs):
        raise AssertionError("Use structured output.")


@pytest.fixture
def project(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="Prompts", repository_factory=ProjectRepository) as session:
        scripts = ScriptGenerationService(session)
        for name in "ABC":
            scripts.append_text(f"First {name} sentence.\n\nSecond {name} sentence.", title=name,
                                expected_active_revision_id=session.active_script.id)
        store = LocalArtifactStore.for_project(session.repository)
        plans = ScenePlanningService(ProjectScenePlans(session.repository, store), sentence_sources)
        accepted = []
        for section in session.active_script.sections:
            proposal = plans.suggest(section)
            accepted.append(plans.accept(proposal.id, reviewer_id="editor"))
        adapter = ProjectVisualPrompts(session.repository, store)
        provider = Provider()
        service = VisualPromptService(adapter, provider, generation_identity={"provider": "fixture", "model": "one"})
        brief = service.pin_context("film_brief", "A film about learning.")
        style = service.pin_context("visual_style", "Watercolor, subdued colors.")
        yield session, store, adapter, service, provider, accepted, brief, style


def args(project, section=0, scene=0, *, brief=None, style=None):
    accepted = project[5][section]
    return accepted.id, accepted.plan.scenes[scene].id, (brief or project[6]).id, (style or project[7]).id


def generate_selected(project, *pos, **kwargs):
    inputs = args(project, *pos, **kwargs)
    revision = project[3].generate(*inputs)
    selection = project[3].select(revision.id, expected_selection_id=None)
    return revision, selection


def test_separate_scenes_manual_lineage_and_explicit_selection_preserve_old_audio_and_reopen(project):
    session, store, adapter, service, provider, *_ = project
    audio = store.save_artifact("existing-section.wav", wav(), {"artifact_type": "audio"})
    original_audio = store.read_artifact(audio.storage_key)
    one, selected = generate_selected(project)
    two, _ = generate_selected(project, 0, 1)
    assert one.inputs.scene_id != two.inputs.scene_id and one.output_key != two.output_key
    before = {m.storage_key: store.read_artifact(m.storage_key) for m in store.list_artifacts()}
    manual = service.edit_manual(one.id, "  Manual composition with a red door.\nKeep this.  ")
    assert manual.parent_revision_id == one.id and manual.provenance == Provenance.MANUAL
    assert service.selected(one.inputs.scene_id) == one  # Creation is never selection.
    choice = service.select(manual.id, expected_selection_id=selected.id)
    assert service.selected(one.inputs.scene_id) == manual and service.selected(two.inputs.scene_id) == two
    assert all(store.read_artifact(key) == data for key, data in before.items())
    assert store.read_artifact(audio.storage_key) == original_audio and len(provider.calls) == 2
    assert adapter.history(one.inputs.scene_id) == (one, manual)
    assert adapter.selection_history(one.inputs.scene_id) == (selected, choice)
    path = session.repository.workspace
    session.close()
    with ProjectSession.open(path, repository_factory=ProjectRepository) as reopened:
        restored = ProjectVisualPrompts(reopened.repository, LocalArtifactStore.for_project(reopened.repository))
        assert restored.selected(one.inputs.scene_id) == choice
        assert restored.revision(choice.revision_id) == manual and restored.history(one.inputs.scene_id) == (one, manual)


def test_provider_receives_only_exact_pinned_context_and_response_is_not_rewritten(project):
    _, _, adapter, service, provider, *_ = project
    revision = service.generate(*args(project))
    assert revision.prompt == provider.output["prompt"] and adapter.selected(revision.inputs.scene_id) is None
    payload, schema = provider.calls[0]
    assert schema == visual_prompt_schema() and payload["inputs"] == revision.inputs.payload
    assert set(payload["inputs"]) == {"scene", "section_context", "film_brief", "visual_style"}
    assert payload["inputs"]["film_brief"]["id"] == project[6].id and payload["inputs"]["visual_style"]["id"] == project[7].id
    assert "Second A sentence." in payload["inputs"]["section_context"]["text"]
    provider.output["prompt"] = "Late mutation"
    assert adapter.revision(revision.id) == revision


@pytest.mark.parametrize("invalid", [None, [], "text", {}, {"prompt": ""}, {"prompt": " \n"},
                                     {"prompt": False}, {"prompt": 42}, {"prompt": ["text"]}, {"prompt": "Text", "extra": True}])
def test_invalid_response_cannot_change_prompt_selection_or_publish_partial_revision(project, invalid):
    one, selected = generate_selected(project)
    before = project[1].list_artifacts()
    project[4].output = invalid
    with pytest.raises(ValueError): project[3].generate(*args(project))
    assert project[1].list_artifacts() == before and project[2].selected(one.inputs.scene_id) == selected


def test_late_generation_never_overwrites_manual_work_and_stale_selection_token_is_rejected(project):
    original, initial = generate_selected(project)
    service, adapter, provider = project[3], project[2], project[4]
    def edit_during_generation():
        manual = service.edit_manual(original.id, "Protected manual prompt")
        service.select(manual.id, expected_selection_id=initial.id)
    provider.during = edit_during_generation
    late = service.generate(*args(project))
    active = adapter.selected(original.inputs.scene_id)
    assert service.selected(original.inputs.scene_id).prompt == "Protected manual prompt"
    assert late.parent_revision_id == original.id and late.provenance == Provenance.GENERATED
    with pytest.raises(ValueError, match="selection changed"):
        service.select(late.id, expected_selection_id=initial.id)
    assert adapter.selected(original.inputs.scene_id) == active
    chosen = service.select(late.id, expected_selection_id=active.id)
    assert chosen.revision_id == late.id  # Explicit reviewed replacement is still possible.


def test_selection_token_detects_aba_and_history_order_is_independent_of_manifest_order(project, monkeypatch):
    original, first = generate_selected(project)
    service, adapter = project[3], project[2]
    manual = service.edit_manual(original.id, "Manual")
    second = service.select(manual.id, expected_selection_id=first.id)
    third = service.select(original.id, expected_selection_id=second.id)
    with pytest.raises(ValueError): service.select(manual.id, expected_selection_id=first.id)
    listing = project[1].list_artifacts
    monkeypatch.setattr(project[1], "list_artifacts", lambda: tuple(reversed(listing())))
    assert adapter.selected(original.inputs.scene_id) == third


def test_style_and_brief_revisions_affect_only_consumers_and_do_not_change_selections(project):
    service, adapter = project[3], project[2]
    alternate = service.pin_context("visual_style", "Independent monochrome style")
    a, _ = generate_selected(project, 0)
    b, _ = generate_selected(project, 1)
    c, _ = generate_selected(project, 2, style=alternate)
    before = {r.inputs.scene_id: adapter.selected(r.inputs.scene_id) for r in (a, b, c)}
    changed = service.pin_context("visual_style", "New watercolor palette", parent_revision_id=project[7].id)
    assert changed.context_id == project[7].context_id
    assert service.freshness(*args(project, 0)).state == Freshness.FRESH  # Pinned old revision remains reproducible.
    assert service.freshness(*args(project, 0, style=changed)).state == Freshness.STALE
    assert service.freshness(*args(project, 1, style=changed)).state == Freshness.STALE
    assert service.freshness(*args(project, 2, style=alternate)).state == Freshness.FRESH
    brief = service.pin_context("film_brief", "Changed shared brief", parent_revision_id=project[6].id)
    for index, style in ((0, project[7]), (1, project[7]), (2, alternate)):
        assert service.freshness(*args(project, index, brief=brief, style=style)).state == Freshness.STALE
    assert {r.inputs.scene_id: adapter.selected(r.inputs.scene_id) for r in (a, b, c)} == before


def test_section_B_context_edit_leaves_A_C_fresh_and_old_prompt_history_readable(project):
    revisions = [generate_selected(project, index)[0] for index in range(3)]
    section = project[0].active_script.sections[1]
    project[0].edit_section(section.section_id, text="Changed B context.")
    assert [project[3].freshness(*args(project, index)).state for index in range(3)] == [Freshness.FRESH, Freshness.STALE, Freshness.FRESH]
    before = project[1].list_artifacts()
    with pytest.raises(ValueError): project[3].generate(*args(project, 1))
    old = project[2].selected(revisions[1].inputs.scene_id)
    with pytest.raises(ValueError): project[3].select(revisions[1].id, expected_selection_id=old.id)
    assert project[1].list_artifacts() == before and project[2].revision(revisions[1].id) == revisions[1]


def test_manual_context_change_flags_review_and_preserves_downstream_bytes_compatibility(project):
    generated, initial = generate_selected(project)
    manual = project[3].edit_manual(generated.id, "Owned manual prompt")
    project[3].select(manual.id, expected_selection_id=initial.id)
    style = project[3].pin_context("visual_style", "New style", parent_revision_id=project[7].id)
    state = project[3].freshness(*args(project, style=style))
    assert state.state == Freshness.STALE and state.review_required
    assert project[3].selected(manual.inputs.scene_id) == manual
    record = project[2].dependency(manual.id)
    records = ArtifactDependencyIndex(project[1]._index).records()
    assert records[record.artifact_id] == record and record.declaration.provenance == Provenance.MANUAL
    image_request = RequestFingerprint.create("existing_image", "1", inputs=[InputEdge.artifact(
        "prompt", manual.output_key, record.artifact_id, record.checksum)])
    image = ArtifactDependency("existing-image", "a" * 64, DependencyDeclaration("image", image_request))
    desired = project[2].snapshot(*args(project, style=style))
    states = evaluate_freshness(requests={manual.output_key: manual.request, "image": image_request},
                                sources=project[2].freshness_sources(desired),
                                selected={manual.output_key: record.artifact_id, "image": image.artifact_id},
                                artifacts={record.artifact_id: record, image.artifact_id: image})
    assert states[manual.output_key].review_required and states["image"].state == Freshness.FRESH


def test_manual_only_operation_requires_no_provider_and_missing_selection_is_reported(project):
    service = VisualPromptService(project[2])
    assert service.freshness(*args(project)).state == Freshness.MISSING
    revision = service.create_manual(*args(project), "Handwritten prompt")
    assert revision.provenance == Provenance.MANUAL and service.selected(revision.inputs.scene_id) is None
    service.select(revision.id, expected_selection_id=None)
    assert service.freshness(*args(project)).state == Freshness.FRESH
    with pytest.raises(ValueError): service.generate(*args(project))
    assert project[4].calls == []


def test_provider_exception_and_selection_publication_failure_preserve_manual_choice(project, monkeypatch):
    original, initial = generate_selected(project)
    manual = project[3].edit_manual(original.id, "Manual")
    def fail(*args): raise OSError("fixture failure")
    project[4].during = fail
    before = project[1].list_artifacts()
    with pytest.raises(OSError): project[3].generate(*args(project))
    monkeypatch.setattr(project[1]._index, "register", fail)
    with pytest.raises(OSError): project[3].select(manual.id, expected_selection_id=initial.id)
    assert project[1].list_artifacts() == before and project[2].selected(original.inputs.scene_id) == initial


def test_unknown_cross_project_context_and_mismatched_context_kind_are_rejected(project, tmp_path):
    inputs = args(project)
    before = project[1].list_artifacts()
    for invalid in ((inputs[0], "unknown-scene", *inputs[2:]),
                    (inputs[0], inputs[1], inputs[3], inputs[2]),
                    (inputs[0], inputs[1], "missing-brief", inputs[3])):
        with pytest.raises(ValueError): project[3].generate(*invalid)
    with pytest.raises(ValueError): project[3].pin_context("film_brief", "wrong kind", parent_revision_id=project[7].id)
    with ProjectSession.create(tmp_path / "other", name="Other", repository_factory=ProjectRepository) as other:
        other_store = LocalArtifactStore.for_project(other.repository)
        with pytest.raises(ValueError): ProjectVisualPrompts(other.repository, project[1])
        other_adapter = ProjectVisualPrompts(other.repository, other_store)
        with pytest.raises(ValueError): other_adapter.context(project[6].id)
        with pytest.raises(ValueError): other_adapter.save_context(project[6])
    assert project[1].list_artifacts() == before and project[4].calls == []


def test_corrupt_prompt_or_context_cannot_be_selected_or_used_for_generation(project):
    revision = project[3].generate(*args(project))
    manifest = next(m for m in project[1].list_artifacts() if m.metadata.get("value_id") == revision.id)
    path = project[1].root / manifest.storage_key
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="checksum"): project[3].select(revision.id, expected_selection_id=None)
    context_manifest = next(m for m in project[1].list_artifacts() if m.metadata.get("value_id") == project[6].id)
    (project[1].root / context_manifest.storage_key).write_bytes(b"{}")
    with pytest.raises(ValueError, match="checksum"): project[3].generate(*args(project))


def test_mock_is_deterministic_schema_specific_and_generation_identity_is_recorded(project):
    identity = {"provider": "mock", "model": "fixture-v1"}
    service = VisualPromptService(project[2], MockLLMProvider(), generation_identity=identity)
    identity["model"] = "external mutation"
    one, two = service.generate(*args(project)), service.generate(*args(project))
    assert one.prompt == two.prompt and one.id != two.id
    assert project[6].text in one.prompt and project[7].text in one.prompt
    assert json.loads(one.request.effective_identity_json)["model"] == "fixture-v1"
    assert VisualPromptRevision.from_payload(json.loads(json.dumps(one.to_payload()))) == one
    service.select(one.id, expected_selection_id=None)
    assert service.freshness(*args(project), generation_identity={"provider": "mock", "model": "v2"}).state == Freshness.STALE


def test_application_import_keeps_provider_storage_tts_and_ui_optional():
    code = "import sys; import app.application.visual_prompts; assert not any(n.startswith(('app.providers', 'app.storage', 'app.tts', 'PySide6', 'sqlite3')) for n in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_generation_after_intervening_section_edit_retains_historical_result_without_selecting(project):
    original, chosen = generate_selected(project)
    section = project[0].active_script.sections[0]
    project[4].during = lambda: project[0].edit_section(section.section_id, text="New source while LLM runs.")
    late = project[3].generate(*args(project))
    assert late.inputs.section_revision_id == section.id
    assert project[2].revision(late.id) == late and project[2].selected(original.inputs.scene_id) == chosen
    with pytest.raises(ValueError): project[3].select(late.id, expected_selection_id=chosen.id)
    assert project[3].freshness(*args(project)).state == Freshness.STALE


@pytest.mark.parametrize("text", ["", " \t\n", False, 123])
def test_invalid_manual_edit_preserves_prompt_history_and_selection(project, text):
    original, selected = generate_selected(project)
    before = project[1].list_artifacts()
    with pytest.raises(ValueError): project[3].edit_manual(original.id, text)
    assert project[1].list_artifacts() == before and project[2].selected(original.inputs.scene_id) == selected


def test_prompt_parent_cannot_cross_scene_and_unpinned_inputs_cannot_be_published(project):
    one, _ = generate_selected(project, 0, 0)
    two, _ = generate_selected(project, 0, 1)
    before = project[1].list_artifacts()
    with pytest.raises(ValueError): project[2].save_revision(replace(two, id="wrong-parent", parent_revision_id=one.id))
    payload = two.inputs.payload
    payload["film_brief"]["text"] = "Unversioned changed text"
    forged_inputs = replace(two.inputs, payload_json=json.dumps(payload))
    from app.domain.visual_prompt import prompt_request
    forged = replace(two, id="unpinned", inputs=forged_inputs,
                     request=prompt_request(forged_inputs, json.loads(two.request.effective_identity_json)))
    with pytest.raises(ValueError): project[2].save_revision(forged)
    assert project[1].list_artifacts() == before


def test_post_commit_cleanup_failure_reports_the_retained_selection_truthfully(project, monkeypatch):
    original, initial = generate_selected(project)
    manual = project[3].edit_manual(original.id, "Manual selection to retain")
    def fail(stage): raise OSError("cleanup after commit")
    monkeypatch.setattr(project[1], "_discard_stage", fail)
    chosen = project[3].select(manual.id, expected_selection_id=initial.id)
    assert project[2].selected(original.inputs.scene_id) == chosen
    assert project[3].selected(original.inputs.scene_id) == manual
