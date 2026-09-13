"""Retained raw audio for one editorial revision; duration comes from PCM frames."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionAudio:
    artifact_id: str
    section_id: str
    revision_id: str
    checksum: str
    sample_rate: int
    frame_count: int

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
        return cls(manifest.artifact_id, data["section_id"], data["revision_id"], manifest.checksum,
                   pcm["sample_rate"], pcm["frame_count"])
