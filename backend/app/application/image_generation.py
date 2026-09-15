"""Pinned image jobs, completed-result cache and explicit variant selection."""

from dataclasses import dataclass
import json
from typing import Protocol

from app.application.image_intake import ImageIntakeService
from app.domain.dependencies import InputEdge, RequestFingerprint
from app.domain.generation_job import AttemptStatus, JobAttempt
from app.providers.image_generation import ImageGenerationCapabilities, ImageGenerationRequest


OPERATION = "scene_image.generate"


class ImageGenerationArtifactsPort(Protocol):
    images: object
    def prepare(self, prompt_revision_id, *, current=True): ...
    def cached(self, prepared, request): ...
    def validated(self, job, result): ...


@dataclass(frozen=True)
class ImageGenerationSubmission:
    attempt: JobAttempt | None = None
    cached_artifact_id: str | None = None


class ImageGenerationService:
    def __init__(self, publication, coordinator, artifacts: ImageGenerationArtifactsPort, provider=None):
        self.publication, self.coordinator, self.artifacts, self.provider = publication, coordinator, artifacts, provider

    def _capabilities(self):
        if self.provider is None:
            raise ValueError("Image generation requires an explicitly configured provider.")
        capabilities = self.provider.capabilities()
        if not isinstance(capabilities, ImageGenerationCapabilities):
            raise ValueError("Provider must declare image generation capabilities.")
        return capabilities

    def enqueue(self, prompt_revision_id, *, width, height, seed=0, format="PNG", negative_prompt="", force=False):
        if type(force) is not bool:
            raise ValueError("Forced image regeneration must be explicit.")
        prepared = self.artifacts.prepare(prompt_revision_id)
        request = ImageGenerationRequest(prepared["prompt"], width, height, seed, format, negative_prompt)
        capabilities = self._capabilities()
        capabilities.validate(request)
        edge = InputEdge.artifact("visual_prompt", "scene:" + prepared["scene_id"] + ":visual_prompt",
                                  prepared["prompt_artifact_id"], prepared["prompt_checksum"])
        fingerprint = RequestFingerprint.create(OPERATION, "1", inputs=[edge],
            settings={"request": request.to_payload(), "prompt_revision_id": prompt_revision_id,
                      "prompt_selection_id": prepared["prompt_selection_id"]}, effective_identity=capabilities.to_payload())
        if not force:
            cached = self.artifacts.cached(prepared, fingerprint)
            if cached is not None:
                return ImageGenerationSubmission(cached_artifact_id=cached)
        attempt = self.publication.enqueue("scene:" + prepared["scene_id"] + ":image_candidate", fingerprint,
            expected_sections={prepared["section_id"]: prepared["section_revision_id"]}, inputs={"image_generation": prepared})
        return ImageGenerationSubmission(attempt=attempt)

    def run(self, claim):
        existing = self.publication.repository.result(claim)
        if existing is not None:
            return existing
        job = self.coordinator.repository.get_job(claim.job_id)
        if job.request.operation != OPERATION or job.request.algorithm_version != "1":
            raise ValueError("Image service only accepts image-generation jobs.")
        try:
            self.publication.repository.prepare(claim)
            request = ImageGenerationRequest(**json.loads(job.request.settings_json)["request"])
            capabilities = self._capabilities()
            if capabilities.to_payload() != json.loads(job.request.effective_identity_json):
                raise ValueError("Image provider identity changed after enqueue.")
            capabilities.validate(request)
            result = self.provider.generate(request)
            if self._capabilities() != capabilities:
                raise ValueError("Image provider identity changed during generation.")
            with self.artifacts.validated(job, result) as (source, metadata):
                return self.publication.publish(claim, "generated-image." + request.format.lower(), source, metadata=metadata)
        except Exception as exc:
            actual = self.coordinator.repository.get_attempt(claim.id)
            if actual.status == AttemptStatus.COMPLETED:
                return self.publication.repository.result(claim)
            if actual.status == AttemptStatus.RUNNING:
                if actual.cancel_requested:
                    self.coordinator.acknowledge_cancel(claim)
                else:
                    self.coordinator.fail(claim, f"image_generation: {type(exc).__name__}: {str(exc)[:1024]}")
            raise

    def select(self, artifact_id, *, expected_selection_id):
        return ImageIntakeService(self.artifacts.images).select(artifact_id, expected_selection_id=expected_selection_id)
