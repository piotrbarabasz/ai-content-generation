"""Delivery mastering consumes frozen project-owned original or D068 media only."""

from dataclasses import asdict
from fractions import Fraction
from hashlib import file_digest, sha256
import shutil
from uuid import uuid4

from app.domain.delivery import DELIVERY_PRESETS
from app.domain.dependencies import content_fingerprint
from app.runtime.media_process import RenderCanceled
from app.storage.paths import contained_path


class DeliveryExportService:
    def __init__(self, session, artifacts, renders, renderer, *, finishing=None):
        self.session, self.artifacts, self.renders, self.renderer, self.finishing = session, artifacts, renders, renderer, finishing

    def source(self, kind):
        if kind == "original":
            manifest = self.renders.selected(verify_bytes=True)
            if manifest is None:
                raise ValueError("No selected verified original final MP4 is available.")
        elif kind == "branded":
            if self.finishing is None:
                raise ValueError("D068 branded source is unavailable.")
            config = self.finishing.branding.current()
            _, _, fingerprint = self.finishing.prepare(config)
            manifest = self.finishing.branding.cached(fingerprint) if config.enabled else None
            if manifest is None:
                raise ValueError("Build a current verified D068 branded video first; historical/stale outputs are not selected.")
        else:
            raise ValueError("Choose an original or branded project-owned source.")
        if manifest.metadata.get("project_id") != self.session.project.id:
            raise ValueError("Delivery source belongs to another project.")
        return manifest

    def prepare(self, config):
        source = self.source(config.source)
        identity = {"algorithm": "delivery-export-v1", "source_artifact_id": source.artifact_id,
                    "source_checksum": source.checksum, "source_size_bytes": source.size_bytes, "settings": config.to_payload(),
                    "preset": asdict(DELIVERY_PRESETS[config.preset]),
                    "renderer": self.renderer.identity() if self.renderer else {"adapter": "source-copy-v1"}}
        if config.preset != "source" and self.renderer is None:
            raise ValueError("Trusted FFmpeg/FFprobe is unavailable. Choose Source / Original or configure local media tools.")
        return source, identity, content_fingerprint(identity)

    async def build(self, config, *, canceled=lambda: False, progress=lambda *_: None):
        self.artifacts.save(config)
        source, identity, fingerprint = self.prepare(config)
        if canceled():
            raise RenderCanceled("Delivery export canceled before execution.")
        cached = self.artifacts.cached(fingerprint)
        if cached is not None:
            return cached
        evidence = source.metadata["render" if config.source == "original" else "finishing"]
        if config.preset == "source":
            report = {**evidence, "version": 1, "fully_decoded": bool(evidence.get("fully_decoded")),
                      "status": "SOURCE_COPY", "source_artifact_id": source.artifact_id, "source_checksum": source.checksum,
                      "preset": "source", "normalization_applied": False, "before": None, "after": None,
                      "video_bitrate": evidence.get("video_bitrate"), "audio_bitrate": evidence.get("audio_bitrate"),
                      "av_discrepancy_seconds": float(Fraction(*evidence["video_duration"]) - Fraction(*evidence["audio_duration"])),
                      "reasons": ["Byte-identical source copy. No re-encoding, normalization or new loudness analysis."]}
            with self.artifacts.store.open_artifact_id(source.artifact_id) as stream:
                return self._publish(stream, report, identity, fingerprint, canceled)
        root = contained_path(self.session.repository.workspace, "work/delivery/" + uuid4().hex)
        root.mkdir(parents=True)
        with self.artifacts.store.open_artifact_id(source.artifact_id) as stream, (root / "source.mp4").open("xb") as target:
            shutil.copyfileobj(stream, target, 1024 * 1024)
        with (root / "source.mp4").open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != source.checksum:
                raise ValueError("Delivery source changed while staging.")
        report = await self.renderer.export(root, evidence, config, canceled=canceled, progress=progress)
        if canceled():
            raise RenderCanceled("Delivery export canceled before publication.")
        if self.artifacts.current() != config or self.prepare(config)[1] != identity:
            raise ValueError("Delivery source or preferences changed during execution; retry current inputs.")
        with (root / "delivery.mp4").open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != report["checksum"]:
                raise ValueError("Delivery bytes changed after quality validation.")
            stream.seek(0)
            return self._publish(stream, report, identity, fingerprint, canceled)

    def _publish(self, stream, report, identity, fingerprint, canceled=lambda: False):
        if not report.get("fully_decoded"):
            raise ValueError("Delivery source/output lacks complete decode evidence.")

        class CheckedStream:
            def __init__(self):
                self.digest, self.size = sha256(), 0

            def read(self, count):
                if canceled():
                    raise RenderCanceled("Delivery canceled during atomic publication.")
                block = stream.read(count)
                self.digest.update(block)
                self.size += len(block)
                if not block and (self.digest.hexdigest() != report["checksum"] or self.size != report["size_bytes"]):
                    raise ValueError("Delivery bytes changed during atomic publication.")
                return block

        report = {**report, "source_artifact_id": identity["source_artifact_id"], "source_checksum": identity["source_checksum"],
                  "source_size_bytes": identity["source_size_bytes"], "encoder_identity": identity["renderer"], "settings": identity["settings"]}
        return self.artifacts.store.import_stream("delivery.mp4", CheckedStream(),
            {"artifact_type": "delivery_video", "module_name": "desktop_delivery", "project_id": self.session.project.id,
             "fingerprint": fingerprint, "identity": identity, "delivery": report})
