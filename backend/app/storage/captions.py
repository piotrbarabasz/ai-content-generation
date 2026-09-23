"""Immutable synchronized caption JSON, SRT and ASS project artifacts."""

from __future__ import annotations

from hashlib import sha256
import json

from app.domain.caption_track import (
    PublishedCaptionTrack,
    SynchronizedCaptionTrack,
    build_synchronized_caption_track,
    serialize_ass,
    serialize_srt,
)
from app.domain.dependencies import (
    DEPENDENCY_METADATA_KEY,
    DependencyDeclaration,
    InputEdge,
    RequestFingerprint,
    canonical_json,
    content_fingerprint,
)
from app.domain.timeline import TimelineRevision
from app.storage.speech_alignment import ProjectSpeechAlignments


class ProjectCaptionTracks:
    track_type = "desktop_caption_track"
    srt_type = "desktop_caption_srt"
    ass_type = "desktop_caption_ass"

    def __init__(self, repository, store):
        if store._index is None or store._index.repository is not repository:
            raise ValueError("Captions require the owning project artifact store.")
        self.repository, self.store = repository, store
        self.project_id = repository.project().id
        self.alignments = ProjectSpeechAlignments(repository, store)

    def create(self, timeline, alignment_ids, *, language=None, max_words=8,
               max_chars=42, max_duration_ms=4_000):
        if timeline.project_id != self.project_id:
            raise ValueError("Caption timeline belongs to another project.")
        if isinstance(alignment_ids, str):
            raise ValueError("Caption alignment IDs must be an explicit sequence.")
        alignment_ids = tuple(alignment_ids)
        active = self.repository.active_script()
        section_ids = tuple(dict.fromkeys(clip.media.section_id for clip in timeline.clips))
        sections = {section_id: active.section(section_id) for section_id in section_ids}
        values = tuple(self.alignments.alignment(value) for value in alignment_ids)
        alignments = {value.section_id: value for value in values}
        if len(values) != len(alignments):
            raise ValueError("Captions require one explicit alignment per selected section.")
        track = build_synchronized_caption_track(
            timeline, {key: value.text for key, value in sections.items()}, alignments,
            language=language, max_words=max_words, max_chars=max_chars,
            max_duration_ms=max_duration_ms)

        edges = [InputEdge("timeline", f"project:{self.project_id}:timeline",
                           content_fingerprint(timeline.to_payload()))]
        for section_id in section_ids:
            alignment = alignments[section_id]
            manifest = self._one(alignment.id, artifact_type="desktop_speech_alignment")
            edges.append(InputEdge.artifact(
                f"alignment:{section_id}", f"section:{section_id}:speech_alignment",
                manifest.artifact_id, manifest.checksum))
        request = RequestFingerprint.create(
            "captions.export", "1", inputs=edges,
            settings={"timeline": timeline.to_payload(), "language": track.language,
                      "max_words": max_words, "max_chars": max_chars,
                      "max_duration_ms": max_duration_ms},
            effective_identity={"method": track.method, "serializers": "srt-ass-v1"})
        dependency = DependencyDeclaration(
            f"timeline:{timeline.id}:captions", request).to_metadata()
        common = {"artifact_version": "1", "module_name": "desktop_captions",
                  "project_id": self.project_id, "value_id": track.id,
                  "timeline_id": timeline.id, "language": track.language,
                  "segment_count": len(track.segments), **dependency}
        srt = self.store.save_artifact(
            f"captions.{track.language}.srt", serialize_srt(track.segments).encode("utf-8"),
            {"artifact_type": self.srt_type, **common})
        ass = self.store.save_artifact(
            f"captions.{track.language}.ass", serialize_ass(track.segments).encode("utf-8"),
            {"artifact_type": self.ass_type, **common})
        payload = canonical_json(track.to_payload()).encode("utf-8")
        manifest = self.store.save_artifact(
            "synchronized-captions.json", payload,
            {"artifact_type": self.track_type, "srt_artifact_id": srt.artifact_id,
             "srt_checksum": srt.checksum, "ass_artifact_id": ass.artifact_id,
             "ass_checksum": ass.checksum, **common})
        return PublishedCaptionTrack(
            track, manifest.artifact_id, manifest.checksum,
            srt.artifact_id, srt.checksum, ass.artifact_id, ass.checksum)

    def _one(self, value_id, *, artifact_type):
        matches = [item for item in self.store.list_artifacts()
                   if item.artifact_type == artifact_type
                   and item.metadata.get("project_id") == self.project_id
                   and item.metadata.get("value_id") == value_id]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous caption dependency artifact.")
        return matches[0]

    def published(self, track_id):
        track_manifest = self._one(track_id, artifact_type=self.track_type)
        raw = self.store.read_artifact(track_manifest.storage_key)
        if sha256(raw).hexdigest() != track_manifest.checksum:
            raise ValueError("Caption track artifact checksum mismatch.")
        track = SynchronizedCaptionTrack.from_payload(json.loads(raw))
        srt = self._one(track_id, artifact_type=self.srt_type)
        ass = self._one(track_id, artifact_type=self.ass_type)
        expected = ((srt, serialize_srt(track.segments).encode("utf-8"), "srt"),
                    (ass, serialize_ass(track.segments).encode("utf-8"), "ass"))
        for manifest, payload, label in expected:
            if (self.store.read_artifact(manifest.storage_key) != payload
                    or sha256(payload).hexdigest() != manifest.checksum):
                raise ValueError(f"Caption {label.upper()} artifact differs from its track.")
        try:
            declarations = tuple(DependencyDeclaration.from_payload(
                manifest.metadata[DEPENDENCY_METADATA_KEY])
                for manifest in (track_manifest, srt, ass))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Caption dependency metadata is invalid.") from exc
        declaration = declarations[0]
        settings = json.loads(declaration.request.settings_json)
        timeline = TimelineRevision.from_payload(settings["timeline"])
        input_edges = {edge.name: edge for edge in declaration.request.inputs}
        alignment_edges = tuple(edge for name, edge in sorted(input_edges.items())
                                if name.startswith("alignment:"))
        common_matches = all(
            manifest.metadata.get("value_id") == track.id
            and manifest.metadata.get("timeline_id") == track.timeline_id
            and manifest.metadata.get("language") == track.language
            and manifest.metadata.get("segment_count") == len(track.segments)
            for manifest in (track_manifest, srt, ass))
        if (track.id != track_id
                or track.project_id != self.project_id
                or not common_matches
                or track_manifest.metadata.get("timeline_id") != track.timeline_id
                or track_manifest.metadata.get("srt_artifact_id") != srt.artifact_id
                or track_manifest.metadata.get("srt_checksum") != srt.checksum
                or track_manifest.metadata.get("ass_artifact_id") != ass.artifact_id
                or track_manifest.metadata.get("ass_checksum") != ass.checksum
                or declaration.output_key != f"timeline:{track.timeline_id}:captions"
                or declaration.request.operation != "captions.export"
                or declaration.request.algorithm_version != "1"
                or declarations[1:] != declarations[:1] * 2
                or timeline.id != track.timeline_id
                or timeline.project_id != track.project_id
                or settings.get("language") != track.language
                or input_edges.get("timeline") != InputEdge(
                    "timeline", f"project:{self.project_id}:timeline",
                    content_fingerprint(timeline.to_payload()))
                or len(alignment_edges) != len(track.alignment_ids)
                or {edge.artifact_id for edge in alignment_edges}
                != {self._one(value, artifact_type="desktop_speech_alignment").artifact_id
                    for value in track.alignment_ids}
                or json.loads(declaration.request.effective_identity_json) != {
                    "method": track.method, "serializers": "srt-ass-v1"}):
            raise ValueError("Caption index metadata differs from its immutable artifacts.")
        return PublishedCaptionTrack(
            track, track_manifest.artifact_id, track_manifest.checksum,
            srt.artifact_id, srt.checksum, ass.artifact_id, ass.checksum)

    def history(self, timeline_id=None):
        values = []
        for manifest in sorted(self.store.list_artifacts(),
                               key=lambda item: (item.created_at, item.artifact_id)):
            if (manifest.artifact_type == self.track_type
                    and manifest.metadata.get("project_id") == self.project_id
                    and (timeline_id is None
                         or manifest.metadata.get("timeline_id") == timeline_id)):
                values.append(self.published(manifest.metadata["value_id"]))
        return tuple(values)


__all__ = ["ProjectCaptionTracks"]
