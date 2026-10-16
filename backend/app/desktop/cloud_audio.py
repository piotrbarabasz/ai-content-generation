"""D078 cloud composition: owner-thread durable jobs, threaded synthesis and validation."""
import asyncio
import base64
from copy import copy
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import threading
from uuid import uuid4
from types import SimpleNamespace

from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
from app.desktop.audio_services import AudioChoice, AudioServices, PlaybackAudio
from app.domain.dependencies import DependencyDeclaration, canonical_json, content_fingerprint
from app.domain.narration import NarrationSettings
from app.domain.publication import PublicationSnapshot
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.openai_tts import OpenAITTSProvider, SpeechError, normalize_speech_wav
from app.providers.tts_result import TTSSynthesisResult
from app.runtime.section_synthesis import generate, validated_output
from app.storage.local_store import LocalArtifactStore
from app.storage.narration import ProjectNarration, PRICING, estimate_chunk
from app.storage.paths import contained_path
from app.storage.result_publication import ResultArtifactIndex
from app.tts.assembly import inspect_pcm_wav
from app.tts.chunking import sentence_chunks


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    with pending.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical_json(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(pending, path)


class CloudAudioServices(AudioServices):
    def __init__(self, session, *, provider=None, root=None):
        self.session = session
        jobs = JobRepository(session.repository)
        self.index = ResultArtifactIndex(session.repository, jobs)
        self.store = LocalArtifactStore(self.index.root, index=self.index)
        self.narration = ProjectNarration(session, self.store)
        self.provider = provider or OpenAITTSProvider()
        if self.narration.project() is None:
            self.narration.save_project(self.provider.settings)
        self.root = Path(root or contained_path(self.store.root, ".cloud-narration")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.coordinator = JobCoordinator(jobs)
        self.publication = ResultPublicationService(self.index, self.store)
        self.attempt = None
        self.canceled = False
        self._stop = threading.Event()
        self._prepared_identities = {}
        self._progress = "Ready"
        self._preview_active = False
        self._cache_only = False
        self.reference_audio = None
        # Existing cleanup/cancellation consumers have a provider-neutral unload port.
        self.supervisor = self

    async def unload(self):
        self.cancel()
        return True

    def choices(self, language):
        if not self.narration.migrated():
            return (AudioChoice("Retained narration — migrate explicitly before replacement", "retained", "legacy", "legacy", language),)
        s = self.narration.settings()
        return (AudioChoice(f"OpenAI / {s.model} / {s.voice} / {s.speed:.2f}x", "openai", s.model, s.voice, language),)

    def selection(self, choice):
        if choice.provider == "retained":
            return {"provider": "retained", "model": "legacy", "voice": "legacy", "language": choice.language, "settings": {}}
        s = self.narration.settings()
        return {"provider": "openai", "model": s.model, "voice": s.voice, "language": choice.language,
                "settings": s.to_payload()}

    def _selection_for(self, section_id, choice):
        s = self.narration.settings(section_id)
        return {"provider": "openai", "model": s.model, "voice": s.voice, "language": choice.language, "settings": s.to_payload()}

    def _selection_stale(self, manifest, choice):
        if choice is None or not self.narration.migrated():
            return False  # Retained WAV playback never needs the generating runtime.
        section_id = manifest.metadata["section_audio"]["section_id"]
        payload = manifest.metadata.get("desktop_dependencies")
        if payload is None:
            return True
        declaration = DependencyDeclaration.from_payload(payload)
        selection = self._selection_for(section_id, choice)
        identity = {"provider": "openai", "synthesis": self.provider.effective_synthesis_identity(selection["settings"])}
        return (json.loads(declaration.request.settings_json).get("selection") != selection
                or declaration.request.effective_identity_json != canonical_json(identity))

    def _prepared(self, section, choice):
        selection = self._selection_for(section.section_id, choice)
        config = selection["settings"]
        return {"selection": selection, "voice_config": config, "max_words": 40,
                "effective_identity": {"provider": "openai", "synthesis": self.provider.effective_synthesis_identity(config)}}

    def _scope(self, section, choice):
        return content_fingerprint({"section_id": section.section_id, "revision_id": section.id,
            "settings": self._selection_for(section.section_id, choice)})

    def estimate(self, sections, choice):
        result = {"sections": len(sections), "reuse": 0, "regenerate": 0, "characters": 0,
                  "estimated_seconds": 0., "low_usd": 0., "high_usd": 0., "scopes": [], "pricing": PRICING,
                  "model": self.narration.settings().model, "chunks": 0}
        for section in sections:
            try:
                playback = self.playback(section, "original", choice)
                if not playback.stale:
                    result["reuse"] += 1
                    continue
            except (ValueError, OSError, KeyError, StopIteration):
                pass
            if not self.narration.migrated():
                raise ValueError("Migrate explicitly to OpenAI in Audio before replacing historical narration.")
            result["regenerate"] += 1
            result["scopes"].append(self._scope(section, choice))
            settings = self.narration.settings(section.section_id)
            for chunk in sentence_chunks(section.text, max_words=40):
                self.provider.validate_text(chunk.text, settings.to_payload())
                result["chunks"] += 1
                value = estimate_chunk(chunk.text, settings)
                for key in ("characters", "estimated_seconds", "low_usd", "high_usd"):
                    result[key] += value[key]
        return result

    def approve_operation(self, estimate, budget, *, max_attempts=2):
        if budget < estimate["high_usd"]:
            raise ValueError("Operation budget is below its estimated upper cost; choose fewer sections or a larger budget.")
        return self.narration.approve(estimate["scopes"], budget, max_attempts=max_attempts)

    async def estimate_async(self, sections, choice):
        """Freeze owner-thread state; verify retained PCM without touching SQLite in a worker."""
        sections = tuple(sections)
        snapshot = copy(self)
        heads, manifests = self.index.selected(), tuple(self.index.manifests())
        settings = {None: self.narration.settings()}
        settings.update({s.section_id: self.narration.settings(s.section_id) for s in sections})
        migrated = self.narration.migrated()
        snapshot.index = SimpleNamespace(selected=lambda: heads, manifests=lambda: manifests)
        root, keys = self.store.root, {m.storage_key for m in manifests}
        def read_frozen(key):
            if key not in keys:
                raise FileNotFoundError(key)
            return contained_path(root, key).read_bytes()
        snapshot.store = SimpleNamespace(read_artifact=read_frozen)
        snapshot.narration = SimpleNamespace(settings=lambda section_id=None: settings[section_id], migrated=lambda: migrated)
        return await asyncio.to_thread(snapshot.estimate, sections, choice)

    def _bridge_provider(self, scope, loop):
        service, provider = self, self.provider

        class ApprovedProvider:
            provider_name = "openai"
            failure = None

            def effective_synthesis_identity(self, config):
                return provider.effective_synthesis_identity(config)

            def synthesize(self, text, config):
                settings = NarrationSettings.from_payload(config)
                identity = self.effective_synthesis_identity(config)
                key = content_fingerprint({"text": text, "identity": identity})
                path = contained_path(service.root, f"responses/{key}.json")
                def retain(payload):
                    value = {"version": 1, "key": key, "checksum": sha256(payload).hexdigest(),
                             "audio": base64.b64encode(payload).decode("ascii")}
                    # Keep every received response, including rejected audio, for review.
                    archive = contained_path(service.root, f"responses/{key}-response-{uuid4().hex}.json")
                    _atomic_json(archive, value)
                    _atomic_json(path, value)
                # An archive may have committed just before a crash lost the cache pointer.
                for candidate in (path, *path.parent.glob(f"{key}-response-*.json")):
                    try:
                        candidate = contained_path(service.root, f"responses/{candidate.name}")
                        value = json.loads(candidate.read_text(encoding="utf-8"))
                        payload = base64.b64decode(value["audio"], validate=True)
                        if value["version"] != 1 or value["key"] != key or sha256(payload).hexdigest() != value["checksum"]:
                            raise ValueError("Response cache identity/checksum mismatch")
                        normalized = normalize_speech_wav(payload, provider.ffmpeg)
                        pcm, _ = inspect_pcm_wav(normalized)
                        return TTSSynthesisResult(normalized, pcm.sample_rate, pcm.duration_seconds, "wav", "openai")
                    except (ValueError, KeyError, OSError):
                        # Preserve rejected bytes; never treat corrupt cache as paid success.
                        pass
                provider.response_sink = retain
                if service._cache_only:
                    raise ValueError("Preview cache is unavailable; approve a new estimated operation before synthesis.")
                for number in range(2):
                    if service._stop.is_set():
                        raise SpeechError("CANCELED")
                    async def reserve():
                        return service.narration.reserve(scope, key, estimate_chunk(text, settings))
                    try:
                        reservation = asyncio.run_coroutine_threadsafe(reserve(), loop).result()
                        result = provider.synthesize(text, config)
                        async def usage():
                            service.narration.save("usage", {"reservation_id": reservation["id"], "state": "received",
                                "measured_seconds": result.duration_seconds, "estimated_cost": estimate_chunk(text, settings)})
                        asyncio.run_coroutine_threadsafe(usage(), loop).result()
                        self.failure = None
                        return result
                    except Exception as exc:
                        message = str(exc) if isinstance(exc, (SpeechError, ValueError)) else "Cloud synthesis failed; response may be billed."
                        self.failure = message
                        async def failed():
                            service.narration.save("usage", {"scope": scope, "chunk_key": key, "state": "failed_or_uncertain", "message": message})
                        asyncio.run_coroutine_threadsafe(failed(), loop).result()
                        if not isinstance(exc, SpeechError) or not exc.retryable or number == 1:
                            service._stop.set()
                            raise
                        if service._stop.wait(2 ** number):
                            raise SpeechError("CANCELED")
                raise SpeechError("PAID_ATTEMPTS_EXHAUSTED")
        return ApprovedProvider()

    async def generate(self, section, choice):
        if not self.narration.migrated():
            raise ValueError("Historical audio is playback-only. Migrate explicitly before OpenAI replacement.")
        if not self._cache_only:
            self.provider.check_configuration()
        if self.attempt or self._preview_active:
            raise ValueError("Wait for the current cloud narration operation.")
        self._stop.clear(); self.provider.canceled.clear(); self.canceled = False
        prepared = self._prepared(section, choice)
        # Validate every chunk before enqueue or paid dispatch.
        for chunk in sentence_chunks(section.text, max_words=40):
            self.provider.validate_text(chunk.text, prepared["voice_config"])
        jobs = self.coordinator.repository
        previous = next((j for j in reversed(jobs.jobs()) if j.output_key == f"section:{section.section_id}:audio:raw"
            and j.request.settings_json == canonical_json(prepared) and PublicationSnapshot.from_job(j).sections == (section,)), None)
        class FrozenVoice:
            def prepare(self, *_): return prepared
        production = SectionAudioService(self.publication, jobs, FrozenVoice(), None)
        last = jobs.attempts(previous.id)[-1] if previous else None
        pending = [a for j in jobs.jobs() for a in jobs.attempts(j.id) if a.status in ("queued", "running")]
        if jobs.paused or pending and (last is None or pending != [last] or last.status != "queued"):
            raise ValueError("Resolve pending or paused audio jobs before starting cloud narration.")
        if last and last.status == "queued":
            queued = last
        elif last and last.status in ("failed", "canceled", "interrupted"):
            queued = self.coordinator.retry(previous.id)
        else:
            queued = production.enqueue(section, prepared["selection"], max_words=40)
        claim = self.coordinator.claim_next("desktop-cloud-audio")
        if claim is None or claim.id != queued.id:
            raise ValueError("Resolve queued/paused audio jobs before cloud synthesis.")
        self.attempt = claim
        job = jobs.get_job(claim.job_id)
        bridge = self._bridge_provider(self._scope(section, choice), asyncio.get_running_loop())
        try:
            def report(phase, done=None, total=None):
                self._progress = f"{phase}: {done}/{total}" if total else phase
            await asyncio.to_thread(generate, job, bridge, self.root / "jobs", report, self._stop.is_set)
            def validate():
                with validated_output(self.root / "jobs", job) as (stream, metadata):
                    return stream.read(), metadata
            payload, metadata = await asyncio.to_thread(validate)
            if self.canceled:
                self.coordinator.cancel(claim.id)
                self.coordinator.acknowledge_cancel(claim)
                return "canceled"
            result = self.publication.publish(claim, "section-audio.wav", io.BytesIO(payload), metadata={"section_audio": metadata})
            return "completed"
        except Exception as exc:
            if self.canceled:
                self.coordinator.cancel(claim.id)
                self.coordinator.acknowledge_cancel(claim)
                return "canceled"
            message = bridge.failure or str(exc)
            if jobs.get_attempt(claim.id).status == "running":
                self.coordinator.fail(claim, message)
            raise ValueError(message) from None
        finally:
            self.attempt = None

    def progress(self):
        return self._progress

    def cancel(self):
        self.canceled = True
        self._stop.set()
        self.provider.canceled.set()

    async def preview(self, choice, text, *, section=None):
        if self.attempt or self._preview_active:
            raise ValueError("Wait for the active audio operation.")
        self._preview_active = True
        try:
            return await self._preview_impl(choice, text, section=section)
        finally:
            self._preview_active = False

    async def _preview_impl(self, choice, text, *, section=None):
        self.provider.check_configuration()
        self._stop.clear(); self.provider.canceled.clear(); self.canceled = False
        settings = self.narration.settings(section.section_id if section else None)
        scope = self._scope(section, choice) if section else content_fingerprint({"preview": text, "settings": settings.to_payload()})
        bridge = self._bridge_provider(scope, asyncio.get_running_loop())
        chunks = sentence_chunks(text, max_words=40)
        for chunk in chunks:
            self.provider.validate_text(chunk.text, settings.to_payload())
        from app.tts.chunk_synthesis import ResumableChunkSynthesizer
        def report(done, total):
            self._progress = f"Preview chunks: {done}/{total}"
        result = await asyncio.to_thread(ResumableChunkSynthesizer(bridge, max_attempts=1).synthesize, chunks,
            runtime_dir=contained_path(self.root, "previews/" + scope), voice_config=settings.to_payload(),
            canceled=self._stop.is_set, progress=report)
        if not result.completed:
            raise ValueError(bridge.failure or "Preview canceled; completed chunks retained.")
        wav = result.final_wav.audio_bytes
        artifact = self.store.save_artifact("narration-preview.wav", wav, {"artifact_type": "narration_preview_audio"})
        record = self.narration.save("preview", {"artifact_id": artifact.artifact_id, "settings": settings.to_payload(),
            "section_id": section.section_id if section else None, "revision_id": section.id if section else None,
            "text_fingerprint": content_fingerprint(text), "measured_seconds": result.final_wav.duration_seconds})
        self.last_preview_id = record["id"]
        return PlaybackAudio(wav, False, f"AI-generated voice preview — {result.final_wav.duration_seconds:.2f}s")

    def estimate_preview(self, choice, text, section=None):
        settings = self.narration.settings(section.section_id if section else None)
        scope = self._scope(section, choice) if section else content_fingerprint({"preview": text, "settings": settings.to_payload()})
        values = []
        for chunk in sentence_chunks(text, max_words=40):
            self.provider.validate_text(chunk.text, settings.to_payload())
            values.append(estimate_chunk(chunk.text, settings))
        return {"sections": 1, "regenerate": 1, "reuse": 0, "scopes": [scope], "chunks": len(values), "model": settings.model,
            "pricing": PRICING, **{k: sum(v[k] for v in values) for k in ("characters", "estimated_seconds", "low_usd", "high_usd")}}

    async def approve_preview(self, preview_id, choice):
        record = next(v for v in self.narration.history("preview") if v["id"] == preview_id)
        section = self.session.active_script.section(record["section_id"])
        if section.id != record["revision_id"] or content_fingerprint(section.text) != record["text_fingerprint"]:
            raise ValueError("Preview is stale for the selected text; preview the current section first.")
        settings = NarrationSettings.from_payload(record["settings"])
        if settings.model != self.narration.settings().model:
            raise ValueError("Select the preview's model explicitly before approving this variant.")
        self.narration.save_overrides([section.section_id], {k: v for k, v in asdict(settings).items() if k != "model"})
        self.narration.save("variant_approval", {"preview_id": preview_id, "section_id": section.section_id, "revision_id": section.id})
        self._cache_only = True
        try:
            return await self.generate(section, choice)
        finally:
            self._cache_only = False

    def history_audio(self, section):
        values = []
        for m in self.index.manifests():
            data = m.metadata.get("section_audio", {})
            if data.get("section_id") == section.section_id:
                values.append((f"Retained audio {m.artifact_id[-8:]}", m.artifact_id, None))
        for v in self.narration.history("preview"):
            if v["section_id"] == section.section_id:
                s = NarrationSettings.from_payload(v["settings"])
                values.append((f"Preview {s.voice} {s.speed:.2f}x · {v['measured_seconds']:.2f}s", v["artifact_id"], v["id"]))
        return tuple(values)

    def retained_playback(self, artifact_id):
        manifest = next(m for m in self.index.manifests() if m.artifact_id == artifact_id)
        payload = self.store.read_artifact(manifest.storage_key)
        pcm, _ = inspect_pcm_wav(payload)
        if sha256(payload).hexdigest() != manifest.checksum or not pcm.frame_count:
            raise ValueError("Retained audio checksum/PCM validation failed.")
        return PlaybackAudio(payload, True, "Retained AI-generated narration — historical/preview variant")
