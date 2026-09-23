"""Create optional synchronized captions without constructing a provider."""


class SynchronizedCaptionService:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def export(self, timeline, alignment_ids, *, language=None, max_words=8,
               max_chars=42, max_duration_ms=4_000):
        return self.artifacts.create(
            timeline, alignment_ids, language=language, max_words=max_words,
            max_chars=max_chars, max_duration_ms=max_duration_ms)


__all__ = ["SynchronizedCaptionService"]
