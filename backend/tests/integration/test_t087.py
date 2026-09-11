from __future__ import annotations

import asyncio
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import wave

import pytest

from app.api import dependencies as api_dependencies
from app.api.dependencies import ApiSettings, build_api_dependencies
from app.api.main import create_app
from app.domain.enums import ProviderType
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_result import TTSSynthesisResult
from app.tts.assembly import inspect_pcm_wav
from app.tts.catalog import (
    TTSCatalog,
    TTSModelDescriptor,
    TTSProviderDescriptor,
    TTSVoiceDescriptor,
)
from app.tts.post_processing import AudioPostProcessingResult
from app.tts.preview import TTSPreviewError, TTSPreviewNotFoundError, TTSPreviewService


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="t087-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _wav(*, frames: int = 800, sample_rate: int = 8000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


_CAPABILITIES = TTSCapabilities(
    provider_name="mock",
    supported_languages=("en", "pl"),
    voice_modes=("builtin", "reference"),
    reference_audio_required=False,
    speaking_rate_supported=False,
    usage_policy="production",
)


def _catalog() -> TTSCatalog:
    return TTSCatalog(
        providers=(
            TTSProviderDescriptor(
                id="mock",
                display_name="Mock",
                usage_policy="production",
                supported_languages=("en", "pl"),
                capabilities=_CAPABILITIES,
                models=(
                    TTSModelDescriptor(
                        id="v3",
                        display_name="Mock V3",
                        provider_id="mock",
                        supported_languages=("en", "pl"),
                        voices=(
                            TTSVoiceDescriptor(
                                id="builtin",
                                display_name="Builtin",
                                provider_id="mock",
                                model_id="v3",
                                voice_mode="builtin",
                                supported_languages=("en", "pl"),
                                preview_supported=True,
                                reference_audio_required=False,
                            ),
                            TTSVoiceDescriptor(
                                id="alternate",
                                display_name="Alternate",
                                provider_id="mock",
                                model_id="v3",
                                voice_mode="builtin",
                                supported_languages=("en", "pl"),
                                preview_supported=True,
                                reference_audio_required=False,
                            ),
                            TTSVoiceDescriptor(
                                id="reference",
                                display_name="Reference",
                                provider_id="mock",
                                model_id="v3",
                                voice_mode="reference",
                                supported_languages=("en", "pl"),
                                preview_supported=True,
                                reference_audio_required=True,
                            ),
                        ),
                        runtime_required=False,
                        asset_required=False,
                    ),
                ),
            ),
        )
    )


class _FakeProvider:
    provider_type = ProviderType.TTS
    provider_name = "mock"

    def __init__(self) -> None:
        self.synthesis_count = 0

    def capabilities(self) -> TTSCapabilities:
        return _CAPABILITIES

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        return {
            "provider": "mock",
            "model_variant": "v3",
            "language_id": config.get("language_id"),
            "voice_mode": config.get("voice_mode"),
            "temperature": config.get("temperature"),
        }

    def synthesize(self, text, voice_config=None):
        self.synthesis_count += 1
        return TTSSynthesisResult(
            audio_bytes=_wav(),
            sample_rate=8000,
            duration_seconds=0.1,
            audio_format="wav",
            provider_name="mock",
        )


class _FakeTempoProcessor:
    def __call__(self, audio_bytes: bytes, tempo: object) -> AudioPostProcessingResult:
        value = float(tempo)
        input_parameters, _ = inspect_pcm_wav(audio_bytes)
        output = audio_bytes if value == 1.0 else _wav(
            frames=round(input_parameters.frame_count / value)
        )
        output_parameters, _ = inspect_pcm_wav(output)
        return AudioPostProcessingResult(
            audio_bytes=output,
            audio_parameters=output_parameters,
            tempo=value,
            processor="fake-tempo",
            input_duration_seconds=input_parameters.duration_seconds,
            output_duration_seconds=output_parameters.duration_seconds,
            input_checksum=sha256(audio_bytes).hexdigest(),
            output_checksum=sha256(output).hexdigest(),
        )


def _service(tmp_path: Path) -> tuple[TTSPreviewService, _FakeProvider]:
    provider = _FakeProvider()
    service = TTSPreviewService(
        catalog=_catalog(),
        preview_root=tmp_path / "tts-previews",
        provider_builder=lambda _: provider,
        tempo_processor=_FakeTempoProcessor(),
    )
    return service, provider


def _app(service):
    app = create_app()
    app.state.api_dependencies = build_api_dependencies(
        app.state.api_settings,
        tts_preview_service=service,
    )
    return app


async def _call(
    app,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    raw_path: bytes | None = None,
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
        "raw_path": raw_path if raw_path is not None else path.encode("utf-8"),
        "query_string": b"",
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


def _post(app, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return asyncio.run(_call(app, "POST", "/api/v1/tts/previews", payload=payload))


def _request(**overrides: Any) -> dict[str, Any]:
    payload = {
        "provider": "mock",
        "model": "v3",
        "voice": "builtin",
        "language": "en",
        "tempo": 1.0,
        "text": "A short preview.",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("configured_root", [None, Path(".local/custom-previews")])
def test_api_preview_storage_and_reload_without_development_metadata(
    tmp_path: Path, monkeypatch, configured_root: Path | None
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = _FakeProvider()
    monkeypatch.setattr(api_dependencies, "build_tts_catalog", _catalog)

    def preview_service(**kwargs):
        return TTSPreviewService(
            **kwargs,
            provider_builder=lambda _: provider,
            tempo_processor=_FakeTempoProcessor(),
        )

    monkeypatch.setattr(api_dependencies, "TTSPreviewService", preview_service)
    settings = (
        ApiSettings(tts_preview_root=configured_root)
        if configured_root is not None
        else None
    )
    dependencies = build_api_dependencies(settings)
    app = _app(dependencies.tts_preview_service)
    status, _, body = _post(app, _request())
    assert status == 200
    first = json.loads(body)
    expected_root = configured_root or Path(".runtime/tts-previews")
    assert list(expected_root.rglob("*.wav"))
    assert list(expected_root.rglob("*.json"))
    assert not Path(".specify").exists()

    # A fresh service must reuse the persisted bytes, without a real provider run.
    reloaded = build_api_dependencies(settings)
    reloaded_app = _app(reloaded.tts_preview_service)
    status, _, body = _post(reloaded_app, _request())
    assert status == 200
    repeated = json.loads(body)
    assert repeated["previewId"] == first["previewId"]
    assert repeated["cached"] is True
    assert provider.synthesis_count == 1
    status, headers, audio = asyncio.run(
        _call(reloaded_app, "GET", repeated["audioUrl"])
    )
    assert status == 200
    assert headers["content-type"] == "audio/wav"
    assert sha256(audio).hexdigest() == first["checksum"]


def test_preview_create_cache_identity_and_wav_delivery(tmp_path: Path) -> None:
    service, provider = _service(tmp_path)
    app = _app(service)

    first_status, _, first_body = _post(app, _request())
    repeat_status, _, repeat_body = _post(app, _request())
    changed_text = json.loads(_post(app, _request(text="Different text."))[2])
    changed_voice = json.loads(_post(app, _request(voice="alternate"))[2])
    changed_tempo = json.loads(_post(app, _request(tempo=1.25))[2])

    assert first_status == repeat_status == 200
    first = json.loads(first_body)
    repeat = json.loads(repeat_body)
    assert set(first) == {
        "previewId",
        "audioUrl",
        "provider",
        "model",
        "voice",
        "language",
        "tempo",
        "durationSeconds",
        "checksum",
        "cached",
    }
    assert first["cached"] is False and repeat["cached"] is True
    assert first["previewId"] == repeat["previewId"]
    assert repeat["audioUrl"] == f"/api/v1/tts/previews/{first['previewId']}/audio"
    assert not any(term in json.dumps(first).lower() for term in ("storage", "runtime", "file:"))
    assert len(
        {
            first["previewId"],
            changed_text["previewId"],
            changed_voice["previewId"],
            changed_tempo["previewId"],
        }
    ) == 4
    assert provider.synthesis_count == 4

    audio_status, audio_headers, audio_body = asyncio.run(
        _call(app, "GET", first["audioUrl"])
    )
    assert audio_status == 200
    assert audio_headers["content-type"].startswith("audio/wav")
    inspect_pcm_wav(audio_body)
    assert sha256(audio_body).hexdigest() == first["checksum"]


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        (_request(runtimePath="C:\\private\\runtime"), 422),
        (_request(audioUrl="file:///private/preview.wav"), 422),
        (_request(reference_audio_artifact_id="artifact-one"), 422),
        (_request(synthesisSettings={"model_path": "C:\\private\\model.onnx"}), 400),
        (_request(synthesisSettings={"unknown": 1}), 400),
        (_request(model="missing"), 400),
        (_request(language="de"), 400),
        (_request(text="   \n\t"), 400),
        (_request(text="x" * 401), 400),
        (_request(voice="reference"), 400),
    ],
)
def test_preview_requests_reject_unknown_paths_and_invalid_inputs(
    tmp_path: Path,
    payload: dict[str, Any],
    expected_status: int,
) -> None:
    service, provider = _service(tmp_path)

    response_status, _, response_body = _post(_app(service), payload)

    assert response_status == expected_status
    assert json.loads(response_body)["detail"]
    assert provider.synthesis_count == 0


@pytest.mark.parametrize(
    "path, raw_path",
    [
        ("/api/v1/tts/previews/unknown/audio", None),
        ("/api/v1/tts/previews/tts_preview_short/audio", None),
        ("/api/v1/tts/previews/..\\private/audio", None),
        ("/api/v1/tts/previews/C:\\private\\voice.wav/audio", None),
        ("/api/v1/tts/previews/../private/audio", None),
        (
            "/api/v1/tts/previews/../private/audio",
            b"/api/v1/tts/previews/%2e%2e%2fprivate/audio",
        ),
        ("/api/v1/tts/previews//private/voice.wav/audio", None),
    ],
)
def test_preview_audio_unknown_malformed_traversal_and_absolute_paths_are_404(
    tmp_path: Path,
    path: str,
    raw_path: bytes | None,
) -> None:
    service, _ = _service(tmp_path)

    response_status, _, _ = asyncio.run(
        _call(_app(service), "GET", path, raw_path=raw_path)
    )

    assert response_status == 404


class _FailingService:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.read_ids: list[str] = []

    def synthesize_preview(self, **_: Any):
        raise self.error

    def read_audio(self, preview_id: str) -> bytes:
        self.read_ids.append(preview_id)
        raise self.error


@pytest.mark.parametrize(
    "error, expected_status, expected_detail",
    [
        (TTSPreviewError("The preview selection is invalid."), 400, "The preview selection is invalid."),
        (TTSPreviewNotFoundError("private path"), 404, "TTS preview was not found."),
        (
            RuntimeError("C:\\private\\model.bin secret-token"),
            500,
            "TTS preview request failed.",
        ),
    ],
)
def test_preview_errors_are_stable_and_unexpected_internals_are_redacted(
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    service = _FailingService(error)
    app = _app(service)

    post_status, _, post_body = _post(app, _request())
    get_status, _, get_body = asyncio.run(
        _call(app, "GET", "/api/v1/tts/previews/tts_preview_" + "a" * 64 + "/audio")
    )

    assert post_status == get_status == expected_status
    assert json.loads(post_body) == {"detail": expected_detail}
    assert json.loads(get_body) == {"detail": expected_detail}
    assert "private" not in post_body.decode().lower()
    assert service.read_ids == ["tts_preview_" + "a" * 64]


def test_preview_openapi_keeps_catalog_components_and_declares_wav_response() -> None:
    openapi = create_app().openapi()

    assert "/api/v1/tts/catalog" in openapi["paths"]
    assert "/api/v1/tts/previews" in openapi["paths"]
    audio_operation = openapi["paths"]["/api/v1/tts/previews/{preview_id}/audio"]["get"]
    assert "audio/wav" in audio_operation["responses"]["200"]["content"]
    assert {name for name in openapi["components"]["schemas"] if name.startswith("TTS")} == {
        "TTSCapabilities",
        "TTSCatalog",
        "TTSModel",
        "TTSProvider",
        "TTSVoice",
    }
