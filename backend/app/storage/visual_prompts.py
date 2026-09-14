"""Immutable D015 artifacts and serialized compare-and-select event history."""

from hashlib import sha256
import json

from app.domain.dependencies import ArtifactDependency, DependencyDeclaration, canonical_json, content_fingerprint
from app.domain.visual_prompt import PromptContextRevision, PromptInputs, PromptSelection, VisualPromptRevision, prompt_request
from .scene_plans import ProjectScenePlans


def _section_payload(section):
    return {"id": section.id, "section_id": section.section_id, "project_id": section.project_id,
            "title": section.title, "role": section.role, "text": section.text}


class ProjectVisualPrompts:
    def __init__(self, repository, store):
        self.scenes = ProjectScenePlans(repository, store)
        self.repository, self.store, self.project_id = repository, store, self.scenes.project_id

    def _manifest(self, kind, value_id):
        matches = [m for m in self.store.list_artifacts() if m.artifact_type == kind and m.metadata.get("value_id") == value_id]
        if len(matches) != 1 or matches[0].metadata.get("project_id") != self.project_id:
            raise ValueError("Unknown or ambiguous project prompt artifact.")
        return matches[0]

    def _read(self, kind, value_id, cls):
        manifest = self._manifest(kind, value_id)
        raw = self.store.read_artifact(manifest.storage_key)
        if sha256(raw).hexdigest() != manifest.checksum:
            raise ValueError("Prompt artifact checksum mismatch.")
        value = cls.from_payload(json.loads(raw))
        owner = value.inputs.project_id if isinstance(value, VisualPromptRevision) else value.project_id
        if value.id != value_id or owner != self.project_id:
            raise ValueError("Prompt artifact identity or project mismatch.")
        return value

    def _write(self, kind, value, metadata=None):
        if any(m.metadata.get("value_id") == value.id for m in self.store.list_artifacts()):
            raise ValueError("Prompt artifact identity already exists.")
        payload = canonical_json(value.to_payload())
        try:
            return self.store.save_artifact(kind + ".json", payload,
                                            {"artifact_type": kind, "project_id": self.project_id, "value_id": value.id,
                                             "module_name": "desktop_visual_prompt", **(metadata or {})})
        except Exception:
            # A cleanup error after D004's index commit must not report a committed
            # selection as failed. Exact registered bytes distinguish that case.
            committed = None
            try:
                manifest = self._manifest(kind, value.id)
                if (manifest.checksum == sha256(payload.encode("utf-8")).hexdigest()
                        and self.store.read_artifact(manifest.storage_key) == payload.encode("utf-8")):
                    committed = manifest
            except Exception:
                pass
            if committed is not None:
                return committed
            raise

    def context(self, revision_id):
        return self._read("prompt_context", revision_id, PromptContextRevision)

    def save_context(self, revision):
        if revision.project_id != self.project_id:
            raise ValueError("Context belongs to a different project.")
        if revision.parent_revision_id is not None:
            parent = self.context(revision.parent_revision_id)
            if (parent.context_id, parent.kind) != (revision.context_id, revision.kind):
                raise ValueError("Context lineage differs from its retained parent.")
        self._write("prompt_context", revision)

    def snapshot(self, acceptance_id, scene_id, brief_revision_id, style_revision_id, *, current=True):
        accepted = self.scenes.acceptance(acceptance_id)
        scene = next((s for s in accepted.plan.scenes if s.id == scene_id), None)
        if scene is None:
            raise ValueError("Scene does not belong to the accepted plan.")
        section = self.repository.get_section(scene.revision_id)
        if current:
            self.scenes.current(section)
        brief, style = self.context(brief_revision_id), self.context(style_revision_id)
        if brief.kind != "film_brief" or style.kind != "visual_style":
            raise ValueError("Prompt inputs require a pinned film brief and visual style.")
        payload = {"scene": {"id": scene.id, "acceptance_id": accepted.id,
                              "source_start": scene.source_start, "source_end": scene.source_end,
                              "text": section.text[scene.source_start:scene.source_end],
                              "visual_description": scene.visual_description},
                   "section_context": _section_payload(section),
                   "film_brief": {"id": brief.id, "context_id": brief.context_id, "text": brief.text},
                   "visual_style": {"id": style.id, "context_id": style.context_id, "text": style.text}}
        return PromptInputs(self.project_id, accepted.id, scene.id, section.section_id, section.id,
                            brief.id, style.id, canonical_json(payload))

    def _validate_inputs(self, inputs, *, current=False):
        expected = self.snapshot(inputs.acceptance_id, inputs.scene_id, inputs.brief_revision_id,
                                 inputs.style_revision_id, current=current)
        if expected != inputs:
            raise ValueError("Prompt inputs differ from retained source revisions.")

    def save_revision(self, revision):
        self._validate_inputs(revision.inputs)
        if revision.parent_revision_id is not None:
            parent = self.revision(revision.parent_revision_id)
            if parent.inputs.scene_id != revision.inputs.scene_id:
                raise ValueError("Prompt parent belongs to a different scene.")
        metadata = DependencyDeclaration(revision.output_key, revision.request, revision.provenance).to_metadata()
        self._write("visual_prompt_revision", revision, metadata | {"scene_id": revision.inputs.scene_id})

    def revision(self, revision_id):
        revision = self._read("visual_prompt_revision", revision_id, VisualPromptRevision)
        self._validate_inputs(revision.inputs)
        manifest = self._manifest("visual_prompt_revision", revision_id)
        declaration = DependencyDeclaration.from_payload(manifest.metadata["desktop_dependencies"])
        if declaration != DependencyDeclaration(revision.output_key, revision.request, revision.provenance):
            raise ValueError("Prompt dependency declaration differs from retained inputs.")
        return revision

    def history(self, scene_id):
        manifests = sorted(self.store.list_artifacts(), key=lambda m: (m.created_at, m.artifact_id))
        return tuple(self.revision(m.metadata["value_id"]) for m in manifests
                     if m.artifact_type == "visual_prompt_revision" and m.metadata.get("scene_id") == scene_id)

    def selection_history(self, scene_id):
        events = [self._read("visual_prompt_selection", m.metadata["value_id"], PromptSelection)
                  for m in self.store.list_artifacts()
                  if m.artifact_type == "visual_prompt_selection" and m.metadata.get("scene_id") == scene_id]
        children = {}
        for event in events:
            revision = self.revision(event.revision_id)
            if event.scene_id != scene_id or revision.inputs.scene_id != scene_id or event.parent_selection_id in children:
                raise ValueError("Invalid or branching prompt selection history.")
            children[event.parent_selection_id] = event
        chain, parent = [], None
        while parent in children:
            event = children.pop(parent)
            chain.append(event)
            parent = event.id
        if children:
            raise ValueError("Incomplete prompt selection history.")
        return tuple(chain)

    def selected(self, scene_id):
        history = self.selection_history(scene_id)
        return history[-1] if history else None

    def save_selection(self, selection):
        if selection.project_id != self.project_id:
            raise ValueError("Selection belongs to a different project.")
        revision = self.revision(selection.revision_id)
        if revision.inputs.scene_id != selection.scene_id:
            raise ValueError("Selection belongs to a different scene.")
        self._validate_inputs(revision.inputs, current=True)
        active = self.selected(selection.scene_id)
        if selection.parent_selection_id != (active.id if active else None):
            raise ValueError("Active prompt selection changed; refresh before selecting.")
        # D003 owns an exclusive coordinator session. D004 registers this complete
        # event atomically, so failed publication leaves the previous chain head.
        self._write("visual_prompt_selection", selection, {"scene_id": selection.scene_id})

    def dependency(self, revision_id):
        revision = self.revision(revision_id)
        manifest = self._manifest("visual_prompt_revision", revision_id)
        return ArtifactDependency(manifest.artifact_id, manifest.checksum,
                                  DependencyDeclaration(revision.output_key, revision.request, revision.provenance))

    def freshness_sources(self, inputs):
        request = prompt_request(inputs, {})
        sources = {edge.key: edge.fingerprint for edge in request.inputs}
        current = next((s for s in self.repository.active_script().sections if s.section_id == inputs.section_id), None)
        key = f"section:{inputs.section_id}:context"
        if current is None:
            sources.pop(key)
        else:
            sources[key] = content_fingerprint(_section_payload(current))
        return sources  # Used for freshness only, never fed back into generation.
