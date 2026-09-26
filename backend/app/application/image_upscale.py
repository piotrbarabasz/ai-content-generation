"""Coordinator-side immutable upscale publication, cache and compare-and-select."""

from hashlib import sha256

from app.domain.base import new_id
from app.domain.dependencies import content_fingerprint
from app.application.image_intake import ImageIntakeService
from app.providers.image_upscale import ImageUpscaleCapabilities, ImageUpscaleRequest, ImageUpscaleResult
from app.storage.image_decoder import ImageLimits, decode_image


OUTPUT_LIMITS = ImageLimits(max_bytes=64 * 1024 * 1024, max_dimension=8192, max_pixels=32_000_000)


class ImageUpscaleService:
    def __init__(self, images, store, provider=None):
        self.images, self.store, self.provider = images, store, provider
        self.intake = ImageIntakeService(images)

    def capabilities(self):
        if self.provider is None:
            return None
        value = self.provider.capabilities()
        if not isinstance(value, ImageUpscaleCapabilities):
            raise ValueError("Upscaler must declare its capabilities.")
        return value

    def prepare(self, artifact_id, factor):
        capabilities = self.capabilities()
        if capabilities is None:
            raise ValueError("Configure the optional local upscaler first.")
        source = self.images.image(artifact_id)
        with self.store.open_artifact_id(artifact_id) as stream:
            payload = stream.read(OUTPUT_LIMITS.max_bytes + 1)
        if len(payload) != source.size_bytes or sha256(payload).hexdigest() != source.checksum:
            raise ValueError("Source bytes changed before upscaling.")
        request = ImageUpscaleRequest(payload, source.format, source.width, source.height, factor)
        capabilities.validate(request)
        fingerprint = content_fingerprint({"algorithm": "scene_image.upscale.v1",
            "source_artifact_id": artifact_id, "source_checksum": source.checksum,
            "factor": factor, "provider": capabilities.to_payload()})
        selection = self.images.selected(source.scene_id)
        if selection is None or selection.artifact_id != artifact_id:
            raise ValueError("Select the source image before upscaling.")
        return source, selection.id, request, fingerprint, capabilities

    def cached(self, source, fingerprint):
        for manifest in self.store.list_artifacts():
            if (manifest.artifact_type == "scene_image"
                    and manifest.metadata.get("image_upscale", {}).get("fingerprint") == fingerprint):
                image = self.images.image(manifest.artifact_id)
                if image.source_artifact_id == source.artifact_id and image.source_checksum == source.checksum:
                    return image.artifact_id
        return None

    def publish(self, prepared, result):
        source, selection_id, request, fingerprint, capabilities = prepared
        if not isinstance(result, ImageUpscaleResult):
            raise ValueError("Upscaler must return the result contract.")
        if self.capabilities() != capabilities:
            raise ValueError("Upscaler identity changed during inference.")
        measured = decode_image(result.image_bytes, OUTPUT_LIMITS)
        target = ("PNG", source.width * request.factor, source.height * request.factor)
        if ((result.format, result.width, result.height) != target
                or (measured["format"], measured["width"], measured["height"]) != target):
            raise ValueError("Decoded upscale output differs from requested dimensions or format.")
        if self.images.selected(source.scene_id).id != selection_id:
            raise ValueError("Image selection changed during upscaling; stale result was discarded.")
        existing = self.cached(source, fingerprint)
        if existing:
            self.intake.select(existing, expected_selection_id=selection_id)
            return existing
        lineage = {"version": 1, "project_id": source.project_id, "acceptance_id": source.acceptance_id,
                   "scene_id": source.scene_id, "section_revision_id": source.section_revision_id,
                   "source_name": f"Real-ESRGAN-x{request.factor}.png", "provenance": "upscaled",
                   "source_artifact_id": source.artifact_id, "source_checksum": source.checksum,
                   "source_width": source.width, "source_height": source.height, "scale": request.factor,
                   "upscaler": {**capabilities.to_payload(), "diagnostics": result.metadata}, **measured}
        metadata = {"artifact_type": "scene_image", "project_id": source.project_id,
                    "scene_id": source.scene_id, "module_name": "desktop_image_upscale",
                    "scene_image": lineage, "image_upscale": {"version": 1, "fingerprint": fingerprint}}
        manifest = self.store.save_artifact("upscaled-image.png", result.image_bytes, metadata)
        self.intake.select(manifest.artifact_id, expected_selection_id=selection_id)
        return manifest.artifact_id

    def upscale_selected(self, scene_id, factor):
        selected = self.images.selected(scene_id)
        if selected is None:
            raise ValueError("Select an image before upscaling.")
        prepared = self.prepare(selected.artifact_id, factor)
        source, _, request, fingerprint, _ = prepared
        existing = self.cached(source, fingerprint)
        if existing:
            self.intake.select(existing, expected_selection_id=selected.id)
            return existing
        return self.publish(prepared, self.provider.upscale(request))
