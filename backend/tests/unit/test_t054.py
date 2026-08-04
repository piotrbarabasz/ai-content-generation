import importlib.util
import io
import sys
import types
import wave
from pathlib import Path

import pytest

from app.providers.chatterbox_v3 import (
    ChatterboxAudioPromptError,
    ChatterboxAudioValidationError,
    ChatterboxCompatibilityError,
    ChatterboxDependencyError,
    ChatterboxDeviceError,
    ChatterboxGenerationError,
    ChatterboxModelLoadError,
    ChatterboxV3Provider,
    _load_runtime_backend,
)

_MISSING = object()


def _wav(sample_rate: int = 24_000, frames: int = 10) -> bytes:
    result = io.BytesIO()
    with wave.open(result, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * frames)
    return result.getvalue()


class RecordingBackend:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def generate(self, text: str, **kwargs: object) -> bytes:
        self.calls.append((text, kwargs))
        return self.payload


class RaisingBackend:
    def generate(self, text: str, **kwargs: object) -> bytes:
        raise ValueError("boom")


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cuda_available: bool = True,
    model_factory_error: Exception | None = None,
    captured_from_pretrained: dict[str, object] | None = None,
    accepts_t3_model: bool = True,
) -> None:
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    fake_torch.device = lambda value: value

    fake_torchaudio = types.ModuleType("torchaudio")
    fake_torchaudio.save = lambda *args, **kwargs: None

    fake_chatterbox = types.ModuleType("chatterbox")
    fake_chatterbox.__path__ = []  # type: ignore[attr-defined]

    fake_mtl_tts = types.ModuleType("chatterbox.mtl_tts")

    class FakeChatterboxMultilingualTTS:
        if accepts_t3_model:
            @classmethod
            def from_pretrained(cls, device: object, t3_model: str) -> object:
                if captured_from_pretrained is not None:
                    captured_from_pretrained["device"] = device
                    captured_from_pretrained["t3_model"] = t3_model
                if model_factory_error is not None:
                    raise model_factory_error
                return {"device": device, "t3_model": t3_model}
        else:
            @classmethod
            def from_pretrained(cls, device: object) -> object:
                if captured_from_pretrained is not None:
                    captured_from_pretrained["device"] = device
                if model_factory_error is not None:
                    raise model_factory_error
                return {"device": device}

    fake_mtl_tts.ChatterboxMultilingualTTS = FakeChatterboxMultilingualTTS
    fake_chatterbox.mtl_tts = fake_mtl_tts  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)
    monkeypatch.setitem(sys.modules, "chatterbox", fake_chatterbox)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", fake_mtl_tts)


def _guard_optional_runtime_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, types.ModuleType]:
    guarded_modules: dict[str, types.ModuleType] = {}
    for name in ("torch", "torchaudio", "chatterbox", "chatterbox.mtl_tts"):
        module = types.ModuleType(name)
        module.__guarded__ = name  # type: ignore[attr-defined]
        guarded_modules[name] = module
        monkeypatch.setitem(sys.modules, name, module)
    return guarded_modules


def _load_provider_module(module_name: str) -> types.ModuleType:
    provider_path = Path(__file__).resolve().parents[2] / "app" / "providers" / "chatterbox_v3.py"
    spec = importlib.util.spec_from_file_location(module_name, provider_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def test_module_import_and_provider_construction_stay_lazy_with_guarded_optional_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_names = ("torch", "torchaudio", "chatterbox", "chatterbox.mtl_tts")
    temp_module_name = "app.providers.chatterbox_v3_import_regression"
    originals = {name: sys.modules.get(name, _MISSING) for name in module_names}
    original_temp_module = sys.modules.get(temp_module_name, _MISSING)
    guarded_modules = _guard_optional_runtime_modules(monkeypatch)
    loader_calls: list[str] = []

    try:
        reloaded_module = _load_provider_module(temp_module_name)
        provider = reloaded_module.ChatterboxV3Provider(
            model_loader=lambda device: loader_calls.append(device) or RecordingBackend(_wav())
        )

        assert loader_calls == []
        assert provider._backend is None
        assert provider.provider_name == "chatterbox_v3"
        assert provider.t3_model == "v3"
        for name, module in guarded_modules.items():
            assert sys.modules[name] is module
    finally:
        for name, original in originals.items():
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        if original_temp_module is _MISSING:
            sys.modules.pop(temp_module_name, None)
        else:
            sys.modules[temp_module_name] = original_temp_module


def test_provider_reuses_backend_and_marks_builtin_voice_with_polish_defaults() -> None:
    calls: list[str] = []
    backend = RecordingBackend(_wav())
    provider = ChatterboxV3Provider(
        model_loader=lambda device: calls.append(device) or backend
    )

    first = provider.synthesize("tekst")
    second = provider.synthesize("ponownie")

    assert provider.provider_name == "chatterbox_v3"
    assert provider.t3_model == "v3"
    assert first.metadata["language_id"] == "pl"
    assert first.metadata["voice"] == "builtin"
    assert second.metadata["voice"] == "builtin"
    assert calls == ["cpu"]
    assert provider._backend is backend
    assert backend.calls[0] == ("tekst", {"language_id": "pl"})
    assert backend.calls[1] == ("ponownie", {"language_id": "pl"})


def test_provider_forwards_reference_prompt_and_language_override() -> None:
    reference = Path(__file__).resolve()
    backend = RecordingBackend(_wav())
    provider = ChatterboxV3Provider(
        audio_prompt_path=reference,
        model_loader=lambda _: backend,
    )

    result = provider.synthesize("tekst", {"language_id": "en"})

    assert result.metadata["voice"] == "reference"
    assert result.metadata["language_id"] == "en"
    assert backend.calls == [("tekst", {"language_id": "en", "audio_prompt_path": str(reference)})]


@pytest.mark.parametrize("device", ["cpu", "cuda", "cuda:0"])
def test_provider_forwards_requested_device_string_to_model_loader(device: str) -> None:
    seen: list[str] = []
    provider = ChatterboxV3Provider(
        device=device,
        model_loader=lambda value: seen.append(value) or RecordingBackend(_wav()),
    )

    provider.synthesize("tekst")

    assert seen == [device]


def test_provider_rejects_unsupported_device_before_loading() -> None:
    provider = ChatterboxV3Provider(
        device="metal",
        model_loader=lambda _: RecordingBackend(_wav()),
    )

    with pytest.raises(ChatterboxDeviceError, match="Unsupported Chatterbox device"):
        provider.synthesize("tekst")


def test_runtime_loader_rejects_missing_optional_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "torchaudio", None)
    monkeypatch.setitem(sys.modules, "chatterbox", None)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", None)

    with pytest.raises(ChatterboxDependencyError, match="chatterbox-v3"):
        _load_runtime_backend("cpu")


def test_runtime_loader_rejects_unavailable_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_runtime(monkeypatch, cuda_available=False)

    with pytest.raises(ChatterboxDeviceError, match="CUDA was requested but is unavailable"):
        _load_runtime_backend("cuda:0")


def test_runtime_loader_forwards_device_and_avoids_outbound_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_from_pretrained: dict[str, object] = {}
    _install_fake_runtime(monkeypatch, captured_from_pretrained=captured_from_pretrained)

    def _deny(*_: object, **__: object) -> object:
        raise AssertionError("outbound HuggingFace entrypoints must not be touched")

    fake_huggingface_hub = types.ModuleType("huggingface_hub")
    fake_huggingface_hub.hf_hub_download = _deny  # type: ignore[attr-defined]
    fake_huggingface_hub.snapshot_download = _deny  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_huggingface_hub)

    runtime = _load_runtime_backend("cuda:0")

    assert captured_from_pretrained == {"device": "cuda:0", "t3_model": "v3"}
    assert runtime._model == captured_from_pretrained
    assert runtime._torchaudio is sys.modules["torchaudio"]


def test_runtime_loader_rejects_incompatible_from_pretrained_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_runtime(monkeypatch, accepts_t3_model=False)

    with pytest.raises(ChatterboxCompatibilityError, match="t3_model"):
        _load_runtime_backend("cpu")


def test_runtime_loader_wraps_model_initialization_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_runtime(monkeypatch, model_factory_error=RuntimeError("boom"))

    with pytest.raises(ChatterboxModelLoadError, match="model loading failed"):
        _load_runtime_backend("cpu")


def test_provider_wraps_generation_errors_and_redacts_missing_prompt_paths() -> None:
    missing = Path(__file__).resolve().parent / "private" / "voice.wav"
    generation_provider = ChatterboxV3Provider(model_loader=lambda _: RaisingBackend())
    prompt_provider = ChatterboxV3Provider(
        audio_prompt_path=missing,
        model_loader=lambda _: RecordingBackend(_wav()),
    )

    with pytest.raises(ChatterboxGenerationError, match="generation failed"):
        generation_provider.synthesize("tekst")

    with pytest.raises(ChatterboxAudioPromptError) as error:
        prompt_provider.synthesize("tekst")
    assert str(missing) not in str(error.value)


def test_provider_redacts_unreadable_prompt_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    readable = Path(__file__).resolve()
    provider = ChatterboxV3Provider(audio_prompt_path=readable, model_loader=lambda _: RecordingBackend(_wav()))

    def _deny_open(*_: object, **__: object) -> object:
        raise OSError("permission denied: C:/private/voice.wav")

    monkeypatch.setattr("builtins.open", _deny_open)

    with pytest.raises(ChatterboxAudioPromptError) as error:
        provider.effective_synthesis_identity()
    assert "C:/private/voice.wav" not in str(error.value)


@pytest.mark.parametrize("text", ["", "   ", None])
def test_provider_rejects_invalid_synthesis_text(text: object) -> None:
    provider = ChatterboxV3Provider(model_loader=lambda _: RecordingBackend(_wav()))

    with pytest.raises(ChatterboxGenerationError, match="non-empty synthesis text"):
        provider.synthesize(text)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [b"", b"not wav", _wav(22_050), bytearray(), memoryview(b"not wav")],
)
def test_provider_rejects_invalid_wav_payloads(payload: bytes | bytearray | memoryview) -> None:
    provider = ChatterboxV3Provider(model_loader=lambda _: RecordingBackend(_wav()))

    provider._backend = RecordingBackend(payload if isinstance(payload, bytes) else bytes(payload))

    with pytest.raises(ChatterboxAudioValidationError):
        provider.synthesize("tekst")
