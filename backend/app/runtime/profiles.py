"""Strict, immutable runtime profile v1 values; no installation, imports or probes."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from urllib.parse import urlsplit


class ProfileError(ValueError):
    pass


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ProfileError("Profile object has missing or unknown fields.")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProfileError("Expected non-empty, unpadded text.")
    return value


def _version(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value) is None:
        raise ProfileError("Versions must be exact numeric releases, not ranges or latest.")
    return value


def _name(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None:
        raise ProfileError("Expected a normalized package/profile name.")
    return value


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProfileError("Expected a lowercase SHA-256 digest.")
    return value


def _url(value):
    _text(value)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port is not None or parsed.query or parsed.fragment or "\\" in value
                or any(ord(c) <= 32 for c in value)):
            raise ProfileError("Provenance/download URLs require credential-free HTTPS without query or fragment.")
    except ValueError as exc:
        raise ProfileError("Invalid profile URL.") from exc
    return parsed


@dataclass(frozen=True, slots=True)
class ArtifactPin:
    filename: str
    url: str
    sha256: str
    size_bytes: int
    source_url: str
    license_expression: str
    license_url: str

    def __post_init__(self):
        if not isinstance(self.filename, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.filename) is None:
            raise ProfileError("Artifact filename must be one safe basename.")
        location = _url(self.url)
        if location.hostname not in ("www.python.org", "files.pythonhosted.org", "download.visualstudio.microsoft.com") or location.path.rsplit("/", 1)[-1] != self.filename:
            raise ProfileError("Artifact URL must identify its exact file at an approved publisher.")
        _digest(self.sha256)
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ProfileError("Artifact size must be a positive integer.")
        _url(self.source_url)
        _text(self.license_expression)
        if self.license_expression.lower() in ("unknown", "none", "todo"):
            raise ProfileError("An explicit publisher license expression is required.")
        _url(self.license_url)

    def to_payload(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_payload(cls, value):
        return cls(**_fields(value, "filename url sha256 size_bytes source_url license_expression license_url"))


@dataclass(frozen=True, slots=True)
class PackagePin:
    name: str
    version: str
    artifact: ArtifactPin
    dependencies: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        _name(self.name)
        _version(self.version)
        if not isinstance(self.artifact, ArtifactPin):
            raise ProfileError("Package requires a pinned wheel artifact.")
        try:
            dependencies = tuple((_name(name), _version(version)) for name, version in self.dependencies)
        except (TypeError, ValueError) as exc:
            raise ProfileError("Invalid pinned dependency pairs.") from exc
        if len(dict(dependencies)) != len(dependencies) or self.name in dict(dependencies):
            raise ProfileError("Package dependencies must be unique and cannot reference self.")
        object.__setattr__(self, "dependencies", tuple(sorted(dependencies)))
        if not self.artifact.filename.startswith(f'{self.name.replace("-", "_")}-{self.version}-'):
            raise ProfileError("Wheel filename does not match package identity.")

    def to_payload(self):
        return {"name": self.name, "version": self.version, "artifact": self.artifact.to_payload(),
                "dependencies": dict(self.dependencies)}

    @classmethod
    def from_payload(cls, value):
        _fields(value, "name version artifact dependencies")
        if not isinstance(value["dependencies"], dict):
            raise ProfileError("Dependencies must map names to exact versions.")
        return cls(value["name"], value["version"], ArtifactPin.from_payload(value["artifact"]), tuple(value["dependencies"].items()))


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    os: str
    architecture: str
    devices: tuple[str, ...]

    def __post_init__(self):
        _text(self.os)
        _text(self.architecture)
        if not isinstance(self.devices, (tuple, list)) or not self.devices:
            raise ProfileError("Host device capabilities must be explicit.")
        object.__setattr__(self, "devices", tuple(_text(device) for device in self.devices))


@dataclass(frozen=True, slots=True)
class NativeRuntimePin:
    version: str
    artifact: ArtifactPin

    def __post_init__(self):
        _version(self.version)
        if not self.version.startswith("14.") or not isinstance(self.artifact, ArtifactPin):
            raise ProfileError("Native runtime requires an exact MSVC v14 artifact.")
        if self.artifact.filename != "VC_redist.x64.exe" or _url(self.artifact.url).hostname != "download.visualstudio.microsoft.com":
            raise ProfileError("Native runtime requires Microsoft's pinned x64 redistributable.")

    def to_payload(self):
        return {"id": "msvc-v14-x64", "version": self.version, "artifact": self.artifact.to_payload(),
                "required_libraries": ["msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"]}

    @classmethod
    def from_payload(cls, value):
        _fields(value, "id version artifact required_libraries")
        result = cls(value["version"], ArtifactPin.from_payload(value["artifact"]))
        if value["id"] != "msvc-v14-x64" or value["required_libraries"] != result.to_payload()["required_libraries"]:
            raise ProfileError("Unsupported native runtime requirement.")
        return result


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    profile_id: str
    profile_version: str
    python_version: str
    interpreter: ArtifactPin
    packages: tuple[PackagePin, ...]
    native_runtime: NativeRuntimePin

    def __post_init__(self):
        _name(self.profile_id)
        _version(self.profile_version)
        _version(self.python_version)
        if not re.fullmatch(r"3\.11\.[0-9]+", self.python_version):
            raise ProfileError("Profile v1 supports the CPython 3.11 worker ABI only.")
        if not isinstance(self.interpreter, ArtifactPin) or self.interpreter.filename != f"python-{self.python_version}-embed-amd64.zip":
            raise ProfileError("Profile requires the exact private Windows x64 embedded interpreter.")
        if not isinstance(self.native_runtime, NativeRuntimePin):
            raise ProfileError("Piper CPU profile requires explicit native-runtime identity.")
        if not isinstance(self.packages, (tuple, list)) or not self.packages or any(not isinstance(p, PackagePin) for p in self.packages):
            raise ProfileError("Profile packages must be explicit PackagePin values.")
        packages = {p.name: p for p in self.packages}
        if len(packages) != len(self.packages) or "piper-tts" not in packages:
            raise ProfileError("A Piper profile requires unique packages including piper-tts.")
        if len({p.artifact.filename for p in self.packages}) != len(self.packages):
            raise ProfileError("Package files must be unique.")
        for package in self.packages:
            self._check_wheel(package.artifact.filename)
            for name, version in package.dependencies:
                if name not in packages or packages[name].version != version:
                    raise ProfileError("Profile dependency closure is incomplete or contradictory.")
        visited, visiting = set(), set()
        def visit(name):
            if name in visiting:
                raise ProfileError("Profile dependency cycle.")
            if name in visited:
                return
            visiting.add(name)
            for dependency, _ in packages[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
        visit("piper-tts")
        if visited != set(packages):
            raise ProfileError("Profile contains packages unrelated to the Piper runtime closure.")
        object.__setattr__(self, "packages", tuple(sorted(self.packages, key=lambda p: p.name)))

    @staticmethod
    def _check_wheel(filename):
        # Deliberately bounded wheel subset for one target, not a package resolver.
        parts = filename.removesuffix(".whl").split("-")
        if not filename.endswith(".whl") or len(parts) != 5:
            raise ProfileError("Expected an unambiguous wheel filename.")
        python, abi, platform = parts[-3:]
        pure = platform == "any" and abi == "none" and "py3" in python.split(".")
        native = platform == "win_amd64" and (
            python == "cp311" and abi == "cp311" or abi == "abi3" and python in ("cp39", "cp310", "cp311"))
        if not (pure or native):
            raise ProfileError("Wheel is incompatible with CPython 3.11 / Windows x64.")

    def ensure_compatible(self, host: HostCapabilities):
        if not isinstance(host, HostCapabilities) or (host.os, host.architecture) != ("windows", "x86_64") or "cpu" not in host.devices:
            raise ProfileError("Piper CPU profile requires Windows x86_64 and CPU capability.")

    def to_payload(self):
        return {"schema_version": 1, "profile_id": self.profile_id, "profile_version": self.profile_version,
                "target": {"os": "windows", "architecture": "x86_64", "device": "cpu"},
                "interpreter": {"implementation": "cpython", "version": self.python_version, "abi": "cp311",
                                "artifact": self.interpreter.to_payload()},
                "packages": [p.to_payload() for p in self.packages],
                "native_runtime": self.native_runtime.to_payload(),
                "model_requirement": {"catalog": "curated-piper-voices", "required_for": "synthesis",
                                      "artifact_roles": ["onnx", "config"], "bundled": False},
                "health_check": {"id": "piper-cpu-imports-v1", "worker_protocol": 1}}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, value):
        _fields(value, "schema_version profile_id profile_version target interpreter packages native_runtime model_requirement health_check")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ProfileError("Unsupported runtime profile schema.")
        if value["target"] != {"os": "windows", "architecture": "x86_64", "device": "cpu"}:
            raise ProfileError("Unsupported profile target; v1 is Windows x64 CPU only.")
        interpreter = _fields(value["interpreter"], "implementation version abi artifact")
        if interpreter["implementation"] != "cpython" or interpreter["abi"] != "cp311":
            raise ProfileError("Unsupported interpreter implementation/ABI.")
        if value["model_requirement"] != {"catalog": "curated-piper-voices", "required_for": "synthesis",
                                         "artifact_roles": ["onnx", "config"], "bundled": False}:
            raise ProfileError("Piper model requirements must use the external curated catalog.")
        if type(value["model_requirement"].get("bundled")) is not bool:
            raise ProfileError("Bundled-model flag must be boolean.")
        health = _fields(value["health_check"], "id worker_protocol")
        if health["id"] != "piper-cpu-imports-v1" or type(health["worker_protocol"]) is not int or health["worker_protocol"] != 1:
            raise ProfileError("Unsupported fixed health-check contract.")
        if not isinstance(value["packages"], list):
            raise ProfileError("Packages must be a list.")
        return cls(value["profile_id"], value["profile_version"], interpreter["version"],
                   ArtifactPin.from_payload(interpreter["artifact"]), tuple(PackagePin.from_payload(p) for p in value["packages"]),
                   NativeRuntimePin.from_payload(value["native_runtime"]))

    @classmethod
    def from_json(cls, text: str):
        if not isinstance(text, str) or len(text.encode("utf-8")) > 256 * 1024:
            raise ProfileError("Profile JSON must be at most 256 KiB.")
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ProfileError("Duplicate profile field.")
                result[key] = value
            return result
        try:
            return cls.from_payload(json.loads(text, object_pairs_hook=pairs))
        except (ValueError, TypeError, RecursionError) as exc:
            raise ProfileError("Invalid runtime profile JSON/schema.") from exc


class HealthStatus(StrEnum):
    READY = "ready"
    NOT_INSTALLED = "not_installed"
    INCOMPATIBLE = "incompatible"
    FAILED = "failed"


class HealthCheck(StrEnum):
    INTERPRETER = "interpreter"
    PACKAGES = "packages"
    CPU_BACKEND = "cpu_backend"
    WORKER_PROTOCOL = "worker_protocol"


@dataclass(frozen=True, slots=True)
class HealthObservation:
    check: HealthCheck
    passed: bool
    detail: str

    def __post_init__(self):
        object.__setattr__(self, "check", HealthCheck(self.check))
        if type(self.passed) is not bool:
            raise ProfileError("Health observations require a boolean outcome.")
        _text(self.detail)


@dataclass(frozen=True, slots=True)
class ProfileHealth:
    profile_id: str
    profile_fingerprint: str
    status: HealthStatus
    checked_at: datetime
    observations: tuple[HealthObservation, ...] = ()

    def __post_init__(self):
        _name(self.profile_id)
        _digest(self.profile_fingerprint)
        object.__setattr__(self, "status", HealthStatus(self.status))
        if not isinstance(self.checked_at, datetime) or self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ProfileError("Health timestamps must be timezone-aware.")
        if not isinstance(self.observations, (list, tuple)) or any(not isinstance(o, HealthObservation) for o in self.observations):
            raise ProfileError("Health evidence must contain typed observations.")
        checks = {o.check for o in self.observations}
        if len(checks) != len(self.observations):
            raise ProfileError("Health observations must be unique.")
        if self.status == HealthStatus.READY and (checks != set(HealthCheck) or not all(o.passed for o in self.observations)):
            raise ProfileError("Ready requires all four successful health observations.")
        if self.status in (HealthStatus.FAILED, HealthStatus.INCOMPATIBLE) and not any(not o.passed for o in self.observations):
            raise ProfileError("Failure/incompatibility requires a failed observation.")
        if self.status == HealthStatus.NOT_INSTALLED and self.observations:
            raise ProfileError("Not-installed cannot claim runtime observations.")
        object.__setattr__(self, "observations", tuple(sorted(self.observations, key=lambda o: o.check)))

    def ensure_matches(self, profile: RuntimeProfile):
        if (self.profile_id, self.profile_fingerprint) != (profile.profile_id, profile.fingerprint):
            raise ProfileError("Health evidence belongs to a different profile revision.")

    def to_payload(self):
        return {"schema_version": 1, "profile_id": self.profile_id, "profile_fingerprint": self.profile_fingerprint,
                "status": self.status.value, "checked_at": self.checked_at.isoformat(),
                "observations": [{"check": o.check.value, "passed": o.passed, "detail": o.detail} for o in self.observations]}

    @classmethod
    def from_payload(cls, value):
        _fields(value, "schema_version profile_id profile_fingerprint status checked_at observations")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ProfileError("Unsupported health outcome schema.")
        if not isinstance(value["observations"], list):
            raise ProfileError("Health observations must be a list.")
        return cls(value["profile_id"], value["profile_fingerprint"], HealthStatus(value["status"]),
                   datetime.fromisoformat(value["checked_at"]),
                   tuple(HealthObservation(**_fields(o, "check passed detail")) for o in value["observations"]))
