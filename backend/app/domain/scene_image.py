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
    source_artifact_id: str | None = None
    source_checksum: str | None = None
    source_width: int | None = None
    source_height: int | None = None
    scale: int | None = None
    upscaler: dict | None = None
    lineage_version: int = 1
    target_profile: str | None = None
    target_width: int | None = None
    target_height: int | None = None
    native_model_scale: int | None = None
    native_width: int | None = None
    native_height: int | None = None
    final_resize_method: str | None = None
    delivery_width: int | None = None
    delivery_height: int | None = None
    overscan_policy: str | None = None
    master_width: int | None = None
    master_height: int | None = None

    def __post_init__(self):
        for value in (self.artifact_id, self.project_id, self.acceptance_id, self.scene_id,
                      self.section_revision_id, self.source_name, self.mode):
            _text(value)
        if any(c in self.source_name for c in "/\\:"):
            raise ValueError("Image source name must not expose a path.")
        if (self.format not in ("PNG", "JPEG", "WEBP") or self.provenance not in ("imported", "generated", "upscaled", "final", "motion_master")
                or any(type(n) is not int or n <= 0 for n in (self.size_bytes, self.width, self.height))
                or type(self.orientation) is not int or self.orientation not in range(1, 9)
                or type(self.checksum) is not str or len(self.checksum) != 64
                or any(c not in "0123456789abcdef" for c in self.checksum)):
            raise ValueError("Invalid scene image measurements.")
        if self.lineage_version not in (1, 2, 3):
            raise ValueError("Unsupported scene image lineage version.")
        if self.provenance == "upscaled" and self.lineage_version == 1:
            if (not self.source_artifact_id or not self.source_checksum or len(self.source_checksum) != 64
                    or type(self.source_width) is not int or type(self.source_height) is not int
                    or self.scale not in (2, 4) or not isinstance(self.upscaler, dict)
                    or self.width != self.source_width * self.scale
                    or self.height != self.source_height * self.scale):
                raise ValueError("Invalid upscaled image lineage.")
        if self.provenance == "final":
            if (self.lineage_version != 2 or not self.source_artifact_id or not self.source_checksum
                    or len(self.source_checksum) != 64 or self.target_profile not in ("fhd", "qhd", "uhd4k")
                    or any(type(n) is not int or n <= 0 for n in (self.source_width, self.source_height,
                           self.target_width, self.target_height, self.native_model_scale, self.native_width,
                           self.native_height)) or self.native_model_scale != 4
                    or (self.width, self.height) != (self.target_width, self.target_height)
                    or (self.native_width, self.native_height) != (self.source_width * 4, self.source_height * 4)
                    or self.final_resize_method not in (None, "Lanczos")):
                raise ValueError("Invalid final image lineage.")
        if self.provenance == "motion_master":
            if (self.lineage_version != 3 or not self.source_artifact_id or not self.source_checksum
                    or len(self.source_checksum) != 64 or self.target_profile not in ("fhd", "qhd", "uhd4k")
                    or any(type(n) is not int or n <= 0 for n in (self.source_width, self.source_height,
                           self.master_width, self.master_height, self.delivery_width, self.delivery_height,
                           self.native_model_scale, self.native_width, self.native_height))
                    or self.native_model_scale != 4 or (self.width, self.height) != (self.master_width, self.master_height)
                    or (self.native_width, self.native_height) != (self.source_width * 4, self.source_height * 4)
                    or self.overscan_policy != "5:4" or self.final_resize_method not in (None, "Lanczos")):
                raise ValueError("Invalid motion-master image lineage.")
            profile_dimensions = {"fhd": (1080, 1920), "qhd": (1440, 2560), "uhd4k": (2160, 3840)}
            resized = (self.native_width, self.native_height) != (self.master_width, self.master_height)
            if (tuple(sorted((self.delivery_width, self.delivery_height))) != profile_dimensions[self.target_profile]
                    or self.master_width * 4 != self.delivery_width * 5
                    or self.master_height * 4 != self.delivery_height * 5
                    or abs(self.source_width * self.delivery_height - self.source_height * self.delivery_width)
                    > self.delivery_height
                    or any(c not in "0123456789abcdef" for c in self.source_checksum)
                    or self.final_resize_method != ("Lanczos" if resized else None)
                    or not isinstance(self.upscaler, dict)
                    or any(type(self.upscaler.get(key)) is not str or not self.upscaler[key]
                           for key in ("provider", "model", "version", "runtime"))):
                raise ValueError("Motion-master profile, overscan or upscaler identity is inconsistent.")

    def to_payload(self):
        return {"version": self.lineage_version, **asdict(self)}

    @classmethod
    def from_manifest(cls, manifest):
        data = dict(manifest.metadata["scene_image"])
        version = data.pop("version")
        if type(version) is not int or version not in (1, 2, 3):
            raise ValueError("Unsupported scene image version.")
        data["lineage_version"] = version
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
