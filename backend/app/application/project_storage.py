"""Storage report/preview values shared by coordinator adapters and desktop."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StorageFile:
    path: str
    size_bytes: int
    checksum: str
    category: str
    eligible: bool
    reason: str
    artifact_id: str | None = None


@dataclass(frozen=True)
class StorageReport:
    files: tuple[StorageFile, ...]

    @property
    def total_bytes(self):
        return sum(item.size_bytes for item in self.files)

    @property
    def categories(self):
        values = {}
        for item in self.files:
            values[item.category] = values.get(item.category, 0) + item.size_bytes
        return values

    @property
    def candidates(self):
        return tuple(item for item in self.files if item.eligible)

    @property
    def reclaimable_bytes(self):
        return sum(item.size_bytes for item in self.candidates)
