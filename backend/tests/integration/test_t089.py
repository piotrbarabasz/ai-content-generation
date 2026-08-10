from __future__ import annotations

import asyncio
import builtins
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlencode
import wave

import pytest

from app.api.dependencies import build_api_dependencies
from app.api.main import create_app
from app.api.routes.projects import WORKFLOW_CONFIGS, reset_api_state
from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.modules.voiceover import VoiceoverModule
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_catalog import build_tts_catalog
from app.providers.tts_result import TTSSynthesisResult
from app.storage.local_store import LocalArtifactStore
from app.tts.assembly import inspect_pcm_wav
from app.tts.post_processing import AudioPostProcessingResult
from app.tts.preview import ApprovedReferenceAudio, TTSPreviewService
from app.tts.selection import TTSSelectionError, map_catalog_selection
from app.workflow.execution import ModuleExecutionContext


_OPTIONAL_RUNTIME_ROOTS = frozenset(
    {"TTS", "chatterbox", "piper", "torch", "torchaudio"}
)


@pytest.fixture
def isolated_root():
    root = Path(tempfile.mkdtemp(prefix="t089-", dir=Path.cwd()))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_api_state():
    reset_api_state()
    try:
        yield
    finally:
        reset_api_state()


def _wav(text: str, *, sample_rate: int = 16_000) -> bytes:
    frame_count = 640 + int(sha256(text.encode("utf-8")).hexdigest()[:4], 16) % 160
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * frame_count)
    return output.getvalue()


class _TempoFake:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, audio_bytes: bytes, tempo: object) -> AudioPostProcessingResult:
        value = float(tempo)
        self.calls.append(value)
        input_parameters, _ = inspect_pcm_wav(audio_bytes)
        output = audio_bytes
        if value != 1.0:
            output_buffer = io.BytesIO()
            with wave.open(output_buffer, "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(input_parameters.sample_rate)
                writer.writeframes(
                    b"\x00\x00" * max(round(input_parameters.frame_count / value), 1)
                )
            output = output_buffer.getvalue()
        output_parameters, _ = inspect_pcm_wav(output)
        return AudioPostProcessingResult(
            audio_bytes=output,
            audio_parameters=output_parameters,
            tempo=value,
            processor="t089-provider-neutral-fake",
            input_duration_seconds=input_parameters.duration_seconds,
            output_duration_seconds=output_parameters.duration_seconds,
            input_checksum=sha256(audio_bytes).hexdigest(),
            output_checksum=sha256(output).hexdigest(),
        )


class _ProviderFake:
    provider_type = ProviderType.TTS

    def __init__(self, config: ProviderConfig) -> None:
        self.provider_name = config.provider_name
        self.settings = dict(config.settings)
        self.synthesis_calls: list[tuple[str, dict[str, object]]] = []
        self.identity_calls: list[dict[str, object]] = []

    def capabilities(self) -> TTSCapabilities:
        usage_policy = str(self.settings.get("usage_policy", "production"))
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("en", "pl"),
            voice_modes=("builtin", "catalog", "reference"),
            reference_audio_required=False,
            speaking_rate_supported=False,
            usage_policy=usage_policy,
        )

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        model = self.settings.get("model_variant", self.settings.get("model_key"))
        generation_keys = (
            "cfg_weight",
            "device",
            "exaggeration",
            "length_scale",
            "min_p",
            "noise_scale",
            "noise_w_scale",
            "repetition_penalty",
            "temperature",
            "top_p",
            "volume",
        )
        generation_settings = {
            key: config.get(key, self.settings.get(key))
            for key in generation_keys
            if config.get(key, self.settings.get(key)) is not None
        }
        identity = {
            "provider": self.provider_name,
            "model": model,
            "voice": {
                "id": config.get("voice_id"),
                "mode": config.get("voice_mode"),
            },
            "language": config.get("language_id", self.settings.get("language_id")),
            "generation_settings": generation_settings,
        }
        self.identity_calls.append(identity)
        return identity

    def synthesize(self, text, voice_config=None):
        config = dict(voice_config or {})
        self.synthesis_calls.append((text, config))
        audio = _wav(text)
        parameters, _ = inspect_pcm_wav(audio)
        return TTSSynthesisResult(
            audio_bytes=audio,
            sample_rate=parameters.sample_rate,
            duration_seconds=parameters.duration_seconds,
            audio_format="wav",
            provider_name=self.provider_name,
        )


class _FakeBuilder:
    def __init__(self) -> None:
        self.providers: list[_ProviderFake] = []

    def __call__(self, config: ProviderConfig) -> _ProviderFake:
        provider = _ProviderFake(config)
        self.providers.append(provider)
        return provider


async def _call(
    app,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    messages: list[dict[str, object]] = []
    request_sent = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    headers = [(b"host", b"testserver")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": urlencode(query or {}).encode("ascii"),
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin1"): value.decode("latin1")
        for key, value in start["headers"]  # type: ignore[index]
    }
    return start["status"], response_headers, response_body  # type: ignore[index]


def _request(app, method: str, path: str, **kwargs):
    status, headers, body = asyncio.run(_call(app, method, path, **kwargs))
    return status, headers, json.loads(body) if body else None


def _workflow_payload(project_id: str) -> dict[str, object]:
    return {
        "projectId": project_id,
        "workflowPreset": "short_video",
        "contentType": "short_video",
        "contentGenre": "news",
        "durationProfile": "60s",
        "targetPlatform": "youtube_shorts",
        "language": "en",
        "tone": "neutral",
        "enabledModules": ["voiceover"],
        "providerConfig": {
            "tts": {
                "providerName": "chatterbox_v3",
                "enabled": True,
                "settings": {
                    "provider": "chatterbox_v3",
                    "usagePolicy": "production",
                    "modelVariant": "v3",
                    "cfgWeight": 0.4,
                },
            }
        },
        "voiceConfig": {
            "voiceId": "builtin",
            "voiceMode": "builtin",
            "postProcessing": {"tempo": 1.2},
        },
    }


def test_future_ui_selection_path_preserves_identity_and_cache_boundaries(
    isolated_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_builder = _FakeBuilder()
    preview_tempo = _TempoFake()
    production_tempo = _TempoFake()
    preview_root = isolated_root / "preview-cache"
    production_root = isolated_root / "production-chunk-cache"
    service = TTSPreviewService(
        catalog=build_tts_catalog(),
        preview_root=preview_root,
        provider_builder=preview_builder,
        tempo_processor=preview_tempo,
    )
    app = create_app()
    app.state.api_dependencies = build_api_dependencies(
        app.state.api_settings,
        tts_preview_service=service,
    )

    original_import = builtins.__import__
    attempted_runtime_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name.split(".", 1)[0] in _OPTIONAL_RUNTIME_ROOTS:
            attempted_runtime_imports.append(name)
            raise AssertionError(f"optional runtime import attempted: {name}")
        return original_import(name, globals, locals, fromlist, level)

    forbidden_activity: list[str] = []
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: forbidden_activity.append("network"),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: forbidden_activity.append("ffmpeg"),
    )
    monkeypatch.setattr(
        "app.modules.voiceover.process_pcm_wav_tempo",
        production_tempo,
    )

    catalog_status, _, catalog = _request(
        app,
        "GET",
        "/api/v1/tts/catalog",
        query={"language": "en", "usagePolicy": "production"},
    )
    assert catalog_status == 200
    assert tuple(provider["id"] for provider in catalog["providers"]) == (
        "chatterbox_v3",
    )
    selection = catalog["providers"][0]
    model = selection["models"][0]
    voice = next(item for item in model["voices"] if item["id"] == "builtin")
    assert selection["capabilities"]["speakingRateSupported"] is False

    preview_request = {
        "provider": selection["id"],
        "model": model["id"],
        "voice": voice["id"],
        "language": "en",
        "tempo": 1.2,
        "text": "Deterministic acceptance preview.",
        "synthesisSettings": {"cfgWeight": 0.4},
    }
    first_status, _, first = _request(
        app, "POST", "/api/v1/tts/previews", payload=preview_request
    )
    repeat_status, _, repeat = _request(
        app, "POST", "/api/v1/tts/previews", payload=preview_request
    )
    assert first_status == repeat_status == 200
    assert first["cached"] is False and repeat["cached"] is True
    assert first["previewId"] == repeat["previewId"]
    assert first["audioUrl"] == f"/api/v1/tts/previews/{first['previewId']}/audio"
    assert sum(len(provider.synthesis_calls) for provider in preview_builder.providers) == 1

    audio_status, audio_headers, audio_bytes = asyncio.run(
        _call(app, "GET", first["audioUrl"])
    )
    audio_parameters, _ = inspect_pcm_wav(audio_bytes)
    assert audio_status == 200
    assert audio_headers["content-type"].startswith("audio/wav")
    assert audio_parameters.channels == 1
    assert audio_parameters.sample_width == 2
    assert sha256(audio_bytes).hexdigest() == first["checksum"]

    project_status, _, project = _request(
        app,
        "POST",
        "/api/v1/projects",
        payload={
            "workspaceId": "workspace-t089",
            "name": "T089 acceptance",
            "contentType": "short_video",
            "contentGenre": "news",
            "targetPlatform": "youtube_shorts",
            "language": "en",
            "tone": "neutral",
        },
    )
    assert project_status == 201
    workflow_status, _, workflow_response = _request(
        app,
        "POST",
        f"/api/v1/projects/{project['id']}/workflow-configs",
        payload=_workflow_payload(project["id"]),
    )
    assert workflow_status == 201
    workflow = WORKFLOW_CONFIGS[workflow_response["id"]]
    assert workflow.language == "en"
    assert workflow.provider_config["tts"]["settings"]["cfg_weight"] == 0.4
    assert workflow.voice_config == {
        "voice_id": "builtin",
        "voice_mode": "builtin",
        "post_processing": {"tempo": 1.2},
    }

    tts_config = workflow.provider_config["tts"]
    production_provider = _ProviderFake(
        ProviderConfig.create(
            workflow_config_id=workflow.id,
            provider_type=ProviderType.TTS,
            provider_name=tts_config["providerName"],
            settings=tts_config["settings"],
        )
    )
    production_voice_config = {
        **workflow.voice_config,
        "language_id": workflow.language,
    }
    result = VoiceoverModule(
        tts_provider=production_provider,
        artifact_store=LocalArtifactStore(isolated_root / "production-artifacts"),
        resumable_runtime_dir=production_root,
    ).execute(
        ModuleExecutionContext(
            workflow_run_id="t089-production-run",
            workflow_config_id=workflow.id,
            module_name="voiceover",
            inputs={
                "text": "Production narration uses an independent artifact.",
                "voice_config": production_voice_config,
                "resumable_chunking": {"max_words": 50, "max_attempts": 1},
            },
        )
    )
    assert result.status == "completed"

    preview_identity = preview_builder.providers[0].identity_calls[0]
    production_identity = production_provider.identity_calls[0]
    assert production_identity == preview_identity == {
        "provider": "chatterbox_v3",
        "model": "v3",
        "voice": {"id": "builtin", "mode": "builtin"},
        "language": "en",
        "generation_settings": {"cfg_weight": 0.4},
    }
    identity_json = json.dumps(production_identity, sort_keys=True)
    assert "tempo" not in identity_json
    assert "speaking_rate" not in identity_json
    assert preview_tempo.calls == production_tempo.calls == [1.2]

    preview_audio = preview_root / "preview-audio" / f"{first['previewId']}.wav"
    production_run_root = production_root / "t089-production-run"
    production_manifest = json.loads(
        (production_run_root / "synthesis-manifest.json").read_text(encoding="utf-8")
    )
    production_chunk_refs = {
        item["artifact_ref"] for item in production_manifest["chunks"]
    }
    assert preview_root.resolve() != production_root.resolve()
    assert preview_audio.is_file()
    assert production_chunk_refs
    assert all((production_run_root / ref).is_file() for ref in production_chunk_refs)
    assert first["previewId"] not in json.dumps(production_manifest)
    assert preview_audio.read_bytes() != (
        isolated_root / "production-artifacts" / result.output["artifact"]["storage_key"]
    ).read_bytes()
    assert attempted_runtime_imports == []
    assert forbidden_activity == []
    assert not {
        name for name in sys.modules if name.split(".", 1)[0] in _OPTIONAL_RUNTIME_ROOTS
    }


@pytest.mark.parametrize(
    "provider, model, voice, language, settings, reference",
    [
        ("chatterbox_v3", "v3", "builtin", "en", {"cfgWeight": 0.3}, False),
        ("piper", "pl_PL-gosia-medium", "gosia", "pl", {"lengthScale": 1.1}, False),
        ("xtts_v2_eval", "xtts_v2", "reference", "pl", None, True),
    ],
)
def test_provider_shaped_preview_fakes_cover_catalog_without_real_runtimes(
    isolated_root: Path,
    provider: str,
    model: str,
    voice: str,
    language: str,
    settings: dict[str, object] | None,
    reference: bool,
) -> None:
    builder = _FakeBuilder()
    reference_path = isolated_root / "approved-reference.wav"
    reference_path.write_bytes(b"deterministic-approved-reference")
    reference_checksum = sha256(reference_path.read_bytes()).hexdigest()
    resolver = lambda artifact_id: (
        ApprovedReferenceAudio(reference_path, reference_checksum, "t089-approved")
        if artifact_id == "approved-reference"
        else None
    )
    service = TTSPreviewService(
        catalog=build_tts_catalog(),
        preview_root=isolated_root / f"preview-{provider}",
        provider_builder=builder,
        tempo_processor=_TempoFake(),
        reference_artifact_resolver=resolver,
    )

    result = service.synthesize_preview(
        provider=provider,
        model=model,
        voice=voice,
        language=language,
        tempo=1.0,
        text=f"Offline {provider} preview.",
        synthesis_settings=settings,
        reference_audio_artifact_id="approved-reference" if reference else None,
    )

    assert result.provider == provider
    assert result.model == model
    assert result.voice == voice
    assert result.language == language
    assert builder.providers[0].provider_name == provider
    assert len(builder.providers[0].synthesis_calls) == 1
    inspect_pcm_wav(service.read_audio(result.preview_id))


def test_xtts_production_selection_is_rejected_before_provider_composition() -> None:
    builder = _FakeBuilder()

    with pytest.raises(TTSSelectionError, match="evaluation-only"):
        map_catalog_selection(
            catalog=build_tts_catalog(),
            provider="xtts_v2_eval",
            model="xtts_v2",
            voice="reference",
            language="pl",
            reference_audio_artifact_id="approved-reference",
            reference_audio_metadata={
                "checksum": "a" * 64,
                "approvalLabel": "t089-approved",
                "approved": True,
            },
            usage_policy="production",
        )

    assert builder.providers == []
