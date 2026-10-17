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
from app.domain.narration_recovery import NarrationPreflightError
from app.domain.publication import PublicationSnapshot
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.openai_tts import OpenAITTSProvider, SpeechError, normalize_speech_wav
from app.providers.tts_result import TTSSynthesisResult
from app.runtime.section_synthesis import generate, validated_output
from app.storage.local_store import LocalArtifactStore
from app.storage.narration import ProjectNarration, PRICING, estimate_chunk, attempt_evidence
from app.desktop.narration_recovery import (response_evidence, technical_evidence, seal_report,
    VALID_CACHED, RECOVERABLE_RESPONSE, ELIGIBLE_FOR_GENERATION, ATTEMPTS_EXHAUSTED, BLOCKED_CONFIGURATION)
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

    def _scan_chunks(self, section, choice, *, settings=None, scope=None, technical=True):
        settings = settings or self.narration.settings(section.section_id)
        config = settings.to_payload()
        identity = self.provider.effective_synthesis_identity(config)
        scope = scope or self._scope(section, choice)
        prepared = self._prepared(section, choice) if technical else None
        jobs = self.coordinator.repository.jobs() if technical else ()
        rows = []
        for chunk in sentence_chunks(section.text, max_words=40):
            key = content_fingerprint({'text': chunk.text, 'identity': identity})
            attempts, legacy, grant, additional = attempt_evidence(self.narration.history, scope, key)
            payload, responses = response_evidence(self.root, key, self.provider, text=chunk.text, identity=identity)
            cached, manifests = technical_evidence(self.root, jobs, section, prepared, chunk, self.provider) if technical else (None, [])
            category, reason = 'READY', 'An approved operation budget is required before dispatch.'
            state = VALID_CACHED if cached else RECOVERABLE_RESPONSE if payload else ELIGIBLE_FOR_GENERATION
            if not cached and not payload:
                outcomes = [u for a in attempts for u in a['outcomes']] + legacy
                failures = [u for u in outcomes if u.get('state') != 'received' and 'exhausted' not in u.get('message', '')]
                category = failures[-1].get('diagnostics', {}).get('category', 'UNKNOWN_PRIOR_FAILURE') if failures else 'NO_RETAINED_AUDIO'
                if len(attempts) >= 2 and not additional:
                    state = ATTEMPTS_EXHAUSTED
                    reason = ('Original and reviewed exceptional attempt exhausted; no further retry is authorized. Preserve evidence and review the underlying failure.' if grant else
                              'Original attempts exhausted; no verified retained audio. Review one exceptional attempt in Audio; a new ordinary budget cannot reset the counter.')
                try:
                    self.provider.validate_text(chunk.text, config)
                    self.provider.check_configuration()
                    from datetime import datetime, timezone
                    if datetime.now(timezone.utc).date().isoformat() >= PRICING['expires']:
                        raise ValueError('Speech pricing expired; update before any paid generation.')
                    if additional and grant['pricing'] != PRICING:
                        raise ValueError('Reviewed exceptional-attempt pricing changed; operator review is stale.')
                except ValueError as exc:
                    state, category, reason = BLOCKED_CONFIGURATION, getattr(exc, 'category', 'CONFIGURATION'), str(exc)
            if cached or payload:
                category, reason = 'VERIFIED_RETAINED_AUDIO', 'Verified retained bytes can be reused without another API call.'
            row = {'section_id': section.section_id, 'revision_id': section.id, 'section_title': section.title,
                'chunk_id': chunk.id, 'chunk_index': chunk.index, 'chunk_key': key, 'scope': scope,
                'state': state, 'category': category, 'reason': reason,
                'api_required': not bool(cached or payload), 'attempts': attempts, 'legacy_outcomes': legacy,
                'responses': responses, 'technical_manifests': manifests,
                'exceptional_retry': grant, 'exceptional_available': additional,
                'estimate': estimate_chunk(chunk.text, settings)}
            rows.append(row)
        return rows

    def recovery_scan(self, sections, choice):
        report = {'sections': len(sections), 'reuse': 0, 'regenerate': 0, 'chunk_statuses': [], 'retained_sections': [],
                  'model': self.narration.settings().model, 'pricing': PRICING}
        for section in sections:
            try:
                if not self.playback(section, 'original', choice).stale:
                    report['reuse'] += 1
                    report['retained_sections'].append({'section_id': section.section_id, 'revision_id': section.id,
                        'title': section.title, 'artifact_id': self.index.selected().get(f'section:{section.section_id}:audio:raw')})
                    continue
            except (ValueError, OSError, KeyError, StopIteration):
                pass
            if not self.narration.migrated():
                raise ValueError('Migrate explicitly to OpenAI before replacing historical narration.')
            report['regenerate'] += 1
            report['chunk_statuses'].extend(self._scan_chunks(section, choice))
        cap = (self.narration.project() or {}).get('project_budget')
        report['reserved_estimated_usd'] = sum(r['reserved_usd'] for r in self.narration.history('reservation'))
        report['project_budget'] = cap
        eligible = [r for r in report['chunk_statuses'] if r['state'] == ELIGIBLE_FOR_GENERATION]
        if cap is not None and report['reserved_estimated_usd'] + sum(r['estimate']['high_usd'] for r in eligible) > cap + 1e-12:
            for row in eligible:
                row.update(state=BLOCKED_CONFIGURATION, category='PROJECT_SPENDING_CEILING',
                           reason='Project spending ceiling cannot cover remaining estimated work; historical reservations remain counted.')
        return seal_report(report)

    def _estimate_report(self, report):
        if report['blocked']:
            raise NarrationPreflightError(report)
        result = {**report, 'characters': 0, 'estimated_seconds': 0., 'low_usd': 0., 'high_usd': 0.,
                  'scopes': [], 'chunks': len(report['chunk_statuses']), 'api_chunks': 0}
        for row in report['chunk_statuses']:
            if row['state'] != ELIGIBLE_FOR_GENERATION:
                continue
            result['api_chunks'] += 1
            if row['scope'] not in result['scopes']:
                result['scopes'].append(row['scope'])
            for key in ('characters', 'estimated_seconds', 'low_usd', 'high_usd'):
                result[key] += row['estimate'][key]
        return result

    def estimate(self, sections, choice):
        return self._estimate_report(self.recovery_scan(sections, choice))

    def approve_operation(self, estimate, budget, *, max_attempts=2):
        if estimate.get('blocked'):
            raise NarrationPreflightError(estimate)
        if budget < estimate['high_usd']:
            raise ValueError('Operation budget is below its estimated upper cost; choose fewer sections or a larger budget.')
        for row in estimate.get('chunk_statuses', []):
            if row['api_required'] and len(row['attempts']) >= max_attempts and not row['exceptional_available']:
                raise ValueError('Selected attempt limit cannot dispatch this chunk; approval was not saved.')
        return self.narration.approve(estimate['scopes'], budget, max_attempts=max_attempts)

    def authorize_exceptional_retry(self, report, choice, *, scope, chunk_key, operator, reason, budget, acknowledge):
        sections = tuple(self.session.active_script.sections)
        fresh = self.recovery_scan(sections, choice)
        return self._authorize_retry_report(report, fresh, scope=scope, chunk_key=chunk_key, operator=operator,
            reason=reason, budget=budget, acknowledge=acknowledge)

    async def authorize_exceptional_retry_async(self, report, choice, **review):
        fresh = await self.recovery_scan_async(self.session.active_script.sections, choice)
        if self.canceled:
            return None
        return self._authorize_retry_report(report, fresh, **review)

    def _authorize_retry_report(self, report, fresh, *, scope, chunk_key, operator, reason, budget, acknowledge):
        if self.attempt or self._preview_active:
            raise ValueError('Stop or finish the current narration operation before reviewing an exceptional retry.')
        if fresh['evidence_fingerprint'] != report['evidence_fingerprint']:
            raise ValueError('Recovery evidence changed; scan and review again before authorization.')
        row = next((r for r in fresh['chunk_statuses'] if (r['scope'], r['chunk_key']) == (scope, chunk_key)), None)
        if row is None or row['state'] != ATTEMPTS_EXHAUSTED or row['exceptional_retry']:
            raise ValueError('No exhausted chunk eligible for one exceptional reviewed attempt.')
        if budget < row['estimate']['high_usd']:
            raise ValueError('Exceptional attempt ceiling is below its estimated cost.')
        return self.narration.authorize_exceptional_retry(scope, chunk_key, operator=operator, reason=reason,
            budget=budget, evidence_fingerprint=fresh['evidence_fingerprint'], acknowledge=acknowledge)

    def _scan_snapshot(self, sections):
        snapshot = copy(self)
        heads, manifests = self.index.selected(), tuple(self.index.manifests())
        settings = {None: self.narration.settings()}
        settings.update({s.section_id: self.narration.settings(s.section_id) for s in sections})
        migrated, project = self.narration.migrated(), self.narration.project()
        histories = {k: self.narration.history(k) for k in ('reservation', 'usage', 'approval', 'exceptional_retry')}
        jobs = tuple(self.coordinator.repository.jobs())
        snapshot.coordinator = SimpleNamespace(repository=SimpleNamespace(jobs=lambda: jobs))
        snapshot.index = SimpleNamespace(selected=lambda: heads, manifests=lambda: manifests)
        root, keys = self.store.root, {m.storage_key for m in manifests}
        def read_frozen(key):
            if key not in keys:
                raise FileNotFoundError(key)
            return contained_path(root, key).read_bytes()
        snapshot.store = SimpleNamespace(read_artifact=read_frozen)
        snapshot.narration = SimpleNamespace(settings=lambda section_id=None: settings[section_id], migrated=lambda: migrated,
            history=lambda kind: histories[kind], project=lambda: project)
        return snapshot

    async def recovery_scan_async(self, sections, choice):
        sections = tuple(sections)
        return await asyncio.to_thread(self._scan_snapshot(sections).recovery_scan, sections, choice)

    async def estimate_async(self, sections, choice):
        return self._estimate_report(await self.recovery_scan_async(sections, choice))

    def _bridge_provider(self, scope, loop, section=None):
        service, provider = self, self.provider
        frozen_jobs = tuple(self.coordinator.repository.jobs())
        retained = len([k for k in self.index.selected() if k.endswith(':audio:raw')])
        prepared = self._prepared(section, self.choices(self.session.project.language)[0]) if section else None

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
                reservation = None
                def retain(payload, *, partial=False):
                    value = {"version": 2, "key": key, "checksum": sha256(payload).hexdigest(),
                        "audio": base64.b64encode(payload).decode("ascii"), "response_state": "partial" if partial else "complete",
                        "scope": scope, "text_hash": sha256(text.encode()).hexdigest(), "effective_identity": identity,
                        "reservation_id": reservation['id'] if reservation else None}
                    label = 'partial' if partial else 'response'
                    archive = contained_path(service.root, f"responses/{key}-{label}-{uuid4().hex}.json")
                    _atomic_json(archive, value)
                    if not partial:
                        _atomic_json(path, value)
                payload, _ = response_evidence(service.root, key, provider, text=text, identity=identity)
                if not payload and section:
                    chunk = next(c for c in sentence_chunks(section.text, max_words=40) if c.text == text)
                    payload, _ = technical_evidence(service.root, frozen_jobs, section, prepared, chunk, provider)
                if payload:
                    pcm, _ = inspect_pcm_wav(payload)
                    return TTSSynthesisResult(payload, pcm.sample_rate, pcm.duration_seconds, 'wav', 'openai')
                provider.response_sink = retain
                provider.partial_response_sink = lambda payload: retain(payload, partial=True)
                if service._cache_only:
                    raise ValueError('Preview cache is unavailable; review recovery before synthesis.')
                for number in range(2):
                    if service._stop.is_set():
                        raise SpeechError("CANCELED")
                    async def reserve():
                        return service.narration.reserve(scope, key, estimate_chunk(text, settings))
                    try:
                        reservation = asyncio.run_coroutine_threadsafe(reserve(), loop).result()
                    except Exception as exc:
                        self.failure = str(exc) if isinstance(exc, ValueError) else 'Reservation failed before dispatch; no provider request was made.'
                        service._stop.set()
                        raise
                    try:
                        result = provider.synthesize(text, config)
                        async def usage():
                            service.narration.save("usage", {"reservation_id": reservation["id"], "state": "received",
                                "measured_seconds": result.duration_seconds, "estimated_cost": estimate_chunk(text, settings)})
                        asyncio.run_coroutine_threadsafe(usage(), loop).result()
                        self.failure = None
                        return result
                    except Exception as exc:
                        diagnostics = exc.to_diagnostics() if isinstance(exc, SpeechError) else {
                            'category': 'APPLICATION_ERROR', 'phase': 'application', 'billing_uncertain': True}
                        message = str(exc) if isinstance(exc, SpeechError) else 'Application failure; receipt/billing not established.'
                        chunk_id = next((c.id for c in sentence_chunks(section.text, max_words=40) if c.text == text), key) if section else key
                        self.failure = (f"{section.title if section else 'Voice preview'} / {chunk_id} / {diagnostics['category']}: {message}\n"
                            f"Retained selected sections: {retained}. Completed chunks remain retained. Another API call may be required; "
                            'use Audio > Scan narration recovery. No automatic restart.')
                        async def failed():
                            service.narration.save("usage", {"scope": scope, "chunk_key": key, "state": "failed_or_uncertain",
                                "reservation_id": reservation['id'], "message": message, "diagnostics": diagnostics,
                                "billing": "UNKNOWN"})
                        asyncio.run_coroutine_threadsafe(failed(), loop).result()
                        if not isinstance(exc, SpeechError) or not exc.retryable or number == 1 or reservation.get('exceptional_retry_id'):
                            service._stop.set()
                            raise
                        if service._stop.wait(2 ** number):
                            raise SpeechError("CANCELED")
                raise SpeechError("PAID_ATTEMPTS_EXHAUSTED")
        return ApprovedProvider()

    async def generate(self, section, choice):
        if not self.narration.migrated():
            raise ValueError("Historical audio is playback-only. Migrate explicitly before OpenAI replacement.")
        if self.attempt or self._preview_active:
            raise ValueError("Wait for the current cloud narration operation.")
        self._stop.clear(); self.provider.canceled.clear(); self.canceled = False
        preflight = self._estimate_report(await self.recovery_scan_async((section,), choice))
        if self.canceled:
            return 'canceled'
        if not preflight['regenerate']:
            return 'completed'
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
        bridge = self._bridge_provider(self._scope(section, choice), asyncio.get_running_loop(), section)
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
        self._stop.clear(); self.provider.canceled.clear(); self.canceled = False
        await self.estimate_preview_async(choice, text, section)
        if self.canceled:
            raise SpeechError('CANCELED')
        settings = self.narration.settings(section.section_id if section else None)
        scope = self._scope(section, choice) if section else content_fingerprint({"preview": text, "settings": settings.to_payload()})
        bridge = self._bridge_provider(scope, asyncio.get_running_loop(), section)
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
        scope = self._scope(section, choice) if section else content_fingerprint({'preview': text, 'settings': settings.to_payload()})
        source = section or SimpleNamespace(section_id='voice-preview', id=scope, title='Voice preview', text=text)
        report = {'sections': 1, 'reuse': 0, 'regenerate': 1, 'model': settings.model, 'pricing': PRICING,
            'chunk_statuses': self._scan_chunks(source, choice, settings=settings, scope=scope, technical=section is not None)}
        cap = (self.narration.project() or {}).get('project_budget')
        used = sum(r['reserved_usd'] for r in self.narration.history('reservation'))
        if cap is not None and used + sum(r['estimate']['high_usd'] for r in report['chunk_statuses'] if r['api_required']) > cap + 1e-12:
            for row in report['chunk_statuses']:
                if row['api_required']:
                    row.update(state=BLOCKED_CONFIGURATION, category='PROJECT_SPENDING_CEILING', reason='Project spending ceiling prevents this preview.')
        return self._estimate_report(seal_report(report))

    async def estimate_preview_async(self, choice, text, section=None):
        snapshot = self._scan_snapshot((section,) if section else ())
        return await asyncio.to_thread(snapshot.estimate_preview, choice, text, section)

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
        for m in self.store.find_manifests(metadata={"section_audio.section_id": section.section_id}):
            data = m.metadata.get("section_audio", {})
            if data.get("section_id") == section.section_id:
                values.append((f"Retained audio {m.artifact_id[-8:]}", m.artifact_id, None))
        for v in self.narration.history("preview", section_id=section.section_id):
            if v["section_id"] == section.section_id:
                s = NarrationSettings.from_payload(v["settings"])
                values.append((f"Preview {s.voice} {s.speed:.2f}x · {v['measured_seconds']:.2f}s", v["artifact_id"], v["id"]))
        return tuple(values)

    def retained_playback(self, artifact_id):
        manifest = self.store.manifest_by_id(artifact_id)
        if manifest is None:
            raise ValueError("Retained narration artifact is not registered in this project.")
        payload = self.store.read_artifact(manifest.storage_key)
        pcm, _ = inspect_pcm_wav(payload)
        if sha256(payload).hexdigest() != manifest.checksum or not pcm.frame_count:
            raise ValueError("Retained audio checksum/PCM validation failed.")
        return PlaybackAudio(payload, True, "Retained AI-generated narration — historical/preview variant")
