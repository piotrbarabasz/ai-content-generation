"""Retained raw audio for one editorial revision; duration comes from PCM frames."""

from dataclasses import dataclass

from .speech_boundary import SpeechBoundaryMap


@dataclass(frozen=True)
class SectionAudio:
    artifact_id: str
    section_id: str
    revision_id: str
    checksum: str
    sample_rate: int
    frame_count: int
    speech_boundary_map: SpeechBoundaryMap | None = None

    @property
    def duration_seconds(self):
        return self.frame_count / self.sample_rate

    @classmethod
    def from_manifest(cls, manifest):
        data = manifest.metadata["section_audio"]
        pcm = data["audio_parameters"]
        if (data["version"] != 1 or data["checksum"] != manifest.checksum or pcm["channels"] != 1
                or pcm["sample_width"] != 2 or pcm["compression_type"] != "NONE"
                or type(pcm["sample_rate"]) is not int or pcm["sample_rate"] <= 0
                or type(pcm["frame_count"]) is not int or pcm["frame_count"] <= 0
                or data["duration_seconds"] != pcm["frame_count"] / pcm["sample_rate"]):
            raise ValueError("Invalid SectionAudio measurement metadata.")
        boundary_map = (SpeechBoundaryMap.from_payload(data["speech_boundary_map"])
                        if "speech_boundary_map" in data else None)
        if boundary_map is not None and (boundary_map.audio_checksum, boundary_map.sample_rate, boundary_map.frame_count) != (
                manifest.checksum, pcm["sample_rate"], pcm["frame_count"]):
            raise ValueError("Speech map differs from SectionAudio measurements.")
        return cls(manifest.artifact_id, data["section_id"], data["revision_id"], manifest.checksum,
                   pcm["sample_rate"], pcm["frame_count"], boundary_map)
