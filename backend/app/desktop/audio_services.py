"""D021 composition adapter. Qt widgets never import concrete audio providers."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from inspect import signature
import json

from app.application.section_audio import SectionAudioService
from app.domain.dependencies import DEPENDENCY_METADATA_KEY, DependencyDeclaration, canonical_json
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
    reference_audio_artifact_id: str | None = None
    reference_checksum: str | None = None
    approval_label: str | None = None

    def selection(self):
        value = {key: getattr(self, key) for key in ("provider", "model", "voice", "language")}
        if self.reference_audio_artifact_id is not None:
            value["reference_audio_artifact_id"] = self.reference_audio_artifact_id
            value["reference_audio_metadata"] = {
                "checksum": self.reference_checksum, "approval_label": self.approval_label, "approved": True}
        return value


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
        self.reference_audio = None
        self._prepared_identities = {}
        self.attempt = None
        self.canceled = False

    def choices(self, language):
        result = []
        for provider in self.catalog.filter(language=language, usage_policy="production").providers:
            if provider.id not in self.providers:
                continue
            for model in provider.models:
                for voice in model.voices:
                    if not voice.preview_supported:
                        continue
                    references = (self.reference_audio.approved() if voice.voice_mode == "reference"
                                  and self.reference_audio is not None else ())
                    candidates = references if voice.voice_mode == "reference" else (None,)
                    for reference in candidates:
                        suffix = "" if reference is None else f" / {reference.source_name} ({reference.approval_label})"
                        choice = AudioChoice(
                            f"{provider.display_name} / {model.display_name} / {voice.display_name}{suffix}",
                            provider.id, model.id, voice.id, language,
                            None if reference is None else reference.artifact_id,
                            None if reference is None else reference.checksum,
                            None if reference is None else reference.approval_label)
                        self.selection(choice)  # Canonical compatibility and policy validation.
                        result.append(choice)
        return tuple(result)

    def selection(self, choice):
        selection = choice.selection()
        settings = deepcopy(self.settings.get(choice.provider, {}))
        map_catalog_selection(catalog=self.catalog, **selection, synthesis_settings=settings)
        return selection | {"settings": settings}

    def configure_reference_audio(self, service):
        self.reference_audio = service
        return self

    def reference_audio_available(self):
        return self.reference_audio is not None

    def reference_sources(self):
        return () if self.reference_audio is None else self.reference_audio.sources()

    def reference_entries(self):
        if self.reference_audio is None:
            return ()
        return tuple((source, self.reference_audio.decision(source.artifact_id))
                     for source in self.reference_audio.sources())

    def import_reference(self, path):
        if self.reference_audio is None:
            raise ValueError("Reference-audio intake is not configured.")
        return self.reference_audio.import_file(path)

    def approve_reference(self, artifact_id, label):
        if self.reference_audio is None:
            raise ValueError("Reference-audio intake is not configured.")
        return self.reference_audio.approve(artifact_id, label)

    def reject_reference(self, artifact_id, label):
        if self.reference_audio is None:
            raise ValueError("Reference-audio intake is not configured.")
        return self.reference_audio.reject(artifact_id, label)

    def _resolved_reference(self, selection):
        artifact_id = selection.get("reference_audio_artifact_id")
        if artifact_id is None:
            return None
        if self.reference_audio is None:
            raise ValueError("Approved reference-audio storage is not configured.")
        resolved = self.reference_audio.resolve(artifact_id)
        if resolved is None:
            raise ValueError("Reference audio is not approved.")
        return resolved

    def _prepare_voice(self, selection, max_words, reference):
        prepare = self.production.voices.prepare
        if reference is not None:
            try:
                signature(prepare).bind(
                    selection, max_words, resolved_reference=reference)
            except (TypeError, ValueError):
                pass
            else:
                return prepare(selection, max_words, resolved_reference=reference)
        return prepare(selection, max_words)

    def _preview(self, selection, text, resolved_reference=None):
        prepared = self._prepare_voice(selection, 120, resolved_reference)
        self._prepared_identities[canonical_json(selection)] = deepcopy(prepared["effective_identity"])
        expected = prepared["effective_identity"]["synthesis"]
        build = self.preview_builder

        class CheckedProvider:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def effective_synthesis_identity(self, config):
                identity = self.wrapped.effective_synthesis_identity(prepared.get("voice_config", config))
                if identity != expected:
                    raise ValueError("Preview and production synthesis identities differ.")
                return identity

            def synthesize(self, text, config):
                return self.wrapped.synthesize(text, prepared.get("voice_config", config))

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

        def provider(config):
            try:
                signature(build).bind(config, prepared, resolved_reference)
            except (TypeError, ValueError):
                try:
                    signature(build).bind(config, prepared)
                except (TypeError, ValueError):
                    wrapped = build(config)
                else:
                    wrapped = build(config, prepared)
            else:
                wrapped = build(config, prepared, resolved_reference)
            return CheckedProvider(wrapped)

        reference_id = selection.get("reference_audio_artifact_id")
        resolver = None
        if resolved_reference is not None:
            resolver = lambda artifact_id: (
                resolved_reference if artifact_id == reference_id else None)
        preview = TTSPreviewService(catalog=self.catalog, preview_root=self.preview_root,
                                    provider_builder=provider,
                                    reference_artifact_resolver=resolver)
        result = preview.synthesize_preview(
            **{key: selection[key] for key in ("provider", "model", "voice", "language")},
            reference_audio_artifact_id=selection.get("reference_audio_artifact_id"),
            synthesis_settings=selection["settings"], tempo=1.0, text=text)
        return PlaybackAudio(preview.read_audio(result.preview_id), False, "Voice preview")

    async def preview(self, choice, text):
        selection = self.selection(choice)
        reference = self._resolved_reference(selection)
        return await asyncio.to_thread(self._preview, selection, text, reference)

    async def generate(self, section, choice):
        selection = self.selection(choice)
        reference = self._resolved_reference(selection)
        prepared = await asyncio.to_thread(self._prepare_voice, selection, 120, reference)
        self._prepared_identities[canonical_json(selection)] = deepcopy(prepared["effective_identity"])
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

    def _selection_stale(self, manifest, choice):
        if choice is None:
            return False
        payload = manifest.metadata.get(DEPENDENCY_METADATA_KEY)
        if payload is None:
            return True
        declaration = DependencyDeclaration.from_payload(payload)
        settings = json.loads(declaration.request.settings_json)
        selection = self.selection(choice)
        if canonical_json(settings.get("selection", {})) != canonical_json(selection):
            return True
        current_identity = self._prepared_identities.get(canonical_json(selection))
        return (current_identity is not None
                and declaration.request.effective_identity_json != canonical_json(current_identity))

    def playback(self, section, variant, choice=None):
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
        raw_manifest = manifest
        if variant == "processed":
            derivative = manifest.metadata["audio_derivative"]
            stale |= derivative["raw_artifact_id"] != heads.get(f"section:{section.section_id}:audio:raw")
            stale |= derivative["processor_version"] != TEMPO_PROCESSOR_VERSION
            raw_id = derivative["raw_artifact_id"]
            raw_manifest = next((item for item in self.index.manifests() if item.artifact_id == raw_id), None)
            stale |= raw_manifest is None
        if raw_manifest is not None:
            stale |= self._selection_stale(raw_manifest, choice)
        return PlaybackAudio(payload, stale, f"{variant}: {'STALE — retained recording' if stale else 'current revision'}")
