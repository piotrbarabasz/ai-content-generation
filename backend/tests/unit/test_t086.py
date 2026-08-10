from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
from threading import Lock
import tempfile
import time
import wave

import pytest

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
from app.tts.preview import (
    ApprovedReferenceAudio,
    TTSPreviewError,
    TTSPreviewNotFoundError,
    TTSPreviewService,
)


@pytest.fixture
def tmp_path():
    """Use an isolated temp child without pytest's inaccessible shared temp root."""

    path = Path(tempfile.mkdtemp(prefix="t086-"))
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


CAPABILITIES = TTSCapabilities(
    provider_name="mock",
    supported_languages=("en", "pl"),
    voice_modes=("builtin", "reference"),
    reference_audio_required=False,
    speaking_rate_supported=False,
    usage_policy="production",
)


def _catalog(*, preview_supported: bool = True) -> TTSCatalog:
    return TTSCatalog(
        providers=(
            TTSProviderDescriptor(
                id="mock",
                display_name="Mock",
                usage_policy="production",
                supported_languages=("en", "pl"),
                capabilities=CAPABILITIES,
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
                                preview_supported=preview_supported,
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


class FakeProvider:
    provider_type = ProviderType.TTS
    provider_name = "mock"

    def __init__(self, *, audio: bytes | None = None, delay: float = 0.0) -> None:
        self.audio = audio if audio is not None else _wav()
        self.delay = delay
        self.synthesis_count = 0
        self.texts: list[str] = []
        self.configs: list[dict[str, object]] = []
        self._guard = Lock()

    def capabilities(self) -> TTSCapabilities:
        return CAPABILITIES

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        self.configs.append(config)
        return {
            "provider": "mock",
            "model_variant": "v3",
            "language_id": config.get("language_id"),
            "generation_settings": {"temperature": config.get("temperature")},
            "voice": {
                "mode": config.get("voice_mode"),
                "reference_path": config.get("audio_prompt_path"),
            },
            "private_token": "must-not-persist",
        }

    def synthesize(self, text, voice_config=None):
        with self._guard:
            self.synthesis_count += 1
            self.texts.append(text)
        if self.delay:
            time.sleep(self.delay)
        return TTSSynthesisResult(
            audio_bytes=self.audio,
            sample_rate=8000,
            duration_seconds=0.1,
            audio_format="wav",
            provider_name="mock",
        )


class FakeBuilder:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider
        self.calls = []

    def __call__(self, provider_config):
        self.calls.append(provider_config)
        return self.provider


class FakeTempoProcessor:
    def __init__(self) -> None:
        self.tempos: list[float] = []

    def __call__(self, audio_bytes: bytes, tempo: object) -> AudioPostProcessingResult:
        value = float(tempo)
        self.tempos.append(value)
        input_parameters, _ = inspect_pcm_wav(audio_bytes)
        output = audio_bytes if value == 1.0 else _wav(frames=round(input_parameters.frame_count / value))
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


def _service(tmp_path: Path, provider: FakeProvider | None = None, **kwargs):
    fake_provider = provider or FakeProvider()
    builder = FakeBuilder(fake_provider)
    tempo = FakeTempoProcessor()
    service = TTSPreviewService(
        catalog=kwargs.pop("catalog", _catalog()),
        preview_root=tmp_path / "tts-previews",
        provider_builder=builder,
        tempo_processor=tempo,
        **kwargs,
    )
    return service, fake_provider, builder, tempo


def _request(**overrides):
    values = {
        "provider": "mock",
        "model": "v3",
        "voice": "builtin",
        "language": "en",
        "tempo": 1.0,
        "text": "A short preview.",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize("text", ["", " \n\t ", "x" * 401])
def test_text_validation_happens_before_provider_composition(tmp_path: Path, text: str) -> None:
    service, provider, builder, _ = _service(tmp_path)

    with pytest.raises(TTSPreviewError, match="empty|400"):
        service.synthesize_preview(**_request(text=text))

    assert builder.calls == []
    assert provider.synthesis_count == 0


def test_catalog_preview_reference_and_settings_validation_precede_composition(tmp_path: Path) -> None:
    service, _, builder, _ = _service(tmp_path)

    invalid_requests = (
        _request(model="unknown"),
        _request(language="de"),
        _request(voice="reference"),
        _request(synthesis_settings={"model_path": "C:\\private\\voice.onnx"}),
        _request(synthesis_settings={"unknown": 1}),
    )
    for request in invalid_requests:
        with pytest.raises(TTSPreviewError):
            service.synthesize_preview(**request)

    disabled, _, disabled_builder, _ = _service(tmp_path / "disabled", catalog=_catalog(preview_supported=False))
    with pytest.raises(TTSPreviewError, match="does not support previews"):
        disabled.synthesize_preview(**_request())
    assert builder.calls == []
    assert disabled_builder.calls == []


def test_normalization_cache_hit_wav_and_separate_relative_namespace(tmp_path: Path) -> None:
    service, provider, builder, tempo = _service(tmp_path)
    production_chunk = tmp_path / "tts-previews" / "chunks" / "production.wav"
    production_chunk.parent.mkdir(parents=True)
    production_chunk.write_bytes(b"production")

    first = service.synthesize_preview(**_request(text="  A\n short\t preview.  "))
    second = service.synthesize_preview(**_request(text="A short preview."))

    assert first.preview_id == second.preview_id
    assert first.cached is False and second.cached is True
    assert provider.texts == ["A short preview."]
    assert provider.synthesis_count == 1
    assert len(builder.calls) == 2
    assert tempo.tempos == [1.0]
    assert inspect_pcm_wav(service.read_audio(first.preview_id))[0].duration_seconds == first.duration_seconds
    assert production_chunk.read_bytes() == b"production"
    assert (tmp_path / "tts-previews" / "preview-audio" / f"{first.preview_id}.wav").is_file()
    assert (tmp_path / "tts-previews" / "preview-manifests" / f"{first.preview_id}.json").is_file()
    assert "path" not in json.dumps(first.to_payload()).lower()


def test_relevant_identity_and_tempo_changes_are_deterministic(tmp_path: Path) -> None:
    service, _, _, tempo = _service(tmp_path)

    baseline = service.synthesize_preview(**_request())
    changed_text = service.synthesize_preview(**_request(text="Different text."))
    changed_language = service.synthesize_preview(**_request(language="pl"))
    changed_settings = service.synthesize_preview(
        **_request(synthesis_settings={"temperature": 0.5})
    )
    changed_tempo = service.synthesize_preview(**_request(tempo=1.25))
    repeat_tempo = service.synthesize_preview(**_request(tempo=1.25))

    assert len(
        {
            baseline.preview_id,
            changed_text.preview_id,
            changed_language.preview_id,
            changed_settings.preview_id,
            changed_tempo.preview_id,
        }
    ) == 5
    assert repeat_tempo.preview_id == changed_tempo.preview_id
    assert repeat_tempo.cached is True
    assert 1.25 in tempo.tempos
    baseline_manifest = json.loads(
        (tmp_path / "tts-previews" / "preview-manifests" / f"{baseline.preview_id}.json").read_text()
    )
    tempo_manifest = json.loads(
        (tmp_path / "tts-previews" / "preview-manifests" / f"{changed_tempo.preview_id}.json").read_text()
    )
    assert baseline_manifest["native_id"] == tempo_manifest["native_id"]
    assert baseline_manifest["tempo"] == 1.0
    assert tempo_manifest["tempo"] == 1.25


def test_approved_reference_checksum_affects_identity_without_path_leakage(tmp_path: Path) -> None:
    private_root = tmp_path / "private-reference"
    private_root.mkdir()
    first_path = private_root / "speaker-one.wav"
    second_path = private_root / "speaker-two.wav"
    first_path.write_bytes(b"approved-reference-one")
    second_path.write_bytes(b"approved-reference-two")

    references = {
        "artifact-one": ApprovedReferenceAudio(
            first_path, sha256(first_path.read_bytes()).hexdigest(), "reviewed"
        ),
        "artifact-two": ApprovedReferenceAudio(
            second_path, sha256(second_path.read_bytes()).hexdigest(), "reviewed"
        ),
    }
    service, _, builder, _ = _service(
        tmp_path,
        reference_artifact_resolver=references.get,
    )

    first = service.synthesize_preview(
        **_request(voice="reference", reference_audio_artifact_id="artifact-one")
    )
    second = service.synthesize_preview(
        **_request(voice="reference", reference_audio_artifact_id="artifact-two")
    )

    assert first.preview_id != second.preview_id
    assert builder.calls[0].settings["audio_prompt_path"] == first_path
    manifest_text = (
        tmp_path / "tts-previews" / "preview-manifests" / f"{first.preview_id}.json"
    ).read_text()
    assert str(private_root) not in manifest_text
    assert "approved-reference-one" not in manifest_text
    assert "must-not-persist" not in manifest_text
    assert sha256(first_path.read_bytes()).hexdigest() in manifest_text
    assert str(private_root) not in json.dumps(first.to_payload())


def test_corrupt_wav_and_manifest_are_regenerated(tmp_path: Path) -> None:
    service, provider, _, _ = _service(tmp_path)
    first = service.synthesize_preview(**_request())
    audio_path = tmp_path / "tts-previews" / "preview-audio" / f"{first.preview_id}.wav"
    manifest_path = tmp_path / "tts-previews" / "preview-manifests" / f"{first.preview_id}.json"

    audio_path.write_bytes(b"corrupt")
    regenerated_audio = service.synthesize_preview(**_request())
    assert regenerated_audio.cached is False
    assert provider.synthesis_count == 2
    inspect_pcm_wav(audio_path.read_bytes())

    manifest_path.write_text("{incomplete", encoding="utf-8")
    regenerated_manifest = service.synthesize_preview(**_request())
    assert regenerated_manifest.cached is False
    assert provider.synthesis_count == 3
    assert json.loads(manifest_path.read_text())["preview_id"] == first.preview_id


def test_concurrent_identical_requests_use_single_synthesis(tmp_path: Path) -> None:
    service, provider, _, _ = _service(tmp_path, provider=FakeProvider(delay=0.05))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: service.synthesize_preview(**_request()), range(8)))

    assert len({result.preview_id for result in results}) == 1
    assert provider.synthesis_count == 1
    assert sum(not result.cached for result in results) == 1
    assert sum(result.cached for result in results) == 7


def test_invalid_wav_and_unknown_or_traversal_ids_are_safe(tmp_path: Path) -> None:
    service, provider, _, tempo = _service(tmp_path, provider=FakeProvider(audio=b"not-a-wav"))

    with pytest.raises(TTSPreviewError, match="synthesis failed"):
        service.synthesize_preview(**_request())
    assert provider.synthesis_count == 1
    assert tempo.tempos == []

    for preview_id in ("unknown", "../production.wav", "C:\\private\\voice.wav"):
        with pytest.raises(TTSPreviewNotFoundError, match="not found"):
            service.read_audio(preview_id)
    assert not (tmp_path / "tts-previews" / "preview-manifests").exists()
