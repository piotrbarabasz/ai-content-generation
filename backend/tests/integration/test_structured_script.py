"""D014 exact provider text, manual input and transactional revision selection."""

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from app.application.projects import ProjectSession
from app.application.script_generation import ScriptGenerationService
from app.domain.script_sections import script_sections_schema, validate_script_sections
from app.providers.mock_llm import MockLLMProvider
from app.storage.project_repository import ProjectRepository


def response(*roles):
    return {"sections": [{"title": f"Title {index}", "role": role,
                           "text": f"  Provider text {index}: zażółć gęślą jaźń.\nSecond line.  "}
                          for index, role in enumerate(roles or ("hook", "body", "body", "close"))]}


class Provider:
    def __init__(self, payload):
        self.payload, self.calls = payload, []
        self.during_call = lambda: None

    def generate_structured(self, prompt, schema):
        self.calls.append((prompt, deepcopy(schema)))
        self.during_call()
        return self.payload

    def generate_text(self, *args, **kwargs):
        raise AssertionError("Desktop generation must consume structured output.")


@pytest.fixture
def session(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="Scripts", language="pl", repository_factory=ProjectRepository) as project:
        service = ScriptGenerationService(project)
        for name in "ABC":
            service.append_text("Original " + name, title=name, expected_active_revision_id=project.active_script.id)
        yield project


@pytest.mark.parametrize("roles", [("hook", "body", "body", "close"), ("body", "body", "cta"), ("body",),
                                    ("example", "example", "cta", "cta")])
def test_provider_order_text_repeated_roles_and_optional_cta_are_authoritative(session, roles):
    payload = response(*roles)
    if len(roles) > 1:
        payload["sections"][1]["text"] = payload["sections"][0]["text"]  # No deduplication.
    provider = Provider(payload)
    service = ScriptGenerationService(session, provider)
    old = session.active_script
    generated = service.generate("Requested script", expected_active_revision_id=old.id)
    assert [s.text for s in generated.sections] == [s["text"] for s in payload["sections"]]
    assert [s.title for s in generated.sections] == [s["title"] for s in payload["sections"]]
    assert tuple(s.role for s in generated.sections) == roles
    assert generated.script_id == old.script_id and generated.project_id == old.project_id and generated.language == "pl"
    assert generated.parent_revision_id == old.id and generated.id != old.id
    assert len({s.section_id for s in generated.sections}) == len(roles)
    assert not {s.section_id for s in old.sections}.intersection(s.section_id for s in generated.sections)
    assert session.repository.get_script(old.id) == old
    assert len(provider.calls) == 1 and provider.calls[0][1] == script_sections_schema()
    assert json.loads(provider.calls[0][0])["language"] == "pl"
    payload["sections"][0]["text"] = "Late mutation"
    assert session.active_script == generated
    path = session.repository.workspace
    session.close()
    with ProjectSession.open(path, repository_factory=ProjectRepository) as reopened:
        assert reopened.active_script == generated and reopened.repository.get_script(old.id) == old


INVALID = [None, [], "{\"sections\": []}", {}, {"sections": []}, {"sections": ()},
           {"sections": {}, "extra": True}, {"sections": [None]},
           {"sections": [{"role": "body", "text": "Missing title"}]},
           {"sections": [{"title": "Title", "role": "body", "text": "Text", "order": 1}]},
           {"sections": [{"title": "Title", "role": "body", "text": False}]},
           {"sections": [{"title": "Title", "role": 3, "text": "Text"}]},
           {"sections": [{"title": [], "role": "body", "text": "Text"}]},
           {"sections": [{"title": "Title", "role": "body", "text": " \t\n"}]},
           {"sections": [{"title": "Title", "role": " ", "text": "Text"}]},
           {"sections": [{"title": "", "role": "body", "text": "Text"}]},
           {"sections": response()["sections"], "script_id": "injected"},
           {"sections": [response()["sections"][0], {"role": "body", "text": "Invalid last section"}]}]


@pytest.mark.parametrize("payload", INVALID)
def test_invalid_entire_response_cannot_replace_active_script_or_write_history(session, payload):
    old, history = session.active_script, session.repository.script_history()
    provider = Provider(payload)
    with pytest.raises(ValueError):
        ScriptGenerationService(session, provider).generate("Request", expected_active_revision_id=old.id)
    assert session.active_script == old and session.repository.script_history() == history
    assert all(session.repository.section_history(s.section_id) == (s,) for s in old.sections)


def test_provider_exception_and_missing_provider_leave_selection_untouched(session):
    old = session.active_script
    provider = Provider(response())
    def fail(): raise RuntimeError("offline provider failed")
    provider.during_call = fail
    with pytest.raises(RuntimeError):
        ScriptGenerationService(session, provider).generate("Request", expected_active_revision_id=old.id)
    with pytest.raises(ValueError):
        ScriptGenerationService(session).generate("Request", expected_active_revision_id=old.id)
    assert session.active_script == old


def test_stale_request_is_rejected_before_provider_call(session):
    provider = Provider(response())
    with pytest.raises(ValueError):
        ScriptGenerationService(session, provider).generate("Request", expected_active_revision_id="stale")
    assert provider.calls == []


def test_intervening_edit_is_not_overwritten_by_late_valid_generation(session):
    old = session.active_script
    provider = Provider(response())
    provider.during_call = lambda: session.edit_section(old.sections[1].section_id, text="New B while generating")
    from app.storage.project_repository import RevisionConflictError
    with pytest.raises(RevisionConflictError):
        ScriptGenerationService(session, provider).generate("Request", expected_active_revision_id=old.id)
    assert session.active_script.sections[1].text == "New B while generating"
    assert session.active_script.sections[0] == old.sections[0] and session.active_script.sections[2] == old.sections[2]
    assert session.repository.get_script(old.id) == old


def test_provider_cannot_relax_validation_by_mutating_its_schema(session):
    class SchemaMutator(Provider):
        def generate_structured(self, prompt, schema):
            schema.clear()
            return {"unexpected": "not a script"}
    old = session.active_script
    with pytest.raises(ValueError):
        ScriptGenerationService(session, SchemaMutator(None)).generate("Request", expected_active_revision_id=old.id)
    assert session.active_script == old and script_sections_schema()["additionalProperties"] is False


def test_manual_append_and_edit_preserve_original_text_identity_and_unrelated_sections(session):
    service = ScriptGenerationService(session)
    original = session.active_script
    value = "  Ręczny tekst.\n\nDrugi akapit!  "
    appended = service.append_text(value, title="User section", role="body", expected_active_revision_id=original.id)
    assert appended.sections[:-1] == original.sections and appended.sections[-1].text == value
    target = appended.sections[1]
    edited = service.edit_text(target.section_id, value, title="Updated B", expected_active_revision_id=appended.id)
    assert edited.sections[1].section_id == target.section_id and edited.sections[1].parent_revision_id == target.id
    assert edited.sections[1].text == value and edited.sections[1].role == target.role
    assert edited.sections[0] == appended.sections[0] and edited.sections[2:] == appended.sections[2:]
    assert session.repository.get_script(original.id) == original
    with pytest.raises(ValueError):
        service.append_text("  ", title="Empty", expected_active_revision_id=edited.id)
    with pytest.raises(ValueError):
        service.edit_text(target.section_id, "Another", expected_active_revision_id=appended.id)
    assert session.active_script == edited


def test_manual_structured_replacement_uses_the_same_strict_contract_without_provider(session):
    service = ScriptGenerationService(session)
    old = session.active_script
    with pytest.raises(ValueError): service.replace_sections(INVALID[-1], expected_active_revision_id=old.id)
    assert session.active_script == old
    payload = response("body", "body")
    new = service.replace_sections(payload, expected_active_revision_id=old.id)
    assert [s.text for s in new.sections] == [s["text"] for s in payload["sections"]]


def test_atomic_repository_failure_rolls_back_all_new_sections_and_script(session, monkeypatch):
    old, history = session.active_script, session.repository.script_history()
    connection = session.repository._connection
    before = connection.execute("SELECT COUNT(*) FROM section_revisions").fetchone()[0]
    save = session.repository._save_section
    calls = []
    def fail_second(section):
        calls.append(section)
        if len(calls) == 2: raise OSError("fixture interrupted transaction")
        save(section)
    monkeypatch.setattr(session.repository, "_save_section", fail_second)
    with pytest.raises(OSError):
        ScriptGenerationService(session, Provider(response())).generate("Request", expected_active_revision_id=old.id)
    assert session.active_script == old and session.repository.script_history() == history
    assert connection.execute("SELECT COUNT(*) FROM section_revisions").fetchone()[0] == before


@pytest.mark.parametrize("include_cta", [False, True])
def test_mock_consumes_known_schema_deterministically_and_returns_editable_content(session, include_cta):
    mock = MockLLMProvider()
    request = "First idea.\n\nSecond idea.\n\nThird idea."
    old = session.active_script
    result = ScriptGenerationService(session, mock).generate(request, include_cta=include_cta, expected_active_revision_id=old.id)
    assert [s.text for s in result.sections[:3]] == ["First idea.", "Second idea.", "Third idea."]
    assert [s.role for s in result.sections[:3]] == ["hook", "body", "body"]
    assert len(result.sections) == 3 + include_cta
    if include_cta: assert result.sections[-1].role == "cta"
    prompt = json.dumps({"task": "generate_script_sections", "language": "pl", "request": request, "include_cta": include_cta})
    one = mock.generate_structured(prompt, script_sections_schema())
    two = mock.generate_structured(prompt, script_sections_schema())
    assert one == two and validate_script_sections(one)
    one["sections"][0]["text"] = "mutated"
    assert mock.generate_structured(prompt, script_sections_schema()) == two
    legacy = mock.generate_structured("Request", {"type": "object"})
    assert set(legacy) == {"provider", "prompt", "schema", "response"}


@pytest.mark.parametrize("generation_request,cta", [("", False), (" \n", False), (False, False), ("Request", 1)])
def test_invalid_generation_arguments_do_not_call_provider(session, generation_request, cta):
    provider = Provider(response())
    old = session.active_script
    with pytest.raises(ValueError):
        ScriptGenerationService(session, provider).generate(generation_request, include_cta=cta, expected_active_revision_id=old.id)
    assert provider.calls == [] and session.active_script == old


def test_manual_service_import_does_not_load_optional_provider_storage_or_ui():
    code = "import sys; import app.application.script_generation; assert not any(n.startswith(('app.providers', 'app.storage', 'PySide6', 'sqlite3')) for n in sys.modules)"
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
