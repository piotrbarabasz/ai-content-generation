"""Coordinator-side immutable upscale publication, cache and compare-and-select."""

from dataclasses import dataclass
from hashlib import sha256

from app.domain.base import new_id
from app.domain.dependencies import content_fingerprint
from app.application.image_intake import ImageIntakeService
from app.providers.image_upscale import ImageUpscaleCapabilities, ImageUpscaleRequest, ImageUpscaleResult
from app.storage.image_decoder import ImageLimits, decode_image
from app.application.image_presets import (RESOLUTIONS, compatible_aspect, final_dimensions,
                                           delivery_dimensions, motion_master_dimensions)


OUTPUT_LIMITS = ImageLimits(max_bytes=64 * 1024 * 1024, max_dimension=8192, max_pixels=32_000_000)


@dataclass(frozen=True)
class FinalImageRequest:
    source_artifact_id: str
    target_profile: str
    target_width: int
    target_height: int

    def __post_init__(self):
        if (type(self.source_artifact_id) is not str or not self.source_artifact_id
                or self.target_profile not in RESOLUTIONS or self.target_profile == "draft"
                or any(type(n) is not int or n <= 0 for n in (self.target_width, self.target_height))):
            raise ValueError("Invalid final-image request.")


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
        request = ImageUpscaleRequest(payload, source.format, source.width, source.height,
                                      target_width=source.width * factor, target_height=source.height * factor)
        capabilities.validate(request)
        fingerprint = content_fingerprint({"algorithm": "scene_image.upscale.v1",
            "source_artifact_id": artifact_id, "source_checksum": source.checksum,
            "factor": factor, "provider": capabilities.to_payload()})
        selection = self.images.selected(source.scene_id)
        if selection is None or selection.artifact_id != artifact_id:
            raise ValueError("Select the source image before upscaling.")
        return source, selection.id, request, fingerprint, capabilities, factor

    def prepare_final(self, artifact_id, orientation, resolution):
        capabilities = self.capabilities()
        if capabilities is None:
            raise ValueError("Configure the optional local upscaler first.")
        selected = self.images.image(artifact_id)
        choice = self.images.selected(selected.scene_id)
        if choice is None or choice.artifact_id != artifact_id:
            raise ValueError("Select the source image before creating a final image.")
        # Switching from one finished resolution to another always starts from
        # the retained original source, so no neural pass is chained.
        source = selected
        if selected.provenance in ("upscaled", "final"):
            source = self.images.image(selected.source_artifact_id)
        target_width, target_height = final_dimensions(orientation, resolution)
        if resolution == "draft":
            raise ValueError("Draft / Source is already the generated image; no final derivative is needed.")
        if not compatible_aspect(source.width, source.height, target_width, target_height):
            ratio = "16:9 Landscape" if orientation == "landscape" else "9:16 Portrait"
            raise ValueError(f"Selected image is not compatible with the {ratio} preset. Cropping is not part of this operation.")
        app_request = FinalImageRequest(source.artifact_id, resolution, target_width, target_height)
        with self.store.open_artifact_id(source.artifact_id) as stream:
            payload = stream.read(OUTPUT_LIMITS.max_bytes + 1)
        if len(payload) != source.size_bytes or sha256(payload).hexdigest() != source.checksum:
            raise ValueError("Source bytes changed before final image processing.")
        request = ImageUpscaleRequest(payload, source.format, source.width, source.height,
                                      target_width=target_width, target_height=target_height)
        capabilities.validate(request)
        fingerprint = content_fingerprint({"algorithm": "scene_image.final.v2",
            "source_artifact_id": app_request.source_artifact_id, "source_checksum": source.checksum,
            "target_profile": resolution, "target_width": target_width, "target_height": target_height,
            "provider": capabilities.to_payload(), "native_model_scale": capabilities.native_model_scale,
            "final_resize_method": "Pillow Lanczos"})
        return source, choice.id, request, fingerprint, capabilities, app_request

    def prepare_motion_master(self, artifact_id, orientation, resolution):
        capabilities = self.capabilities()
        if capabilities is None:
            raise ValueError("Configure the optional local upscaler first.")
        selected = self.images.image(artifact_id)
        choice = self.images.selected(selected.scene_id)
        if choice is None or choice.artifact_id != artifact_id:
            raise ValueError("Select an image before creating a motion master.")
        source = selected
        if selected.provenance in ("upscaled", "final", "motion_master"):
            source = self.images.image(selected.source_artifact_id)
        delivery_width, delivery_height = delivery_dimensions(orientation, resolution)
        target_width, target_height = motion_master_dimensions(orientation, resolution)
        if resolution == "draft":
            raise ValueError("Draft / Source does not create a motion master.")
        if not compatible_aspect(source.width, source.height, delivery_width, delivery_height):
            ratio = "16:9 Landscape" if orientation == "landscape" else "9:16 Portrait"
            raise ValueError(f"Selected image is not compatible with the {ratio} preset.")
        with self.store.open_artifact_id(source.artifact_id) as stream:
            payload = stream.read(OUTPUT_LIMITS.max_bytes + 1)
        if len(payload) != source.size_bytes or sha256(payload).hexdigest() != source.checksum:
            raise ValueError("Source bytes changed before motion-master processing.")
        request = ImageUpscaleRequest(payload, source.format, source.width, source.height,
                                      target_width=target_width, target_height=target_height)
        capabilities.validate(request)
        fingerprint = content_fingerprint({"algorithm": "scene_image.motion_master.v1",
            "source_artifact_id": source.artifact_id, "source_checksum": source.checksum,
            "delivery_profile": resolution, "delivery_width": delivery_width,
            "delivery_height": delivery_height, "master_width": target_width, "master_height": target_height,
            "overscan_policy": "5:4", "provider": capabilities.to_payload(),
            "native_model_scale": 4, "final_resize_policy": "Pillow Lanczos"})
        app_request = FinalImageRequest(source.artifact_id, resolution, target_width, target_height)
        return source, choice.id, request, fingerprint, capabilities, (app_request, delivery_width, delivery_height)

    def cached_motion_master(self, prepared):
        source, _, _, fingerprint, _, _ = prepared
        for manifest in self.store.list_artifacts():
            if (manifest.artifact_type == "scene_image"
                    and manifest.metadata.get("image_motion_master", {}).get("fingerprint") == fingerprint):
                image = self.images.image(manifest.artifact_id)
                if image.provenance == "motion_master" and image.source_artifact_id == source.artifact_id:
                    return image.artifact_id
        return None

    def publish_motion_master(self, prepared, result):
        source, selection_id, request, fingerprint, capabilities, details = prepared
        app_request, delivery_width, delivery_height = details
        if not isinstance(result, ImageUpscaleResult) or self.capabilities() != capabilities:
            raise ValueError("Upscaler result or identity changed during motion-master inference.")
        measured = decode_image(result.image_bytes, OUTPUT_LIMITS)
        target = ("PNG", request.target_width, request.target_height)
        if (result.format, result.width, result.height) != target or (measured["format"], measured["width"], measured["height"]) != target:
            raise ValueError("Decoded motion master differs from requested dimensions.")
        current = self.images.selected(source.scene_id)
        if current is None or current.id != selection_id:
            raise ValueError("Image selection changed during processing; stale motion master discarded.")
        existing = self.cached_motion_master(prepared)
        if existing:
            self.intake.select(existing, expected_selection_id=selection_id)
            return existing
        diagnostics = result.metadata.get("diagnostics", {})
        native_width, native_height = source.width * 4, source.height * 4
        resized = (native_width, native_height) != (request.target_width, request.target_height)
        method = "Lanczos" if resized else None
        if (diagnostics.get("native_model_scale") != 4
                or (diagnostics.get("native_width"), diagnostics.get("native_height")) != (native_width, native_height)
                or (diagnostics.get("final_width"), diagnostics.get("final_height")) != (request.target_width, request.target_height)
                or diagnostics.get("final_resize_occurred") is not resized
                or diagnostics.get("final_resize_method") != method):
            raise ValueError("Upscaler diagnostics do not describe one native x4 inference and the requested resize.")
        lineage = {"version": 3, "project_id": source.project_id, "acceptance_id": source.acceptance_id,
            "scene_id": source.scene_id, "section_revision_id": source.section_revision_id,
            "source_name": "motion-master.png", "provenance": "motion_master",
            "source_artifact_id": source.artifact_id, "source_checksum": source.checksum,
            "source_width": source.width, "source_height": source.height,
            "target_profile": app_request.target_profile, "master_width": request.target_width,
            "master_height": request.target_height, "delivery_width": delivery_width,
            "delivery_height": delivery_height, "overscan_policy": "5:4", "native_model_scale": 4,
            "native_width": native_width, "native_height": native_height,
            "final_resize_method": method, "lineage_version": 3,
            "upscaler": {**capabilities.to_payload(), "diagnostics": result.metadata}, **measured}
        metadata = {"artifact_type": "scene_image", "project_id": source.project_id,
            "scene_id": source.scene_id, "module_name": "desktop_image_motion_master",
            "scene_image": lineage, "image_motion_master": {"version": 1, "fingerprint": fingerprint}}
        manifest = self.store.save_artifact("motion-master.png", result.image_bytes, metadata)
        self.intake.select(manifest.artifact_id, expected_selection_id=selection_id)
        return manifest.artifact_id

    def create_motion_master_selected(self, artifact_id, orientation, resolution):
        prepared = self.prepare_motion_master(artifact_id, orientation, resolution)
        cached = self.cached_motion_master(prepared)
        if cached:
            self.intake.select(cached, expected_selection_id=prepared[1])
            return cached
        return self.publish_motion_master(prepared, self.provider.upscale(prepared[2]))

    def cached_final(self, prepared):
        source, _, _, fingerprint, _, app_request = prepared
        for manifest in self.store.list_artifacts():
            if (manifest.artifact_type == "scene_image"
                    and manifest.metadata.get("image_final", {}).get("fingerprint") == fingerprint):
                image = self.images.image(manifest.artifact_id)
                if (image.source_artifact_id == source.artifact_id
                        and image.source_checksum == source.checksum
                        and image.target_profile == app_request.target_profile
                        and (image.target_width, image.target_height) ==
                        (app_request.target_width, app_request.target_height)):
                    return image.artifact_id
        return None

    def publish_final(self, prepared, result):
        source, selection_id, request, fingerprint, capabilities, app_request = prepared
        if not isinstance(result, ImageUpscaleResult):
            raise ValueError("Upscaler must return the result contract.")
        if self.capabilities() != capabilities:
            raise ValueError("Upscaler identity changed during inference.")
        measured = decode_image(result.image_bytes, OUTPUT_LIMITS)
        target = ("PNG", app_request.target_width, app_request.target_height)
        if ((result.format, result.width, result.height) != target
                or (measured["format"], measured["width"], measured["height"]) != target):
            raise ValueError("Decoded final image differs from the requested target dimensions.")
        current = self.images.selected(source.scene_id)
        if current is None or current.id != selection_id:
            raise ValueError("Image selection changed during final image processing; stale result was discarded.")
        existing = self.cached_final(prepared)
        if existing:
            self.intake.select(existing, expected_selection_id=selection_id)
            return existing
        diagnostics = result.metadata.get("diagnostics", {})
        native_width, native_height = source.width * 4, source.height * 4
        if (diagnostics.get("native_model_scale") != 4
                or (diagnostics.get("native_width"), diagnostics.get("native_height")) != (native_width, native_height)
                or (diagnostics.get("final_width"), diagnostics.get("final_height")) !=
                (app_request.target_width, app_request.target_height)):
            raise ValueError("Upscaler diagnostics do not describe one native x4 inference at the requested size.")
        resized = (native_width, native_height) != (app_request.target_width, app_request.target_height)
        expected_resize_method = "Lanczos" if resized else None
        if (diagnostics.get("final_resize_occurred") is not resized
                or diagnostics.get("final_resize_method") != expected_resize_method):
            raise ValueError("Upscaler final-resize diagnostics are inconsistent with requested dimensions.")
        lineage = {"version": 2, "project_id": source.project_id, "acceptance_id": source.acceptance_id,
                   "scene_id": source.scene_id, "section_revision_id": source.section_revision_id,
                   "source_name": "final-image.png", "provenance": "final",
                   "source_artifact_id": source.artifact_id, "source_checksum": source.checksum,
                   "source_width": source.width, "source_height": source.height,
                   "target_profile": app_request.target_profile, "target_width": app_request.target_width,
                   "target_height": app_request.target_height, "native_model_scale": 4,
                   "native_width": native_width, "native_height": native_height,
                   "final_resize_method": "Lanczos" if resized else None,
                   "lineage_version": 2, "upscaler": {**capabilities.to_payload(), "diagnostics": result.metadata},
                   **measured}
        metadata = {"artifact_type": "scene_image", "project_id": source.project_id,
                    "scene_id": source.scene_id, "module_name": "desktop_image_final",
                    "scene_image": lineage, "image_final": {"version": 2, "fingerprint": fingerprint}}
        manifest = self.store.save_artifact("final-image.png", result.image_bytes, metadata)
        self.intake.select(manifest.artifact_id, expected_selection_id=selection_id)
        return manifest.artifact_id

    def create_final_selected(self, artifact_id, orientation, resolution):
        prepared = self.prepare_final(artifact_id, orientation, resolution)
        existing = self.cached_final(prepared)
        if existing:
            self.intake.select(existing, expected_selection_id=prepared[1])
            return existing
        return self.publish_final(prepared, self.provider.upscale(prepared[2]))

    def cached(self, source, fingerprint):
        for manifest in self.store.list_artifacts():
            if (manifest.artifact_type == "scene_image"
                    and manifest.metadata.get("image_upscale", {}).get("fingerprint") == fingerprint):
                image = self.images.image(manifest.artifact_id)
                if image.source_artifact_id == source.artifact_id and image.source_checksum == source.checksum:
                    return image.artifact_id
        return None

    def publish(self, prepared, result):
        source, selection_id, request, fingerprint, capabilities, factor = prepared
        if not isinstance(result, ImageUpscaleResult):
            raise ValueError("Upscaler must return the result contract.")
        if self.capabilities() != capabilities:
            raise ValueError("Upscaler identity changed during inference.")
        measured = decode_image(result.image_bytes, OUTPUT_LIMITS)
        target = ("PNG", request.target_width, request.target_height)
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
                   "source_name": f"Real-ESRGAN-x{factor}.png", "provenance": "upscaled",
                   "source_artifact_id": source.artifact_id, "source_checksum": source.checksum,
                   "source_width": source.width, "source_height": source.height, "scale": factor,
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
        source, _, request, fingerprint, _, _ = prepared
        existing = self.cached(source, fingerprint)
        if existing:
            self.intake.select(existing, expected_selection_id=selected.id)
            return existing
        return self.publish(prepared, self.provider.upscale(request))
