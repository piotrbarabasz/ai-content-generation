"""D029 candidate contract; deliberately NOT an approved installable v1 profile.

The source revision is essential: the PyPI 0.1.7 wheel is not the V3 contract.
Discovery is pure Python. Only the private worker may check actual capabilities.
"""

from dataclasses import dataclass
from hashlib import sha256
import re

from app.domain.dependencies import canonical_json
from .resources import DeviceDecision


SOURCE_REVISION = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
PACKAGES = (
    ("chatterbox-tts", "0.1.7"), ("torch", "2.6.0+cu124"),
    ("torchaudio", "2.6.0+cu124"), ("resemble-perth", "1.0.1"),
    ("setuptools", "80.10.2"), ("transformers", "5.2.0"), ("numpy", "1.26.4"),
)
# Immutable upstream snapshot, not a mutable main/latest download.
MODEL_FILES = (
    ("ve.pt", 5698626, "4b16d836bc598509860f6fa068165a8bb5e9ac84f05582dfcf278a5a372879f1"),
    ("s3gen.pt", 1057165844, "9b9ff07e60b20c136e2b1b3d7563a24604e8d2c4c267888d1ee929dd0151d2a3"),
    ("t3_mtl23ls_v3.safetensors", 2143989928, "5abca8321ede76f8e61f1cc0d19aea6c946b28871017ce8726f8a69203f05953"),
    ("conds.pt", 107374, "6552d70568833628ba019c6b03459e77fe71ca197d5c560cef9411bee9d87f4e"),
    ("grapheme_mtl_merged_expanded_v1.json", 69989, "69632f47220a788a52ce2661d096453c5655e9bf25289d89a8d832c46ee07dbf"),
    ("Cangjie5_TC.json", 1920163, "7073fd9de919443ae88e0bd2449917a65fe54898a4413ed1edcc4b67f28bce8c"),
)


def candidate_profile():
    return {"schema_version": 1, "profile_id": "chatterbox-v3-cu124-windows-x64-candidate",
            "python": "3.11.9", "cuda": "12.4", "packages": dict(PACKAGES),
            "source_revision": SOURCE_REVISION, "model_revision": MODEL_REVISION,
            "model_repository": "ResembleAI/chatterbox", "model_variant": "v3",
            "model_license": "MIT", "languages": ["en", "pl"], "sample_rate": 24000,
            "files": {name: {"size": size, "sha256": digest} for name, size, digest in MODEL_FILES},
            "distribution_status": "pending-reviewed-private-runtime"}


def profile_fingerprint():
    return sha256(canonical_json(candidate_profile()).encode()).hexdigest()


@dataclass(frozen=True)
class ChatterboxHealth:
    """Result of the fixed private-worker probe, not a distribution trust decision."""

    profile: str
    device: str
    languages: tuple[str, ...]
    gpu_name: str
    total_memory: int

    def __post_init__(self):
        if self.profile != profile_fingerprint() or not re.fullmatch(r"cuda:(0|[1-9][0-9]*)", self.device):
            raise ValueError("Chatterbox requires the pinned profile and an explicit CUDA device.")
        if (type(self.languages) is not tuple or set(self.languages) != {"en", "pl"}
                or len(self.languages) != 2 or not isinstance(self.gpu_name, str) or not self.gpu_name.strip()
                or type(self.total_memory) is not int or self.total_memory <= 0):
            raise ValueError("Incomplete Chatterbox language/device health evidence.")

    def decision(self):
        return DeviceDecision(self.profile, self.device, self.device, (self.device,))

    def to_payload(self):
        return {"profile": self.profile, "device": self.device, "languages": sorted(self.languages),
                "gpu_name": self.gpu_name, "total_memory": self.total_memory}

    @classmethod
    def from_payload(cls, value):
        if not isinstance(value, dict) or set(value) != {"profile", "device", "languages", "gpu_name", "total_memory"}:
            raise ValueError("Invalid Chatterbox health response.")
        return cls(value["profile"], value["device"], tuple(value["languages"]), value["gpu_name"], value["total_memory"])
