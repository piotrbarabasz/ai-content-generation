"""Project-owned imported media and immutable compare-and-select history."""

from hashlib import sha256
import json

from app.domain.base import new_id
from app.domain.dependencies import canonical_json
from app.domain.scene_image import ImageSelection, SceneImage
from .image_decoder import ImageLimits, decode_image
from .local_store import CHUNK_SIZE
from .paths import import_path
from .scene_plans import ProjectScenePlans


class ProjectSceneImages:
    def __init__(self, repository, store, *, limits=None):
        self.scenes = ProjectScenePlans(repository, store)
        self.repository, self.store, self.project_id = repository, store, self.scenes.project_id
        self.limits = limits if limits is not None else ImageLimits()

    def _scene(self, acceptance_id, scene_id, *, current):
        accepted = self.scenes.acceptance(acceptance_id)
        scene = next((s for s in accepted.plan.scenes if s.id == scene_id), None)
        if scene is None:
            raise ValueError("Image scene does not belong to the accepted project plan.")
        if current:
            self.scenes.current(self.repository.get_section(scene.revision_id))
        return scene

    def _publish(self, name, payload, kind, value_id, metadata):
        if any(m.metadata.get("value_id") == value_id for m in self.store.list_artifacts()):
            raise ValueError("Image publication identity already exists.")
        metadata = {"artifact_type": kind, "project_id": self.project_id, "value_id": value_id,
                    "module_name": "desktop_image_intake", **metadata}
        try:
            return self.store.save_artifact(name, payload, metadata)
        except Exception:
            # D004 may commit the index then fail private staging cleanup. Return
            # success only for this exact committed publication, never retry it.
            committed = None
            try:
                matches = [m for m in self.store.list_artifacts() if m.artifact_type == kind
                           and m.metadata.get("value_id") == value_id and m.metadata.get("project_id") == self.project_id]
                if len(matches) == 1 and matches[0].checksum == sha256(payload).hexdigest():
                    if self.store.read_artifact(matches[0].storage_key) == payload:
                        committed = matches[0]
            except Exception:
                pass
            if committed is not None:
                return committed
            raise

    def import_file(self, acceptance_id, scene_id, source, *, source_root=None):
        scene = self._scene(acceptance_id, scene_id, current=True)
        path = import_path(source, source_root)
        with path.open("rb") as stream:
            chunks, size = [], 0
            while size <= self.limits.max_bytes:
                chunk = stream.read(min(CHUNK_SIZE, self.limits.max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        payload = b"".join(chunks)
        measured = decode_image(payload, self.limits)
        # Recheck the editorial snapshot after potentially slow external input I/O.
        if self._scene(acceptance_id, scene_id, current=True) != scene:
            raise ValueError("Image scene changed during intake.")
        metadata = {"version": 1, "project_id": self.project_id, "acceptance_id": acceptance_id,
                    "scene_id": scene_id, "section_revision_id": scene.revision_id,
                    "source_name": path.name, "provenance": "imported", **measured}
        extension = ".png" if measured["format"] == "PNG" else ".jpg"
        manifest = self._publish("imported-image" + extension, payload, "scene_image", new_id("image_import"),
                                 {"scene_id": scene_id, "scene_image": metadata})
        return SceneImage.from_manifest(manifest)

    def image(self, artifact_id):
        matches = [m for m in self.store.list_artifacts() if m.artifact_id == artifact_id
                   and m.artifact_type == "scene_image" and m.metadata.get("project_id") == self.project_id]
        if len(matches) != 1:
            raise ValueError("Unknown image artifact in this project.")
        image = SceneImage.from_manifest(matches[0])
        scene = self._scene(image.acceptance_id, image.scene_id, current=False)
        if image.project_id != self.project_id or image.section_revision_id != scene.revision_id:
            raise ValueError("Image belongs to a different project scene revision.")
        limit = 64 * 1024 * 1024 if image.provenance == "upscaled" else self.limits.max_bytes
        with self.store.open_artifact_id(artifact_id) as stream:
            payload = stream.read(limit + 1)
        if len(payload) != image.size_bytes or len(payload) > limit or sha256(payload).hexdigest() != image.checksum:
            raise ValueError("Image bytes differ from retained measurements.")
        if image.provenance == "upscaled":
            source = self.image(image.source_artifact_id)
            if (source.checksum, source.width, source.height) != (image.source_checksum, image.source_width, image.source_height):
                raise ValueError("Upscaled source lineage differs from the retained source.")
        return image

    def history(self, scene_id):
        manifests = sorted(self.store.list_artifacts(), key=lambda m: (m.created_at, m.artifact_id))
        return tuple(self.image(m.artifact_id) for m in manifests
                     if m.artifact_type == "scene_image" and m.metadata.get("scene_id") == scene_id)

    def selection_history(self, scene_id):
        children = {}
        for manifest in self.store.list_artifacts():
            if manifest.artifact_type != "scene_image_selection" or manifest.metadata.get("scene_id") != scene_id:
                continue
            payload = self.store.read_artifact(manifest.storage_key)
            if sha256(payload).hexdigest() != manifest.checksum:
                raise ValueError("Image selection checksum mismatch.")
            event = ImageSelection.from_payload(json.loads(payload))
            image = self.image(event.artifact_id)
            if (event.project_id != self.project_id or event.scene_id != scene_id or image.scene_id != scene_id
                    or manifest.metadata.get("project_id") != self.project_id
                    or manifest.metadata.get("value_id") != event.id or event.parent_selection_id in children):
                raise ValueError("Invalid or branching image selection history.")
            children[event.parent_selection_id] = event
        chain, parent = [], None
        while parent in children:
            event = children.pop(parent)
            chain.append(event)
            parent = event.id
        if children:
            raise ValueError("Incomplete image selection history.")
        return tuple(chain)

    def selected(self, scene_id):
        chain = self.selection_history(scene_id)
        return chain[-1] if chain else None

    def save_selection(self, selection):
        image = self.image(selection.artifact_id)
        if selection.project_id != self.project_id or selection.scene_id != image.scene_id:
            raise ValueError("Image choice belongs to a different project or scene.")
        self._scene(image.acceptance_id, image.scene_id, current=True)
        previous = self.selected(image.scene_id)
        if selection.parent_selection_id != (previous.id if previous else None):
            raise ValueError("Active image selection changed; refresh before selecting.")
        self._publish("image-selection.json", canonical_json(selection.to_payload()).encode("utf-8"),
                      "scene_image_selection", selection.id, {"scene_id": image.scene_id})
