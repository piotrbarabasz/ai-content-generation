"""Application-wide managed GPU ownership, without importing a device runtime."""

from dataclasses import dataclass, replace
import json
import re
from threading import Lock
from uuid import uuid4

from app.domain.dependencies import RequestFingerprint, canonical_json


def _device(value):
    if not isinstance(value, str) or not re.fullmatch(r"cpu|cuda:(0|[1-9][0-9]*)", value):
        raise ValueError("Select cpu or an explicit cuda device index.")


@dataclass(frozen=True)
class DeviceDecision:
    """Trusted profile health evidence, supplied by composition before enqueue."""

    profile: str
    requested: str
    effective: str
    tested_devices: tuple[str, ...]
    fallback_approved: bool = False

    def __post_init__(self):
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise ValueError("A tested runtime profile identity is required.")
        _device(self.requested)
        _device(self.effective)
        if type(self.tested_devices) is not tuple or not self.tested_devices:
            raise ValueError("Explicit profile/device health evidence is required.")
        for device in self.tested_devices:
            _device(device)
        if type(self.fallback_approved) is not bool or self.effective not in self.tested_devices:
            raise ValueError("Effective device must have passed this profile's health check.")
        if self.requested != self.effective and not self.fallback_approved:
            raise ValueError("A device fallback requires an explicit decision.")

    def to_payload(self):
        return {"profile": self.profile, "requested": self.requested, "effective": self.effective,
                "tested_devices": sorted(set(self.tested_devices)), "fallback_approved": self.fallback_approved}

    def bind(self, request: RequestFingerprint):
        identity = json.loads(request.effective_identity_json)
        identity["runtime_device"] = self.to_payload()
        return replace(request, effective_identity_json=canonical_json(identity))

    def validate(self, request: RequestFingerprint):
        if json.loads(request.effective_identity_json).get("runtime_device") != self.to_payload():
            raise ValueError("Worker device/profile differs from the pinned request identity.")


@dataclass(frozen=True)
class GPULease:
    token: str
    owner: str
    device: str


@dataclass(frozen=True)
class GPUAvailability:
    """Scheduling availability, not a promise about free VRAM or installed CUDA."""

    available: bool
    owner: str | None = None
    device: str | None = None
    worker_pid: int | None = None
    quarantined: bool = False


class GPUResourceManager:
    """One GPU operation across projects, preview threads and device indices.

    The desktop composition uses APPLICATION_GPU_RESOURCES. Tests may inject an
    isolated instance. This is an application-process lease, not an OS-wide GPU
    scheduler; other programs/desktop processes are outside its scope.
    """

    def __init__(self):
        self._lock = Lock()
        self._lease = None
        self._process = None
        self._quarantined = False

    def availability(self):
        with self._lock:
            lease = self._lease
            return GPUAvailability(lease is None, lease.owner if lease else None,
                                   lease.device if lease else None,
                                   self._process.pid if self._process else None, self._quarantined)

    def acquire(self, owner: str, device: str):
        _device(device)
        if device == "cpu" or not isinstance(owner, str) or not owner.strip():
            raise ValueError("GPU lease needs an owner and an explicit GPU device.")
        with self._lock:
            if self._lease is not None:
                return None
            self._lease = GPULease(uuid4().hex, owner, device)
            return self._lease

    def _check(self, lease):
        if self._lease is None or lease is not self._lease:
            raise ValueError("GPU ownership token is stale or foreign.")

    def attach(self, lease, process):
        with self._lock:
            self._check(lease)
            if self._process is not None:
                raise ValueError("GPU lease already owns a worker.")
            self._process = process

    def quarantine(self, lease):
        with self._lock:
            self._check(lease)
            self._quarantined = True

    def release(self, lease):
        """Called by the supervisor only after process-tree and pipe cleanup."""
        with self._lock:
            self._check(lease)
            if self._process is not None and self._process.returncode is None:
                raise ValueError("GPU worker must exit before ownership can be released.")
            self._lease = self._process = None
            self._quarantined = False


APPLICATION_GPU_RESOURCES = GPUResourceManager()
