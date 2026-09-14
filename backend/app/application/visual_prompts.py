"""Independent prompt generation, manual ownership and explicit selection."""

import json
from typing import Protocol

from app.application.invalidation import evaluate_freshness
from app.application.script_generation import StructuredScriptProvider
from app.domain.base import new_id
from app.domain.dependencies import Provenance, canonical_json
from app.domain.visual_prompt import (PromptContextRevision, PromptSelection, VisualPromptRevision,
                                      prompt_request, validate_visual_prompt, visual_prompt_schema)


class VisualPromptsPort(Protocol):
    project_id: str
    def context(self, revision_id): ...
    def save_context(self, revision): ...
    def snapshot(self, acceptance_id, scene_id, brief_revision_id, style_revision_id, *, current=True): ...
    def revision(self, revision_id): ...
    def save_revision(self, revision): ...
    def selected(self, scene_id): ...
    def save_selection(self, selection): ...
    def dependency(self, revision_id): ...
    def freshness_sources(self, inputs): ...


class VisualPromptService:
    def __init__(self, prompts: VisualPromptsPort, provider: StructuredScriptProvider | None = None, *, generation_identity=None):
        self.prompts, self.provider = prompts, provider
        self.identity_json = canonical_json(dict(generation_identity or {}))

    def pin_context(self, kind, text, *, parent_revision_id=None):
        parent = self.prompts.context(parent_revision_id) if parent_revision_id is not None else None
        if parent is not None and parent.kind != kind:
            raise ValueError("Context revision must retain its kind.")
        revision = PromptContextRevision(new_id("prompt_context_revision"), parent.context_id if parent else new_id("prompt_context"),
                                         self.prompts.project_id, kind, text, parent_revision_id)
        self.prompts.save_context(revision)
        return revision

    def generate(self, acceptance_id, scene_id, brief_revision_id, style_revision_id):
        if self.provider is None or not json.loads(self.identity_json):
            raise ValueError("Generation requires an explicit provider and its configured identity.")
        inputs = self.prompts.snapshot(acceptance_id, scene_id, brief_revision_id, style_revision_id)
        selected = self.prompts.selected(scene_id)
        request = prompt_request(inputs, json.loads(self.identity_json))
        payload = self.provider.generate_structured(canonical_json({"task": "visual_prompt", "inputs": inputs.payload}), visual_prompt_schema())
        revision = VisualPromptRevision(new_id("visual_prompt_revision"), validate_visual_prompt(payload), inputs, request,
                                        Provenance.GENERATED, selected.revision_id if selected else None)
        self.prompts.save_revision(revision)
        return revision  # A late result is retained; it never changes active selection.

    def create_manual(self, acceptance_id, scene_id, brief_revision_id, style_revision_id, text):
        inputs = self.prompts.snapshot(acceptance_id, scene_id, brief_revision_id, style_revision_id)
        selected = self.prompts.selected(scene_id)
        revision = VisualPromptRevision(new_id("visual_prompt_revision"), text, inputs,
                                        prompt_request(inputs, json.loads(self.identity_json)), Provenance.MANUAL,
                                        selected.revision_id if selected else None)
        self.prompts.save_revision(revision)
        return revision

    def edit_manual(self, revision_id, text):
        parent = self.prompts.revision(revision_id)
        revision = VisualPromptRevision(new_id("visual_prompt_revision"), text, parent.inputs, parent.request,
                                        Provenance.MANUAL, parent.id)
        self.prompts.save_revision(revision)
        return revision

    def select(self, revision_id, *, expected_selection_id):
        revision = self.prompts.revision(revision_id)
        selection = PromptSelection(new_id("prompt_selection"), revision.inputs.project_id, revision.inputs.scene_id,
                                    revision.id, expected_selection_id)
        self.prompts.save_selection(selection)
        return selection

    def selected(self, scene_id):
        selection = self.prompts.selected(scene_id)
        return self.prompts.revision(selection.revision_id) if selection else None

    def freshness(self, acceptance_id, scene_id, brief_revision_id, style_revision_id, *, generation_identity=None):
        # Explicit desired revision bindings, never an unversioned global-context lookup.
        inputs = self.prompts.snapshot(acceptance_id, scene_id, brief_revision_id, style_revision_id, current=False)
        selected = self.prompts.selected(scene_id)
        record = self.prompts.dependency(selected.revision_id) if selected else None
        identity = generation_identity if generation_identity is not None else (
            json.loads(record.declaration.request.effective_identity_json) if record else json.loads(self.identity_json))
        request = prompt_request(inputs, identity)
        key = f"scene:{scene_id}:visual_prompt"
        return evaluate_freshness(requests={key: request}, sources=self.prompts.freshness_sources(inputs),
                                  selected={key: record.artifact_id} if record else {},
                                  artifacts={record.artifact_id: record} if record else {})[key]
