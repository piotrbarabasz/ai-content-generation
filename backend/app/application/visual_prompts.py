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
        prepared = self.prepare_generation(acceptance_id, scene_id, brief_revision_id, style_revision_id)
        return self.retain_generated(prepared, self.generate_payload(prepared))

    def prepare_generation(self, acceptance_id, scene_id, brief_revision_id, style_revision_id):
        if self.provider is None:
            raise ValueError("LLM provider is not configured.")
        if not json.loads(self.identity_json):
            raise ValueError("Generation requires an explicit provider and its configured identity.")
        inputs = self.prompts.snapshot(acceptance_id, scene_id, brief_revision_id, style_revision_id)
        selected = self.prompts.selected(scene_id)
        request = prompt_request(inputs, json.loads(self.identity_json))
        instruction = (
            "Create one image-generation prompt for one render scene: ONE RENDER SCENE = ONE IMAGE = "
            "ONE COHERENT VISUAL COMPOSITION. Choose the single strongest visual idea in the current scene. "
            "Describe one continuous scene, one moment in time, one camera/viewpoint, one main subject or "
            "coherent subject group, and one full-frame composition. Favor natural scene construction, "
            "cinematic documentary/editorial composition, physically plausible lighting, clear subject "
            "separation, and an image readable without text. Preserve the selected Visual Style; use "
            "photographic realism when that style calls for it, and illustration when it calls for illustration. "
            "Never request an infographic, storyboard, collage, grid, multiple panels, split screen, "
            "comic-strip layout, before/after layout, timeline, chart, graph, explanatory diagram, "
            "UI/mockup/interface elements, captions, titles, labels, callouts, arrows, speech bubbles, "
            "visible explanatory text, visible written words, or poster-like information layout. "
            "Compose for subtle camera motion: leave natural breathing room around the focal subject, keep essential "
            "subjects away from extreme edges and critical information within a central safe composition area, "
            "avoid tight crops, and make a subtle zoom or pan possible. Prefer useful foreground, midground and "
            "background separation where appropriate. Keep one clear focal subject; use natural rule-of-thirds "
            "placement when it serves the scene rather than centering everything. Let edge content be expendable; "
            "no critical text or information may depend on exact borders. Never draw safe-area guides, frames or crop marks. "
            "Do not depict every sentence or concept, or combine the entire section or Film Brief into one image. "
            "Context priority: CURRENT SCENE > SECTION CONTEXT > VISUAL STYLE > FILM BRIEF. "
            "The current scene is the authoritative visual subject; section context only resolves ambiguity; "
            "Visual Style controls aesthetics; Film Brief supplies global video context only. "
            "Return only the final image-generation prompt in the required JSON prompt field."
        )
        return (inputs, request, selected, canonical_json({"task": "visual_prompt", "instruction": instruction,
                                                           "inputs": inputs.payload}),
                visual_prompt_schema())

    def generate_payload(self, prepared):
        if self.provider is None:
            raise ValueError("LLM provider is not configured.")
        _, _, _, prompt, schema = prepared
        return self.provider.generate_structured(prompt, schema)

    def retain_generated(self, prepared, payload):
        inputs, request, selected, _, _ = prepared
        parent_revision_id = self._readable_parent_revision(selected)
        revision = VisualPromptRevision(new_id("visual_prompt_revision"), validate_visual_prompt(payload), inputs,
                                        request, Provenance.GENERATED,
                                        parent_revision_id)
        self.prompts.save_revision(revision)
        return revision  # Late results are retained; they never change active selection.

    def _readable_parent_revision(self, selection):
        if selection is None:
            return None
        try:
            self.prompts.revision(selection.revision_id)
            return selection.revision_id
        except (ValueError, OSError, KeyError, TypeError):
            # Keep the selection event as the compare-and-select token, but do
            # not make a new valid prompt inherit unreadable legacy data.
            return None

    def validate_prepared_current(self, prepared):
        inputs, _, selected, _, _ = prepared
        self.prompts.snapshot(inputs.acceptance_id, inputs.scene_id,
                              inputs.brief_revision_id, inputs.style_revision_id)
        current = self.prompts.selected(inputs.scene_id)
        if (current.id if current else None) != (selected.id if selected else None):
            raise ValueError("Visual prompt selection changed during generation; stale result was retained but not selected.")

    def create_manual(self, acceptance_id, scene_id, brief_revision_id, style_revision_id, text):
        inputs = self.prompts.snapshot(acceptance_id, scene_id, brief_revision_id, style_revision_id)
        selected = self.prompts.selected(scene_id)
        revision = VisualPromptRevision(new_id("visual_prompt_revision"), text, inputs,
                                        prompt_request(inputs, json.loads(self.identity_json)), Provenance.MANUAL,
                                        self._readable_parent_revision(selected))
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
        if selection is None:
            return None
        try:
            return self.prompts.revision(selection.revision_id)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            if getattr(self.prompts, "revision_is_generated", lambda _: False)(selection.revision_id):
                getattr(self.prompts, "_recovery_warning", lambda **_: None)(
                    scene_id=scene_id, revision_id=selection.revision_id, reason=exc)
            return None

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
