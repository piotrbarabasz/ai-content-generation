"""Immutable scene image measurements and explicit scene choice events."""

from dataclasses import asdict, dataclass


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("Image identities must be nonempty strings.")


@dataclass(frozen=True)
class SceneImage:
    artifact_id: str
    project_id: str
    acceptance_id: str
    scene_id: str
    section_revision_id: str
    source_name: str
    checksum: str
    size_bytes: int
    format: str
    width: int
    height: int
    mode: str
    orientation: int = 1
    provenance: str = "imported"

    def __post_init__(self):
        for value in (self.artifact_id, self.project_id, self.acceptance_id, self.scene_id,
                      self.section_revision_id, self.source_name, self.mode):
            _text(value)
        if any(c in self.source_name for c in "/\\:"):
            raise ValueError("Image source name must not expose a path.")
        if (self.format not in ("PNG", "JPEG") or self.provenance not in ("imported", "generated")
                or any(type(n) is not int or n <= 0 for n in (self.size_bytes, self.width, self.height))
                or type(self.orientation) is not int or self.orientation not in range(1, 9)
                or type(self.checksum) is not str or len(self.checksum) != 64
                or any(c not in "0123456789abcdef" for c in self.checksum)):
            raise ValueError("Invalid scene image measurements.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_manifest(cls, manifest):
        data = dict(manifest.metadata["scene_image"])
        if type(data.pop("version")) is not int or manifest.metadata["scene_image"]["version"] != 1:
            raise ValueError("Unsupported scene image version.")
        if manifest.artifact_type != "scene_image":
            raise ValueError("Expected a scene image artifact.")
        return cls(artifact_id=manifest.artifact_id, checksum=manifest.checksum,
                   size_bytes=manifest.size_bytes, **data)


@dataclass(frozen=True)
class ImageSelection:
    id: str
    project_id: str
    scene_id: str
    artifact_id: str
    parent_selection_id: str | None

    def __post_init__(self):
        for value in (self.id, self.project_id, self.scene_id, self.artifact_id):
            _text(value)
        if self.parent_selection_id is not None:
            _text(self.parent_selection_id)
            if self.id == self.parent_selection_id:
                raise ValueError("Image selection cannot be its own parent.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if type(data.pop("version")) is not int or value["version"] != 1:
            raise ValueError("Unsupported image selection version.")
        return cls(**data)
