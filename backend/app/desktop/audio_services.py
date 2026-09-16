"""D021 composition adapter. Qt widgets never import concrete audio providers."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256

from app.application.section_audio import SectionAudioService
from app.domain.section_audio import SectionAudio
from app.tts.assembly import inspect_pcm_wav
from app.tts.preview import TTSPreviewService
from app.tts.selection import map_catalog_selection
from app.tts.post_processing import TEMPO_PROCESSOR_VERSION


@dataclass(frozen=True)
class AudioChoice:
    label: str
    provider: str
    model: str
    voice: str
    language: str

    def selection(self):
        return {key: getattr(self, key) for key in ("provider", "model", "voice", "language")}


@dataclass(frozen=True)
class PlaybackAudio:
    payload: bytes
    stale: bool
    label: str


class AudioServices:
    """Construct on the coordinator thread with explicitly composed services.

    preview_builder must produce the same effective synthesis provider as voices;
    mismatch is rejected even on preview cache hits. It must bound its calls.
    supervisor must use production.complete as its completion handler.
    """

    def __init__(self, *, catalog, production, coordinator, supervisor, index, store,
                 preview_root, preview_builder, providers, settings=None):
        self.catalog, self.production = catalog, production
        self.coordinator, self.supervisor = coordinator, supervisor
        self.index, self.store = index, store
        self.preview_root, self.preview_builder = preview_root, preview_builder
        self.providers, self.settings = tuple(providers), deepcopy(settings or {})
        self.attempt = None
        self.canceled = False

    def choices(self, language):
        result = []
        for provider in self.catalog.filter(language=language, usage_policy="production").providers:
            if provider.id not in self.providers:
                continue
            for model in provider.models:
                for voice in model.voices:
                    if voice.voice_mode == "reference" or not voice.preview_supported:
                        continue
                    choice = AudioChoice(f"{provider.display_name} / {model.display_name} / {voice.display_name}",
                                         provider.id, model.id, voice.id, language)
                    self.selection(choice)  # Canonical compatibility and policy validation.
                    result.append(choice)
        return tuple(result)

    def selection(self, choice):
        selection = choice.selection()
        settings = deepcopy(self.settings.get(choice.provider, {}))
        map_catalog_selection(catalog=self.catalog, **selection, synthesis_settings=settings)
        return selection | {"settings": settings}

    def _preview(self, selection, text):
        prepared = self.production.voices.prepare(selection, 120)
        expected = prepared["effective_identity"]["synthesis"]
        build = self.preview_builder

        class CheckedProvider:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def effective_synthesis_identity(self, config):
                identity = self.wrapped.effective_synthesis_identity(config)
                if identity != expected:
                    raise ValueError("Preview and production synthesis identities differ.")
                return identity

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

        preview = TTSPreviewService(catalog=self.catalog, preview_root=self.preview_root,
                                    provider_builder=lambda config: CheckedProvider(build(config)))
        result = preview.synthesize_preview(
            **{key: selection[key] for key in ("provider", "model", "voice", "language")},
            synthesis_settings=selection["settings"], tempo=1.0, text=text)
        return PlaybackAudio(preview.read_audio(result.preview_id), False, "Voice preview")

    async def preview(self, choice, text):
        return await asyncio.to_thread(self._preview, self.selection(choice), text)

    async def generate(self, section, choice):
        selection = self.selection(choice)
        prepared = await asyncio.to_thread(self.production.voices.prepare, selection, 120)
        if self.canceled:
            return "Canceled before enqueue"
        jobs = self.coordinator.repository
        if jobs.paused or any(a.status in ("queued", "running")
                               for job in jobs.jobs() for a in jobs.attempts(job.id)):
            raise ValueError("Resolve pending or paused audio jobs before starting a new one.")

        class FrozenVoice:
            def prepare(self, selection, max_words):
                return deepcopy(prepared)

        production = SectionAudioService(self.production.publication, jobs, FrozenVoice(), self.production.outputs)
        self.attempt = production.enqueue(section, selection)
        try:
            await self.supervisor.run_next("desktop-audio")
            attempt = jobs.get_attempt(self.attempt.id)
            if attempt.status == "queued":
                self.coordinator.cancel(attempt.id)
                raise ValueError("Audio worker did not start; the queued request was canceled.")
            return f"{attempt.status}: {attempt.error}".rstrip(": ")
        finally:
            if jobs.get_attempt(self.attempt.id).status == "queued":
                self.coordinator.cancel(self.attempt.id)
            self.attempt = None

    def progress(self):
        if self.attempt is None:
            return "Preparing audio"
        attempt = self.coordinator.repository.get_attempt(self.attempt.id)
        progress = attempt.progress
        return str(attempt.status) if progress is None else (
            f"{progress.phase}: {progress.completed}/{progress.total}" if progress.total is not None else progress.phase)

    def cancel(self):
        self.canceled = True
        if self.attempt:
            self.coordinator.cancel(self.attempt.id)

    def playback(self, section, variant):
        if variant not in ("original", "processed"):
            raise ValueError("Choose original or processed audio explicitly.")
        heads = self.index.selected()
        suffix = "raw" if variant == "original" else "processed"
        artifact_id = heads.get(f"section:{section.section_id}:audio:{suffix}")
        if not artifact_id:
            raise ValueError(f"No selected {variant} audio for this section.")
        manifest = next(m for m in self.index.manifests() if m.artifact_id == artifact_id)
        audio = SectionAudio.from_manifest(manifest)
        if audio.section_id != section.section_id:
            raise ValueError("Audio selection belongs to another section.")
        payload = self.store.read_artifact(manifest.storage_key)
        measured, _ = inspect_pcm_wav(payload)
        if (sha256(payload).hexdigest() != audio.checksum or
                measured.to_payload() != manifest.metadata["section_audio"]["audio_parameters"]):
            raise ValueError("Retained audio failed checksum or measurement validation.")
        stale = audio.revision_id != section.id
        if variant == "processed":
            derivative = manifest.metadata["audio_derivative"]
            stale |= derivative["raw_artifact_id"] != heads.get(f"section:{section.section_id}:audio:raw")
            stale |= derivative["processor_version"] != TEMPO_PROCESSOR_VERSION
        return PlaybackAudio(payload, stale, f"{variant}: {'STALE — retained recording' if stale else 'current revision'}")
