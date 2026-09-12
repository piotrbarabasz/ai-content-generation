"""Immutable request identity and consumed-input declarations; no execution or I/O."""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from collections.abc import Mapping, Sequence

from app.domain.base import DomainValidationError


DEPENDENCY_METADATA_KEY = "desktop_dependencies"


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be non-empty text.")


def canonical_json(value) -> str:
    """Version-1 strict JSON: sorted object keys, ordered arrays, finite numbers.

    Preserve text and numeric types; never trim prose or coerce unknown values.
    This is a project format, not a claim of RFC 8785 interoperability.
    """
    def validate(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise DomainValidationError("Fingerprints require strict JSON with string keys and finite numbers.")

    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_fingerprint(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise DomainValidationError("Expected a lowercase SHA-256 fingerprint.")


def artifact_fingerprint(artifact_id: str, checksum: str) -> str:
    _text(artifact_id, "artifact_id")
    _digest(checksum)
    return content_fingerprint({"artifact_id": artifact_id, "checksum": checksum})


@dataclass(frozen=True, slots=True)
class InputEdge:
    """Named consumed input, bound to a logical source or selected output key."""

    name: str
    key: str
    fingerprint: str
    artifact_id: str | None = None

    def __post_init__(self):
        _text(self.name, "input name")
        _text(self.key, "input key")
        _digest(self.fingerprint)
        if self.artifact_id is not None:
            _text(self.artifact_id, "input artifact_id")

    @classmethod
    def artifact(cls, name: str, key: str, artifact_id: str, checksum: str) -> "InputEdge":
        return cls(name, key, artifact_fingerprint(artifact_id, checksum), artifact_id)

    def to_payload(self):
        return {"name": self.name, "key": self.key, "fingerprint": self.fingerprint, "artifact_id": self.artifact_id}


@dataclass(frozen=True, slots=True)
class RequestFingerprint:
    operation: str
    algorithm_version: str
    inputs: tuple[InputEdge, ...] = ()
    settings_json: str = "{}"
    effective_identity_json: str = "{}"

    def __post_init__(self):
        _text(self.operation, "operation")
        _text(self.algorithm_version, "algorithm_version")
        if not isinstance(self.inputs, (tuple, list)) or any(not isinstance(edge, InputEdge) for edge in self.inputs):
            raise DomainValidationError("Request inputs must be InputEdge values.")
        if len({edge.name for edge in self.inputs}) != len(self.inputs):
            raise DomainValidationError("Input binding names must be unique.")
        object.__setattr__(self, "inputs", tuple(sorted(self.inputs, key=lambda edge: edge.name)))
        for field in ("settings_json", "effective_identity_json"):
            value = json.loads(getattr(self, field))
            if not isinstance(value, dict):
                raise DomainValidationError("Settings and effective identity must be JSON objects.")
            object.__setattr__(self, field, canonical_json(value))

    @classmethod
    def create(cls, operation: str, algorithm_version: str, *, inputs: Sequence[InputEdge] = (),
               settings: Mapping | None = None, effective_identity: Mapping | None = None,
               relevant_settings: Sequence[str] | None = None) -> "RequestFingerprint":
        settings = dict(settings or {})
        if relevant_settings is not None:
            if (isinstance(relevant_settings, str)
                    or any(not isinstance(key, str) for key in relevant_settings)
                    or len(set(relevant_settings)) != len(relevant_settings)):
                raise DomainValidationError("Relevant settings must be unique field names.")
            if any(not isinstance(key, str) or key not in settings for key in relevant_settings):
                raise DomainValidationError("A declared relevant setting is missing.")
            settings = {key: settings[key] for key in relevant_settings}
        return cls(operation, algorithm_version, tuple(inputs), canonical_json(settings),
                   canonical_json(dict(effective_identity or {})))

    @property
    def fingerprint(self) -> str:
        return content_fingerprint(self.to_payload())

    def to_payload(self):
        return {"version": 1, "operation": self.operation, "algorithm_version": self.algorithm_version,
                "inputs": [edge.to_payload() for edge in self.inputs],
                "settings": json.loads(self.settings_json), "effective_identity": json.loads(self.effective_identity_json)}

    @classmethod
    def from_payload(cls, payload) -> "RequestFingerprint":
        if type(payload.get("version")) is not int or payload["version"] != 1:
            raise DomainValidationError("Unsupported request fingerprint format.")
        return cls.create(payload["operation"], payload["algorithm_version"],
                          inputs=[InputEdge(**edge) for edge in payload["inputs"]],
                          settings=payload["settings"], effective_identity=payload["effective_identity"])


class Provenance(StrEnum):
    GENERATED = "generated"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class DependencyDeclaration:
    output_key: str
    request: RequestFingerprint
    provenance: Provenance = Provenance.GENERATED

    def __post_init__(self):
        _text(self.output_key, "output key")
        if not isinstance(self.request, RequestFingerprint):
            raise DomainValidationError("A dependency declaration requires a request.")
        object.__setattr__(self, "provenance", Provenance(self.provenance))

    def to_metadata(self) -> dict:
        return {DEPENDENCY_METADATA_KEY: {"version": 1, "output_key": self.output_key,
                "request": self.request.to_payload(), "provenance": self.provenance.value}}

    @classmethod
    def from_payload(cls, payload) -> "DependencyDeclaration":
        if type(payload.get("version")) is not int or payload["version"] != 1:
            raise DomainValidationError("Unsupported dependency declaration format.")
        return cls(payload["output_key"], RequestFingerprint.from_payload(payload["request"]), payload["provenance"])


@dataclass(frozen=True, slots=True)
class ArtifactDependency:
    """Reference to existing indexed bytes and their declaration, not a new artifact."""

    artifact_id: str
    checksum: str
    declaration: DependencyDeclaration

    def __post_init__(self):
        _text(self.artifact_id, "artifact_id")
        _digest(self.checksum)
        if not isinstance(self.declaration, DependencyDeclaration):
            raise DomainValidationError("An artifact dependency requires a declaration.")

    @property
    def fingerprint(self) -> str:
        return artifact_fingerprint(self.artifact_id, self.checksum)


@dataclass(frozen=True, slots=True)
class FailedAttempt:
    attempt_id: str
    output_key: str
    request_fingerprint: str
    error: str

    def __post_init__(self):
        for field in ("attempt_id", "output_key", "error"):
            _text(getattr(self, field), field)
        _digest(self.request_fingerprint)
