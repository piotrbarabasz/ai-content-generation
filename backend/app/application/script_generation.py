"""Desktop script generation and manual input through immutable project revisions."""

from dataclasses import replace
import json
from typing import Protocol

from app.domain.base import new_id
from app.domain.narrative_segment import SectionRevision
from app.domain.script_sections import ScriptSectionInput, script_sections_schema, validate_script_sections


class StructuredScriptProvider(Protocol):
    # Structural subset of LLMProvider; keeps its storage/provider imports out of core.
    def generate_structured(self, prompt: str, schema: dict) -> dict: ...


class ScriptSessionPort(Protocol):
    @property
    def active_script(self): ...
    def save_script(self, revision, *, expected_active_revision_id: str): ...


class ScriptGenerationService:
    def __init__(self, session: ScriptSessionPort, provider: StructuredScriptProvider | None = None):
        self.session, self.provider = session, provider

    def _current(self, expected_active_revision_id):
        current = self.session.active_script
        if current.id != expected_active_revision_id:
            raise ValueError("Active script changed; explicitly retry from its current revision.")
        return current

    def _save(self, current, sections):
        revision = replace(current, id=new_id("script_revision"), parent_revision_id=current.id, sections=tuple(sections))
        self.session.save_script(revision, expected_active_revision_id=current.id)
        return revision

    def _replace(self, current, payload):
        values = validate_script_sections(payload)
        sections = tuple(SectionRevision.create(project_id=current.project_id, title=value.title,
                                                role=value.role, text=value.text) for value in values)
        return self._save(current, sections)

    def generate(self, request: str, *, expected_active_revision_id: str, include_cta: bool = False):
        """Explicit whole-script replacement; run outside UI when using a slow provider."""
        current = self._current(expected_active_revision_id)
        if type(request) is not str or not request.strip():
            raise ValueError("Script generation request must be nonempty text.")
        if type(include_cta) is not bool:
            raise ValueError("include_cta must be a boolean.")
        if self.provider is None:
            raise ValueError("Script generation requires an explicitly composed LLM provider.")
        prompt = json.dumps({"task": "generate_script_sections", "language": current.language,
                             "request": request, "include_cta": include_cta}, ensure_ascii=False)
        payload = self.provider.generate_structured(prompt, script_sections_schema())
        return self._replace(current, payload)

    def replace_sections(self, payload, *, expected_active_revision_id: str):
        """Explicit manual structured replacement, with the same strict contract."""
        return self._replace(self._current(expected_active_revision_id), payload)

    def append_text(self, text: str, *, title: str, role: str = "body", expected_active_revision_id: str):
        current = self._current(expected_active_revision_id)
        value = ScriptSectionInput(title, role, text)
        section = SectionRevision.create(project_id=current.project_id, title=value.title, role=value.role, text=value.text)
        return self._save(current, (*current.sections, section))

    def edit_text(self, section_id: str, text: str, *, expected_active_revision_id: str,
                  title: str | None = None, role: str | None = None):
        current = self._current(expected_active_revision_id)
        section = current.section(section_id)
        value = ScriptSectionInput(section.title if title is None else title, section.role if role is None else role, text)
        revision = current.edit_section(section_id, text=value.text, title=value.title, role=value.role)
        self.session.save_script(revision, expected_active_revision_id=current.id)
        return revision
