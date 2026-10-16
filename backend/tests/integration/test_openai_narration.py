"""Cloud narration contracts, offline official-SDK-shaped transport and real storage."""
import asyncio
from dataclasses import replace
from io import BytesIO
import json
import threading
from types import SimpleNamespace
import wave

import pytest

from app.application.projects import ProjectSession
from app.desktop.cloud_audio import CloudAudioServices
from app.domain.narration import NarrationSettings
from app.domain.narrative_segment import SectionRevision
from app.providers.openai_tts import OpenAITTSProvider, SpeechError, normalize_speech_wav
from app.storage.project_repository import ProjectRepository
from app.tts.assembly import inspect_pcm_wav


def wav(seconds=.2, *, channels=1, width=2, rate=24000):
    stream = BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x01" * width * channels * round(seconds * rate))
    return stream.getvalue()


class SDK:
    def __init__(self):
        self.calls, self.options, self.threads = [], [], []
        self.failures = []
        self.hook = None
        self.payload = wav()

    def __call__(self, **options):
        self.options.append(options)
        return self

    def __enter__(self): return self
    def __exit__(self, *_): pass

    @property
    def audio(self): return self
    @property
    def speech(self): return self
    @property
    def with_streaming_response(self): return self

    def create(self, **payload):
        self.calls.append(payload)
        self.threads.append(threading.get_ident())
        if self.hook:
            self.hook(payload)
        if self.failures:
            error = self.failures.pop(0)
            if error:
                raise error
        return self

    def iter_bytes(self):
        yield self.payload[:100]
        yield self.payload[100:]


@pytest.fixture
def cloud(tmp_path):
    session = ProjectSession.create(tmp_path / "project", name="Cloud narration", language="en", repository_factory=ProjectRepository)
    old = session.active_script
    sections = tuple(SectionRevision.create(project_id=session.project.id, title=title, role="body",
        text=f"{title} explains the causes. Distinct consequences follow.") for title in ("First", "Second"))
    session.save_script(replace(old, id="script-new", parent_revision_id=old.id, sections=sections), expected_active_revision_id=old.id)
    sdk = SDK()
    provider = OpenAITTSProvider(client_factory=sdk, environment={"OPENAI_API_KEY": "private-fixture-key"})
    service = CloudAudioServices(session, provider=provider)
    yield session, service, sdk, sections
    session.close()


def approve(service, sections, choice, budget=1, attempts=2):
    estimate = service.estimate(sections, choice)
    return service.approve_operation(estimate, budget, max_attempts=attempts)


def test_exact_sdk_parameters_and_canonical_pcm():
    sdk = SDK()
    provider = OpenAITTSProvider(client_factory=sdk, environment={"OPENAI_API_KEY": "private"})
    settings = NarrationSettings(voice="marin", speed=.95, style="Historical", preset="Cinematic historical", instructions="Emphasize the river.", pacing="Measured")
    result = provider.synthesize("History matters.", settings.to_payload())
    call = sdk.calls[0]
    assert call["model"] == settings.model and call["voice"] == "marin" and call["speed"] == .95
    assert call["response_format"] == "wav" and call["instructions"] == settings.delivery
    assert sdk.options[0]["max_retries"] == 0
    pcm, _ = inspect_pcm_wav(result.audio_bytes)
    assert (pcm.sample_rate, pcm.channels, pcm.sample_width, pcm.frame_count) == (24000, 1, 2, 4800)
    assert result.duration_seconds == .2


@pytest.mark.parametrize("values", [{"model": "gpt-realtime-2.1-mini"}, {"voice": "unknown"}, {"speed": .2}, {"speed": float('nan')}, {"style": "unknown"}, {"instructions": "x" * 601}])
def test_invalid_controls_are_rejected(values):
    with pytest.raises(ValueError): NarrationSettings(**values)


def test_speed_and_instructions_are_request_identity_inputs():
    provider = OpenAITTSProvider()
    base = NarrationSettings()
    assert provider.effective_synthesis_identity(base.to_payload()) != provider.effective_synthesis_identity(replace(base, speed=.95).to_payload())
    assert provider.effective_synthesis_identity(base.to_payload()) != provider.effective_synthesis_identity(replace(base, instructions="Pause thoughtfully.").to_payload())


def test_budget_gate_and_threaded_publication_with_measured_boundaries(cloud):
    session, service, sdk, sections = cloud
    choice = service.choices("en")[0]
    with pytest.raises(ValueError, match="budget approval"):
        asyncio.run(service.generate(sections[0], choice))
    assert sdk.calls == []
    approve(service, sections, choice)
    assert asyncio.run(service.generate(sections[0], choice)) == "completed"
    assert all(t != threading.get_ident() for t in sdk.threads)
    from app.application.plan_duration import selected_narration
    audio = selected_narration(service, sections[0], choice, "original")
    assert audio.frame_count == 9600 and len(audio.speech_boundary_map.chunks) == 2
    assert service.playback(sections[0], "original", choice).stale is False
    retained = json.dumps([m.metadata for m in service.index.manifests()])
    assert "private-fixture-key" not in retained
    assert "private-fixture-key" not in json.dumps([j.to_payload() for j in service.coordinator.repository.jobs()])


def test_only_changed_section_settings_invalidate_audio_and_keep_script(cloud):
    session, service, sdk, sections = cloud
    choice = service.choices("en")[0]
    approve(service, sections, choice)
    for s in sections: asyncio.run(service.generate(s, choice))
    script, heads = session.active_script, service.index.selected()
    service.narration.save_overrides([sections[0].section_id], {"voice": "onyx", "speed": .95, "instructions": "Measured delivery."})
    assert service.playback(sections[0], "original", choice).stale
    assert not service.playback(sections[1], "original", choice).stale
    estimate = service.estimate(sections, choice)
    assert estimate["regenerate"] == 1 and estimate["reuse"] == 1
    service.approve_operation(estimate, 1)
    asyncio.run(service.generate(sections[0], choice))
    assert session.active_script == script
    assert service.index.selected()[f"section:{sections[1].section_id}:audio:raw"] == heads[f"section:{sections[1].section_id}:audio:raw"]


def test_restart_reuses_paid_success_and_resumes_only_missing_chunk(cloud):
    session, service, sdk, sections = cloud
    choice = service.choices("en")[0]
    sdk.failures = [None, RuntimeError("timeout"), RuntimeError("timeout")]
    approve(service, sections, choice)
    with pytest.raises(ValueError, match="uncertain"):
        asyncio.run(service.generate(sections[0], choice))
    assert len(sdk.calls) == 3
    root = session.repository.workspace
    session.close()
    reopened = ProjectSession.open(root, repository_factory=ProjectRepository)
    try:
        service2 = CloudAudioServices(reopened, provider=service.provider)
        # Paid attempts persist: a third attempt is blocked even with a fresh process.
        with pytest.raises(ValueError, match="exhausted"):
            asyncio.run(service2.generate(sections[0], choice))
        assert len(sdk.calls) == 3
    finally:
        reopened.close()


def test_preview_is_durable_and_preferred_variant_uses_cached_audio(cloud):
    _, service, sdk, sections = cloud
    choice = service.choices("en")[0]
    estimate = service.estimate_preview(choice, sections[0].text, sections[0])
    service.approve_operation(estimate, 1)
    result = asyncio.run(service.preview(choice, sections[0].text, section=sections[0]))
    assert "0.40s" in result.label and len(sdk.calls) == 2
    assert not service.index.selected()
    service.provider.environment = {}  # Cached approval must work offline.
    for pointer in (service.root / "responses").glob("*.json"):
        if "-response-" not in pointer.name:
            pointer.unlink()  # Simulate loss of the cache pointer after its archive committed.
    asyncio.run(service.approve_preview(service.last_preview_id, choice))
    assert len(sdk.calls) == 2
    assert not service.playback(sections[0], "original", choice).stale
    assert len(service.history_audio(sections[0])) == 2


def test_missing_key_does_not_spend_or_enqueue(cloud):
    _, service, sdk, sections = cloud
    service.provider.environment = {}
    with pytest.raises(ValueError, match="MISSING_API_KEY"):
        asyncio.run(service.generate(sections[0], service.choices("en")[0]))
    assert not service.narration.history("reservation") and not sdk.calls


def test_bad_or_truncated_wav_is_never_published():
    with pytest.raises(ValueError): normalize_speech_wav(b"invalid")
    with pytest.raises(ValueError): normalize_speech_wav(wav()[:-10])
    # Speech streaming may use an unknown RIFF/data length; rebuild a finite header.
    raw = bytearray(wav()); raw[4:8] = b"\xff"*4; raw[40:44] = b"\xff"*4
    assert inspect_pcm_wav(normalize_speech_wav(bytes(raw)))[0].frame_count == 4800


def test_project_ceiling_and_attempt_approval_are_durable(cloud):
    _, service, sdk, sections = cloud
    service.narration.save_project(NarrationSettings(), project_budget=.001)
    choice = service.choices("en")[0]
    approve(service, sections, choice)
    with pytest.raises(ValueError, match="budget exhausted"):
        asyncio.run(service.generate(sections[0], choice))
    assert not sdk.calls


def test_cancellation_retains_completed_audio_and_blocks_publication(cloud):
    _, service, sdk, sections = cloud
    choice = service.choices("en")[0]
    approve(service, sections, choice)
    sdk.hook = lambda _: service.cancel()
    assert asyncio.run(service.generate(sections[0], choice)) == "canceled"
    assert not service.index.selected()
    assert service.coordinator.repository.attempts(service.coordinator.repository.jobs()[0].id)[-1].status == "canceled"


def test_permanent_error_stops_then_restart_only_requests_missing_text(cloud):
    session, service, sdk, sections = cloud
    choice = service.choices("en")[0]
    approve(service, sections, choice)
    error = RuntimeError("unsafe provider detail private-fixture-key")
    error.status_code = 400
    sdk.failures = [None, error]
    with pytest.raises(ValueError, match="UNSUPPORTED_SPEECH_REQUEST"):
        asyncio.run(service.generate(sections[0], choice))
    root = session.repository.workspace
    session.close()
    reopened = ProjectSession.open(root, repository_factory=ProjectRepository)
    try:
        restored = CloudAudioServices(reopened, provider=service.provider)
        assert asyncio.run(restored.generate(sections[0], choice)) == "completed"
        assert [c["input"] for c in sdk.calls] == ["First explains the causes.", "Distinct consequences follow.", "Distinct consequences follow."]
        manifest = next(m for m in restored.index.manifests() if m.metadata.get("section_audio"))
        assert manifest.metadata["section_audio"]["reused_chunks"] == 1
        assert "private-fixture-key" not in json.dumps(restored.narration.history("usage"))
    finally:
        reopened.close()


def test_paid_download_survives_crash_before_chunk_manifest(cloud, monkeypatch):
    _, service, sdk, sections = cloud
    choice = service.choices("en")[0]
    approve(service, sections, choice)
    from app.tts.chunk_synthesis import ResumableChunkSynthesizer
    def crashed(synth, chunk, config, record, root):
        synth._provider.synthesize(chunk.text, config)
        raise RuntimeError("simulated crash after paid response")
    with monkeypatch.context() as patch:
        patch.setattr(ResumableChunkSynthesizer, "_synthesize_chunk", crashed)
        with pytest.raises(ValueError, match="simulated crash"):
            asyncio.run(service.generate(sections[0], choice))
    assert len(sdk.calls) == 1
    assert asyncio.run(service.generate(sections[0], choice)) == "completed"
    assert len(sdk.calls) == 2  # no repeated first paid request


def test_legacy_wav_playback_without_runtime_and_explicit_migration(tmp_path, monkeypatch):
    from app.application.result_publication import ResultPublicationService
    from app.domain.dependencies import RequestFingerprint
    from app.jobs.coordinator import JobCoordinator
    from app.jobs.repository import JobRepository
    from app.storage.local_store import LocalArtifactStore
    from app.storage.result_publication import ResultArtifactIndex
    import hashlib, sys
    monkeypatch.setitem(sys.modules, "torch", None)
    with ProjectSession.create(tmp_path/'legacy', name="Legacy", language="en", repository_factory=ProjectRepository) as session:
        section = SectionRevision.create(project_id=session.project.id, title="Legacy", role="body", text="Historical narration.")
        old = session.active_script
        session.save_script(replace(old, id="legacy-script", parent_revision_id=old.id, sections=(section,)), expected_active_revision_id=old.id)
        jobs = JobRepository(session.repository); index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index); publication = ResultPublicationService(index, store)
        queued = publication.enqueue(f"section:{section.section_id}:audio:raw", RequestFingerprint.create("legacy.audio", "1",
            settings={"selection": {"provider": "chatterbox_v3"}}), expected_sections={section.section_id: section.id})
        claim = JobCoordinator(jobs).claim_next("legacy-fixture")
        payload = wav(); pcm, _ = inspect_pcm_wav(payload)
        publication.publish(claim, "legacy.wav", BytesIO(payload), metadata={"section_audio": {"version": 1,
            "section_id": section.section_id, "revision_id": section.id, "checksum": hashlib.sha256(payload).hexdigest(),
            "audio_parameters": pcm.to_payload(), "duration_seconds": pcm.duration_seconds}})
        service = CloudAudioServices(session)
        choice = service.choices("en")[0]
        assert choice.provider == "retained" and not service.narration.migrated()
        assert service.playback(section, "original", choice).payload == payload
        with pytest.raises(ValueError, match="Migrate explicitly|migrate explicitly"):
            asyncio.run(service.generate(section, choice))
        service.narration.save_project(NarrationSettings(), migrate=True)
        assert service.playback(section, "original", service.choices("en")[0]).stale
        assert service.retained_playback(index.selected()[f"section:{section.section_id}:audio:raw"]).payload == payload


def test_long_input_is_rejected_before_sdk_or_spending(cloud):
    _, service, sdk, sections = cloud
    section = sections[0].revise(text="a" * 4097)
    with pytest.raises(ValueError, match="INPUT_LIMIT"):
        asyncio.run(service.generate(section, service.choices("en")[0]))
    assert sdk.calls == [] and not service.narration.history("reservation")


def test_controls_persist_through_ui_reopen_and_timer_runs_during_request(cloud, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from app.desktop.audio_panel import AudioPanel
    from app.desktop.narration_panel import NarrationCostDialog
    session, service, sdk, sections = cloud
    qt = QApplication.instance() or QApplication([])
    widget = AudioPanel(); widget.bind(service, "en"); widget.select_section(sections[0])
    controls = widget.narration_controls
    controls.speed.setValue(19); controls.voice.setCurrentText("marin"); controls.preset.setCurrentText("Cinematic historical")
    controls.instructions.setPlainText("A quiet, thoughtful voice.")
    script = session.active_script
    controls.save_project()
    assert service.narration.settings().speed == .95 and controls.speed_value.text() == "0.95x"
    controls.speed.setValue(21); controls.save_selected()
    assert service.narration.settings(sections[0].section_id).speed == 1.05
    assert service.narration.settings(sections[1].section_id).speed == .95
    assert session.active_script == script
    release = threading.Event()
    sdk.hook = lambda _: release.wait(5)
    def accept(dialog):
        dialog.budget.setValue(1)
        QTimer.singleShot(0, dialog.accept)
    monkeypatch.setattr(NarrationCostDialog, "open", accept)
    ticks = []
    timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: ticks.append(True)); timer.start()
    widget.preview()
    try:
        for _ in range(20): QTest.qWait(10)
        assert widget.busy and len(ticks) > 5
        widget.cancel(); release.set()
        for _ in range(400):
            QTest.qWait(10)
            if not widget.busy: break
        assert not widget.busy
        widget.bind(service, "en"); widget.select_section(sections[1])
        assert controls.speed_value.text() == "0.95x"
    finally:
        release.set(); timer.stop(); widget.close()

@pytest.mark.parametrize('status,category,retryable', [(401,'AUTHENTICATION_FAILED',False),(400,'UNSUPPORTED_SPEECH_REQUEST',False),(404,'MODEL_UNAVAILABLE',False),(429,'RATE_LIMIT',True)])
def test_safe_http_error_categories(status, category, retryable):
    sdk=SDK()
    error=RuntimeError('private-fixture-key must never be persisted')
    error.status_code=status
    sdk.failures=[error]
    provider=OpenAITTSProvider(client_factory=sdk,environment={'OPENAI_API_KEY':'private-fixture-key'})
    with pytest.raises(SpeechError) as caught:
        provider.synthesize('A brief sentence.')
    assert caught.value.category==category and caught.value.retryable is retryable
    assert 'private-fixture-key' not in str(caught.value)


def test_persistence_failure_does_not_retry_a_received_paid_response(cloud):
    from unittest.mock import patch
    _,service,sdk,sections=cloud
    choice=service.choices('en')[0]
    approve(service,sections[:1],choice)
    with patch('app.desktop.cloud_audio._atomic_json',side_effect=OSError('disk full')):
        with pytest.raises(ValueError,match='RESPONSE_PERSISTENCE_FAILED'):
            asyncio.run(service.generate(sections[0],choice))
    assert len(sdk.calls)==1
    assert len(service.narration.history('reservation'))==1


def test_primary_composition_needs_no_local_tts_runtime(cloud,monkeypatch):
    import sys
    from app.desktop.product_composition import compose_installed_audio
    session,_,_,_=cloud
    monkeypatch.setenv('AICS_TTS_PROVIDER','openai')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setitem(sys.modules,'torch',None)
    monkeypatch.setitem(sys.modules,'chatterbox',None)
    service=compose_installed_audio(session)
    assert isinstance(service,CloudAudioServices)
    assert service.choices('en')[0].provider=='openai'
    with pytest.raises(SpeechError,match='MISSING_API_KEY'):
        service.provider.check_configuration()


def test_rate_limit_is_bounded_and_received_responses_are_archived(cloud):
    _,service,sdk,sections=cloud
    choice=service.choices('en')[0]
    approve(service,sections[:1],choice)
    error=RuntimeError('rate limited'); error.status_code=429
    sdk.failures=[error,None]
    assert asyncio.run(service.generate(sections[0],choice))=='completed'
    assert len(sdk.calls)==3  # retry the missing first chunk, then second chunk
    assert len(list((service.root/'responses').glob('*-response-*.json')))==2
    assert not service.playback(sections[0],'original',choice).stale
    assert not service.playback(sections[0],'original').stale


def test_unexpected_pcm_format_is_normalized_and_clipping_rejected():
    import shutil
    ffmpeg=shutil.which('ffmpeg')
    if ffmpeg is None:
        pytest.skip('FFmpeg normalization requires the configured local media tool')
    payload=normalize_speech_wav(wav(channels=2,width=1,rate=8000),ffmpeg)
    pcm,_=inspect_pcm_wav(payload)
    assert (pcm.sample_rate,pcm.channels,pcm.sample_width,pcm.frame_count)==(24000,1,2,4800)
    stream=BytesIO()
    with wave.open(stream,'wb') as writer:
        writer.setparams((1,2,24000,0,'NONE','not compressed'))
        writer.writeframes(b'\xff\x7f'*100)
    with pytest.raises(SpeechError,match='CLIPPED_SPEECH_AUDIO'):
        normalize_speech_wav(stream.getvalue())


def test_changed_pricing_requires_a_fresh_approval(cloud,monkeypatch):
    from app.storage.narration import PRICING
    _,service,sdk,sections=cloud
    choice=service.choices('en')[0]
    approve(service,sections[:1],choice)
    monkeypatch.setitem(PRICING,'version','new-pricing-version')
    with pytest.raises(ValueError,match='pricing changed'):
        asyncio.run(service.generate(sections[0],choice))
    assert not sdk.calls and not service.narration.history('reservation')


def test_cost_preflight_verifies_pcm_off_owner_thread(cloud,monkeypatch):
    _,service,sdk,sections=cloud
    choice=service.choices('en')[0]
    approve(service,sections[:1],choice)
    asyncio.run(service.generate(sections[0],choice))
    owner=threading.get_ident()
    threads=[]
    def inspect(payload):
        threads.append(threading.get_ident())
        return inspect_pcm_wav(payload)
    monkeypatch.setattr('app.desktop.audio_services.inspect_pcm_wav',inspect)
    estimate=asyncio.run(service.estimate_async(sections,choice))
    assert estimate['reuse']==1 and estimate['regenerate']==1
    assert threads and all(t!=owner for t in threads)
    assert len(sdk.calls)==2
