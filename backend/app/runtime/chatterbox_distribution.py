"""Reviewed, allowlisted D029 Windows/CUDA private-runtime distribution."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import PurePath
import re

from app.domain.dependencies import canonical_json
from .chatterbox_profile import MODEL_REVISION, SOURCE_REVISION
from .profiles import ProfileError


PROFILE_ID = "chatterbox-v3-cu124-windows-x64"
APPROVED_FINGERPRINT = "49e2830df17fe50ebad2155c55ba5c88a4d343fe347033e529e1092f35a416db"


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ProfileError("Chatterbox distribution has missing or unknown fields.")
    return value


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProfileError("Chatterbox distribution requires exact SHA-256 digests.")


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ProfileError("Chatterbox distribution sizes must be positive integers.")


def _filename(value):
    if (not isinstance(value, str) or PurePath(value).name != value
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+\-]*", value) is None):
        raise ProfileError("Chatterbox distribution artifact names must be safe basenames.")


@dataclass(frozen=True, slots=True)
class ChatterboxArtifact:
    filename: str
    sha256: str
    size_bytes: int
    unpacked_size_bytes: int | None = None

    def __post_init__(self):
        _filename(self.filename)
        _digest(self.sha256)
        _positive(self.size_bytes)
        if self.unpacked_size_bytes is not None:
            _positive(self.unpacked_size_bytes)


@dataclass(frozen=True, slots=True)
class ChatterboxPackage:
    name: str
    version: str
    artifact: ChatterboxArtifact


@dataclass(frozen=True, slots=True)
class ChatterboxDistribution:
    payload: dict
    fingerprint: str
    packages: tuple[ChatterboxPackage, ...]
    interpreter: ChatterboxArtifact
    native_runtime: ChatterboxArtifact
    auxiliary: ChatterboxArtifact

    @property
    def profile_id(self):
        return PROFILE_ID

    @property
    def package_versions(self):
        return {package.name: package.version for package in self.packages}

    def to_payload(self):
        return json.loads(canonical_json(self.payload))


def _artifact(value, *, wheel=False):
    required = {"filename", "sha256", "size_bytes"}
    if wheel:
        required.add("unpacked_size_bytes")
    if not isinstance(value, dict) or not required.issubset(value):
        raise ProfileError("Incomplete Chatterbox artifact pin.")
    return ChatterboxArtifact(value["filename"], value["sha256"], value["size_bytes"],
                              value.get("unpacked_size_bytes"))


def distribution_from_payload(value) -> ChatterboxDistribution:
    _fields(value, "schema_version profile_id profile_version target interpreter packages native_runtime "
                   "native_libraries auxiliary_assets model_contract health_check")
    if (value["schema_version"] != 1 or value["profile_id"] != PROFILE_ID
            or value["profile_version"] != "1.0.0"
            or value["target"] != {"os": "windows", "architecture": "x86_64", "device": "cuda:0",
                                    "minimum_compute_capability": "7.5", "cuda_runtime": "12.4"}
            or value["health_check"] != {"id": "chatterbox-v3-cu124-v1", "worker_protocol": 1}
            or value["model_contract"] != {"revision": MODEL_REVISION, "variant": "v3",
                                            "languages": ["en", "pl"], "sample_rate": 24000,
                                            "bundled": False}):
        raise ProfileError("Unsupported Chatterbox distribution contract.")
    interpreter = _fields(value["interpreter"], "implementation version abi artifact")
    if (interpreter["implementation"], interpreter["version"], interpreter["abi"]) != ("cpython", "3.11.9", "cp311"):
        raise ProfileError("Chatterbox requires the reviewed embedded CPython 3.11.9 ABI.")
    native = _fields(value["native_runtime"], "id version artifact required_libraries")
    if native["id"] != "msvc-v14-x64" or native["version"] != "14.44.35211.0":
        raise ProfileError("Chatterbox requires the reviewed native runtime.")
    if not isinstance(value["packages"], list) or len(value["packages"]) != 111:
        raise ProfileError("Chatterbox package closure is incomplete.")
    packages = []
    names = set()
    for item in value["packages"]:
        _fields(item, "name version artifact dependencies license")
        if (not isinstance(item["name"], str) or not isinstance(item["version"], str)
                or item["name"] in names or not isinstance(item["dependencies"], list)):
            raise ProfileError("Invalid Chatterbox package identity/closure.")
        names.add(item["name"])
        packages.append(ChatterboxPackage(item["name"], item["version"], _artifact(item["artifact"], wheel=True)))
    for item in value["packages"]:
        for dependency in item["dependencies"]:
            _fields(dependency, "name specifier")
            if dependency["name"] not in names:
                raise ProfileError("Chatterbox dependency closure is incomplete.")
        license_value = _fields(item["license"], "declared files")
        if not license_value["declared"] and not license_value["files"]:
            raise ProfileError("Every Chatterbox package requires license evidence.")
    identities = {package.name: package.version for package in packages}
    if (identities.get("chatterbox-tts") != "0.1.7"
            or identities.get("torch") != "2.6.0+cu124"
            or identities.get("torchaudio") != "2.6.0+cu124"
            or identities.get("spacy-pkuseg") != "1.0.1"):
        raise ProfileError("Chatterbox critical package identities differ from review.")
    chatterbox = next(item for item in value["packages"] if item["name"] == "chatterbox-tts")
    if chatterbox["artifact"].get("source_revision") != SOURCE_REVISION:
        raise ProfileError("Chatterbox source revision differs from review.")
    if not isinstance(value["native_libraries"], list) or not value["native_libraries"]:
        raise ProfileError("Chatterbox native-library inventory is missing.")
    for item in value["native_libraries"]:
        _fields(item, "path size_bytes sha256")
        _positive(item["size_bytes"])
        _digest(item["sha256"])
    auxiliary, = value["auxiliary_assets"]
    _fields(auxiliary, "id filename url sha256 size_bytes license_expression license_url")
    if auxiliary["id"] != "spacy-pkuseg-ontonotes-0.0.26":
        raise ProfileError("Unsupported Chatterbox tokenizer asset.")
    fingerprint = sha256(canonical_json(value).encode("utf-8")).hexdigest()
    if fingerprint != APPROVED_FINGERPRINT:
        raise ProfileError("Chatterbox distribution content is not in the approved allowlist.")
    return ChatterboxDistribution(json.loads(canonical_json(value)), fingerprint,
                                  tuple(sorted(packages, key=lambda item: item.name)),
                                  _artifact(interpreter["artifact"]), _artifact(native["artifact"]),
                                  _artifact(auxiliary))


def load_approved_chatterbox_distribution() -> ChatterboxDistribution:
    content = files("app.runtime").joinpath("chatterbox_gpu_windows_x64.json").read_text(encoding="utf-8")
    try:
        return distribution_from_payload(json.loads(content))
    except (ValueError, TypeError, KeyError) as exc:
        raise ProfileError("Invalid approved Chatterbox distribution.") from exc
