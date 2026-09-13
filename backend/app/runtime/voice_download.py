"""On-demand curated Piper download: resumable bytes, verified atomic publication."""

import os
from http.client import HTTPException
import shutil
from uuid import uuid4

from app.providers.piper_catalog import get_piper_voice_catalog_entry
from .model_index import ModelIndex, _plain, asset_names, file_identity, validate_config, voice_fingerprint
from .profile_catalog import load_approved_profile
from .profiles import HealthStatus
from .provisioning import _json
from .voice_http import (
    DownloadInterrupted, VoiceDownloadError, VoiceHTTPTransport, inspect_remote, response_extent,
)


class PiperVoiceDownloader:
    """Blocking service; schedule off the UI thread. No retry loop or model loading."""

    def __init__(self, root, runtime, *, transport=None, free_bytes=None, reserve_bytes=64 * 1024 * 1024):
        self.index = ModelIndex(root)
        self.runtime = runtime
        self.transport = transport if transport is not None else VoiceHTTPTransport()
        self.free_bytes = free_bytes if free_bytes is not None else lambda path: shutil.disk_usage(path).free
        if type(reserve_bytes) is not int or reserve_bytes < 0:
            raise VoiceDownloadError("Disk reserve must be a non-negative byte count.")
        self.reserve = reserve_bytes

    def _entry(self, key, language_id):
        entry = get_piper_voice_catalog_entry(key)
        asset_names(entry)
        # The existing provider/selection contract uses "pl", while the curated
        # voice/config identify its locale as "pl_PL". Preserve that explicit alias.
        if language_id != entry.language_id and not (language_id == "pl" and entry.language_id == "pl_PL"):
            raise VoiceDownloadError("Selected language does not match the curated voice.")
        return entry

    def installed(self, key, *, language_id):
        return self.index.installed(self._entry(key, language_id))

    def _space(self, needed):
        if self.free_bytes(self.index.root) < needed + self.reserve:
            raise VoiceDownloadError("Insufficient free space for the voice and safety reserve.")

    def download(self, key, *, language_id, canceled=lambda: False, progress=lambda name, done, total: None):
        entry = self._entry(key, language_id)
        profile = load_approved_profile("piper-cpu-windows-x64", self.runtime.host)
        runtime = self.runtime.active(profile)
        if runtime is None or runtime.health.status != HealthStatus.READY:
            raise VoiceDownloadError("Install a healthy managed Piper runtime before downloading a voice.")
        with self.index.lock():
            existing = self.index.installed(entry)
            if existing is not None:
                return existing
            version = self.index.version_path(entry)
            if version.exists():
                # Recovery after directory publication but before active-pointer replace.
                if canceled():
                    raise DownloadInterrupted("Voice activation canceled; complete inactive version retained.")
                return self.index.activate(entry)
            downloads = self.index.root / "downloads"
            downloads.mkdir(exist_ok=True)
            _plain(downloads)
            work = downloads / voice_fingerprint(entry)
            work.mkdir(exist_ok=True)
            _plain(work)
            names = asset_names(entry)
            urls = entry.download_urls()
            remotes = []
            for i, path in enumerate(entry.required_files):
                if canceled():
                    raise DownloadInterrupted("Voice download canceled; retry to resume.")
                remotes.append(inspect_remote(self.transport, urls[path], 512 * 1024 * 1024 if i == 0 else 1024 * 1024))
            remaining = 0
            for name, remote in zip(names, remotes, strict=True):
                part = work / (name + ".part")
                if part.exists() or part.is_symlink():
                    _plain(part)
                    if part.stat().st_size > remote.size:
                        part.rename(work / (name + ".bad-" + uuid4().hex))
                remaining += remote.size - (part.stat().st_size if part.exists() else 0)
            self._space(remaining)
            identities = {}
            for name, path, remote in zip(names, entry.required_files, remotes, strict=True):
                part = work / (name + ".part")
                self._download_file(urls[path], part, remote, canceled, progress)
                identity = file_identity(part)
                if identity["md5"] != dict(entry.checksums)[path]:
                    part.rename(work / (name + ".bad-" + uuid4().hex))
                    raise VoiceDownloadError("Voice hash mismatch: " + name)
                identities[name] = identity
            # Assemble by renaming verified files only. Retained bad files never enter a version.
            candidate = self.index.root / ("candidate-" + uuid4().hex)
            candidate.mkdir()
            for name in names:
                os.replace(work / (name + ".part"), candidate / name)
            validate_config(candidate, entry)
            _json(candidate / "voice.json", {"schema_version": 1, "catalog": entry.to_catalog_payload(),
                                             "files": identities})
            version.parent.mkdir(exist_ok=True)
            _plain(version.parent)
            os.replace(candidate, version)
            if canceled():
                raise DownloadInterrupted("Voice activation canceled; complete inactive version retained.")
            return self.index.activate(entry)

    def _download_file(self, url, part, remote, canceled, progress):
        offset = part.stat().st_size if part.exists() else 0
        progress(part.name.removesuffix(".part"), offset, remote.size)
        while offset < remote.size:
            if canceled():
                raise DownloadInterrupted("Voice download canceled; retry to resume.")
            with self.transport.open(url, offset=offset, etag=remote.etag) as response:
                start, end, total = response_extent(response)
                if total != remote.size or (response.status == 206 and start != offset):
                    raise VoiceDownloadError("Voice range does not match the partial file.")
                if response.status == 200:
                    # Server ignored Range/If-Range: never append a full body to a prefix.
                    self._space(total)
                    offset = 0
                expected = end - start + 1
                self._space(expected)
                with part.open("wb" if offset == 0 else "ab") as output:
                    received = 0
                    while received < expected:
                        if canceled():
                            raise DownloadInterrupted("Voice download canceled; retry to resume.")
                        try:
                            data = response.read(min(256 * 1024, expected - received))
                        except (OSError, HTTPException):
                            raise DownloadInterrupted("Voice connection interrupted; retry to resume.") from None
                        if not data:
                            raise DownloadInterrupted("Voice response ended early; retry to resume.")
                        if len(data) > expected - received:
                            raise VoiceDownloadError("Voice response exceeds the declared range.")
                        self._space(len(data))
                        output.write(data)
                        received += len(data)
                        offset += len(data)
                        progress(part.name.removesuffix(".part"), offset, total)
                    output.flush()
                    os.fsync(output.fileno())
                if response.read(1):
                    raise VoiceDownloadError("Voice response exceeds its declared length.")
