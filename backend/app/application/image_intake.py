"""Controlled local image intake; no decoder, filesystem or database imports."""

from typing import Protocol

from app.domain.base import new_id
from app.domain.scene_image import ImageSelection


class SceneImagesPort(Protocol):
    project_id: str
    def import_file(self, acceptance_id, scene_id, source, *, source_root=None): ...
    def image(self, artifact_id): ...
    def selected(self, scene_id): ...
    def save_selection(self, selection): ...


class ImageIntakeService:
    def __init__(self, images: SceneImagesPort):
        self.images = images

    def import_file(self, acceptance_id, scene_id, source, *, source_root=None):
        return self.images.import_file(acceptance_id, scene_id, source, source_root=source_root)

    def select(self, artifact_id, *, expected_selection_id):
        image = self.images.image(artifact_id)
        selection = ImageSelection(new_id("image_selection"), self.images.project_id,
                                   image.scene_id, image.artifact_id, expected_selection_id)
        self.images.save_selection(selection)
        return selection

    def import_and_select(self, acceptance_id, scene_id, source, *, expected_selection_id, source_root=None):
        image = self.import_file(acceptance_id, scene_id, source, source_root=source_root)
        selection = self.select(image.artifact_id, expected_selection_id=expected_selection_id)
        return image, selection

    def selected(self, scene_id):
        selection = self.images.selected(scene_id)
        return self.images.image(selection.artifact_id) if selection else None
