"""D017 decoding/cache adapter and prompt-aware D040 candidate publication."""

from contextlib import contextmanager
import io
import json

from app.domain.dependencies import DependencyDeclaration
from app.domain.publication import PublicationSnapshot
from app.providers.image_generation import ImageGenerationRequest, ImageGenerationResult
from .image_decoder import decode_image
from .local_store import LocalArtifactStore
from .result_publication import ResultArtifactIndex
from .scene_images import ProjectSceneImages
from .visual_prompts import ProjectVisualPrompts


OPERATION = "scene_image.generate"


class ImageResultIndex(ResultArtifactIndex):
    """D015 choices are event chains; compare them at the D040 commit boundary."""

    def __init__(self, repository, jobs, **kwargs):
        super().__init__(repository, jobs, **kwargs)
        self.prompts = ProjectVisualPrompts(repository, LocalArtifactStore(self.root, index=self))

    def _artifact_inputs_match(self, connection, request, visiting=frozenset()):
        matches = super()._artifact_inputs_match(connection, request, visiting)
        if request.operation != OPERATION:
            return matches
        settings = json.loads(request.settings_json)
        prompt = self.prompts.revision(settings["prompt_revision_id"])
        chosen = self.prompts.selected(prompt.inputs.scene_id)
        return (matches and chosen is not None and chosen.id == settings["prompt_selection_id"]
                and chosen.revision_id == prompt.id)


class ProjectImageGeneration:
    def __init__(self, index, store):
        if not isinstance(index, ImageResultIndex) or store._index is not index:
            raise ValueError("Image generation requires the same prompt-aware publication index/store.")
        self.index, self.store, self.prompts = index, store, index.prompts
        self.images = ProjectSceneImages(index.repository, store)

    def prepare(self, prompt_revision_id, *, current=True):
        prompt = self.prompts.revision(prompt_revision_id)
        self.prompts._validate_inputs(prompt.inputs, current=current)
        chosen = self.prompts.selected(prompt.inputs.scene_id)
        if current and (chosen is None or chosen.revision_id != prompt.id):
            raise ValueError("Image generation requires the explicitly selected prompt revision.")
        dependency = self.prompts.dependency(prompt.id)
        section = self.index.repository.get_section(prompt.inputs.section_revision_id)
        return {"prompt_revision_id": prompt.id, "prompt_selection_id": chosen.id if chosen else None,
                "prompt": prompt.prompt, "prompt_artifact_id": dependency.artifact_id,
                "prompt_checksum": dependency.checksum, "scene_id": prompt.inputs.scene_id,
                "acceptance_id": prompt.inputs.acceptance_id, "section_id": section.section_id,
                "section_revision_id": section.id}

    def cached(self, prepared, request):
        section = self.index.repository.get_section(prepared["section_revision_id"])
        bound = PublicationSnapshot(self.index.project_id, "cache-probe", (section,)).bind(request)
        key = "scene:" + prepared["scene_id"] + ":image_candidate"
        artifact_id = self.index.selected().get(key)
        if artifact_id is None:
            return None
        try:
            image = self.images.image(artifact_id)
            manifest = next(m for m in self.store.list_artifacts() if m.artifact_id == artifact_id)
            declaration = DependencyDeclaration.from_payload(manifest.metadata["desktop_dependencies"])
            if (image.provenance == "generated" and declaration.request == bound
                    and self.prepare(prepared["prompt_revision_id"]) == prepared):
                return artifact_id
        except (ValueError, KeyError, OSError, StopIteration):
            pass  # A corrupt/missing candidate is never a cache hit.
        return None

    @contextmanager
    def validated(self, job, result):
        if not isinstance(result, ImageGenerationResult):
            raise ValueError("Provider must return the image result contract.")
        prepared = json.loads(job.input_snapshot_json)["inputs"]["image_generation"]
        settings = json.loads(job.request.settings_json)
        request = ImageGenerationRequest(**settings["request"])
        retained = self.prepare(prepared["prompt_revision_id"], current=False)
        # The current prompt choice may have changed; historical inputs may not.
        if ({k: v for k, v in retained.items() if k != "prompt_selection_id"}
                != {k: v for k, v in prepared.items() if k != "prompt_selection_id"}
                or settings["prompt_revision_id"] != prepared["prompt_revision_id"]
                or settings["prompt_selection_id"] != prepared["prompt_selection_id"]
                or request.prompt != prepared["prompt"]):
            raise ValueError("Image result differs from its frozen prompt inputs.")
        measured = decode_image(result.image_bytes, self.images.limits)
        if ((result.format, result.width, result.height) != (request.format, request.width, request.height)
                or (measured["format"], measured["width"], measured["height"])
                != (result.format, result.width, result.height)):
            raise ValueError("Decoded image differs from requested or declared dimensions/format.")
        snapshot = PublicationSnapshot.from_job(job)
        if len(snapshot.sections) != 1 or snapshot.sections[0].id != prepared["section_revision_id"]:
            raise ValueError("Image publication section differs from its pinned prompt.")
        image = {"version": 1, "project_id": self.index.project_id, "acceptance_id": prepared["acceptance_id"],
                 "scene_id": prepared["scene_id"], "section_revision_id": prepared["section_revision_id"],
                 "source_name": "generated-image." + ("png" if result.format == "PNG" else "jpg"),
                 "provenance": "generated", **measured}
        metadata = {"artifact_type": "scene_image", "project_id": self.index.project_id,
                    "scene_id": prepared["scene_id"], "module_name": "desktop_image_generation", "scene_image": image,
                    "image_generation": {"version": 1, "generation_id": snapshot.generation_id,
                                         "prompt_revision_id": prepared["prompt_revision_id"],
                                         "request": request.to_payload(), "provider": json.loads(job.request.effective_identity_json)}}
        metadata["image_generation"]["result"] = dict(result.metadata)
        with io.BytesIO(result.image_bytes) as source:
            yield source, metadata
