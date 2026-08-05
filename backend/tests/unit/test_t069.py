from __future__ import annotations

import io
import json
import wave

import pytest

import app.providers.piper_catalog as piper_catalog_module
import app.providers.piper_tts as piper_tts_module
from app.providers.piper_catalog import (
    PiperCatalogError,
    PiperVoiceCatalogEntry,
    get_piper_voice_catalog_entry,
    list_piper_voice_catalog,
    list_piper_voice_keys,
)
from app.providers.piper_tts import PiperConfigurationError, PiperTTSProvider
from app.providers.tts_result import TTSSynthesisResult


def _wav(*, sample_rate: int = 22_050, channels: int = 1, sample_width: int = 2, frames: int = 12) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(b"\0" * (frames * channels * sample_width))
    return buffer.getvalue()


class RecordingBackend:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def synthesize(self, text: str, **kwargs: object) -> bytes:
        self.calls.append((text, dict(kwargs)))
        return self.payload


@pytest.mark.parametrize(
    (
        "provider_key",
        "voice_name",
        "quality",
        "sample_rate",
        "source_revision",
        "onnx_checksum",
        "onnx_json_checksum",
        "model_card_checksum",
        "model_license",
    ),
    [
        (
            "pl_PL-bass-high",
            "bass",
            "high",
            22_050,
            "7c89b592e94392a56c789bd35a860fec66a6583f",
            "427c7c0975ee21cea29db0f58f827883",
            "0a121543c2a697ddb48a74bbdd0fbbe9",
            "53b729f8209e4fc98d55c299055d79b5",
            "Apache-2.0",
        ),
        (
            "pl_PL-darkman-medium",
            "darkman",
            "medium",
            22_050,
            "e9ef9dd",
            "27bf2d71e934b112657544fd0b100a7a",
            "1c13180312cca98cb75ca39b31972056",
            "1a570e4294182ab00ca0e62f343f7279",
            "CC0",
        ),
        (
            "pl_PL-gosia-medium",
            "gosia",
            "medium",
            22_050,
            "e9ef9dd",
            "ecf817530e575025166e454adde1f382",
            "82fe5f840c3af4c98e8a1430431ecdbd",
            "e1355330fe5fab166e6f2e20af7e91e9",
            "CC0",
        ),
        (
            "pl_PL-mc_speech-medium",
            "mc_speech",
            "medium",
            22_050,
            "441d4ac",
            "a927e2f2c882bb40cbc2e5f3356ce19b",
            "3f506e68bb9531b11e94e5f5dda5dd21",
            "affe6073af7777237f73d0768103547e",
            "CC0",
        ),
        (
            "pl_PL-mls_6892-low",
            "mls_6892",
            "low",
            16_000,
            "5227e41",
            "8590d8e979292ca35d20e6e123bfa612",
            "7da3504b7726d6a7143a9265d9295fa1",
            "74ebc618d120896113449ad2f957b7a4",
            "CC-BY-4.0",
        ),
    ],
)
def test_catalog_records_reviewed_voice_identity_and_download_metadata(
    provider_key: str,
    voice_name: str,
    quality: str,
    sample_rate: int,
    source_revision: str,
    onnx_checksum: str,
    onnx_json_checksum: str,
    model_card_checksum: str,
    model_license: str,
) -> None:
    entry = get_piper_voice_catalog_entry(provider_key)

    identity = entry.to_identity_payload()
    catalog = entry.to_catalog_payload()
    download_urls = entry.download_urls()

    assert list_piper_voice_keys() == (
        "pl_PL-bass-high",
        "pl_PL-darkman-medium",
        "pl_PL-gosia-medium",
        "pl_PL-mc_speech-medium",
        "pl_PL-mls_6892-low",
    )
    assert tuple(item.provider_key for item in list_piper_voice_catalog()) == list_piper_voice_keys()
    assert identity == {
        "provider_key": provider_key,
        "voice_name": voice_name,
        "language_id": "pl_PL",
        "quality": quality,
        "expected_sample_rate_hz": sample_rate,
        "source_repository": "rhasspy/piper-voices",
        "source_revision": source_revision,
        "required_files": list(entry.required_files),
        "checksums": {
            entry.required_files[0]: onnx_checksum,
            entry.required_files[1]: onnx_json_checksum,
            entry.required_files[2]: model_card_checksum,
        },
        "license_identifier": {"engine": "MIT", "model": model_license},
    }
    assert catalog["download_urls"] == {
        path: f"https://huggingface.co/rhasspy/piper-voices/resolve/{source_revision}/{path}"
        for path in entry.required_files
    }
    assert catalog["model_card_url"] == (
        f"https://huggingface.co/rhasspy/piper-voices/blob/{source_revision}/{entry.model_card_path}"
    )
    assert download_urls == catalog["download_urls"]
    assert identity["required_files"][0].startswith("pl/pl_PL/")
    assert identity["checksums"][entry.required_files[0]] == onnx_checksum
    assert identity["checksums"][entry.required_files[1]] == onnx_json_checksum
    assert identity["checksums"][entry.required_files[2]] == model_card_checksum


def test_provider_includes_catalog_identity_in_effective_synthesis_identity() -> None:
    backend = RecordingBackend(_wav())
    captured: dict[str, object] = {}

    provider = PiperTTSProvider(
        model_key="pl_PL-gosia-medium",
        model_loader=lambda reference: (captured.__setitem__("reference", reference) or backend),
    )

    identity = provider.effective_synthesis_identity({"voice_mode": "catalog"})
    result = provider.synthesize("tekst")

    assert isinstance(result, TTSSynthesisResult)
    assert identity["voice"]["mode"] == "catalog"
    assert identity["voice"]["model"]["kind"] == "catalog_voice"
    assert identity["voice"]["catalog"]["provider_key"] == "pl_PL-gosia-medium"
    assert identity["voice"]["catalog"]["source_revision"] == "e9ef9dd"
    assert identity["voice"]["catalog"]["checksums"]["pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx"] == (
        "ecf817530e575025166e454adde1f382"
    )
    assert result.metadata["voice"]["mode"] == "catalog"
    assert result.metadata["voice"]["model"]["provider_key"] == "pl_PL-gosia-medium"
    assert result.metadata["voice"]["catalog"]["license_identifier"]["model"] == "CC0"
    assert captured["reference"] == {
        "model_key": "pl_PL-gosia-medium",
        "device": "cpu",
        "language_id": "pl",
        "model_identity": identity["voice"]["model"],
        "catalog_identity": identity["voice"]["catalog"],
    }
    assert backend.calls == [("tekst", {"language_id": "pl"})]


def test_catalog_keys_are_unique_and_every_entry_carries_checksums() -> None:
    catalog = list_piper_voice_catalog()
    keys = list_piper_voice_keys()

    assert len(keys) == len(set(keys))
    assert tuple(entry.provider_key for entry in catalog) == keys
    for entry in catalog:
        assert set(entry.required_files) == set(entry.checksums_payload())
        assert entry.model_card_path in entry.checksums_payload()
        assert entry.model_license_identifier
        assert entry.engine_license_identifier == "MIT"


def test_catalog_identity_changes_when_revision_or_checksum_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = get_piper_voice_catalog_entry("pl_PL-gosia-medium")
    changed = PiperVoiceCatalogEntry(
        provider_key=entry.provider_key,
        voice_name=entry.voice_name,
        language_id=entry.language_id,
        quality=entry.quality,
        expected_sample_rate_hz=entry.expected_sample_rate_hz,
        source_repository=entry.source_repository,
        source_revision="fffffff",
        required_files=entry.required_files,
        checksums=(
            (entry.required_files[0], "ffffffffffffffffffffffffffffffff"),
            (entry.required_files[1], entry.checksums_payload()[entry.required_files[1]]),
            (entry.required_files[2], entry.checksums_payload()[entry.required_files[2]]),
        ),
        engine_license_identifier=entry.engine_license_identifier,
        model_license_identifier=entry.model_license_identifier,
        model_card_path=entry.model_card_path,
    )

    monkeypatch.setattr(piper_tts_module, "get_piper_voice_catalog_entry", lambda _: entry)
    first = PiperTTSProvider(model_key="pl_PL-gosia-medium", model_loader=lambda _: RecordingBackend(_wav()))
    first_identity = first.effective_synthesis_identity()["voice"]["model"]

    monkeypatch.setattr(piper_tts_module, "get_piper_voice_catalog_entry", lambda _: changed)
    second = PiperTTSProvider(model_key="pl_PL-gosia-medium", model_loader=lambda _: RecordingBackend(_wav()))
    second_identity = second.effective_synthesis_identity()["voice"]["model"]

    assert first_identity != second_identity
    assert first_identity["source_revision"] != second_identity["source_revision"]
    assert first_identity["checksums"] != second_identity["checksums"]


def test_catalog_identity_keeps_paths_relative_and_actionable() -> None:
    entry = get_piper_voice_catalog_entry("pl_PL-mc_speech-medium")
    identity = entry.to_identity_payload()

    assert all(
        not path.startswith(("C:\\", "D:\\", "/Users/", "C:/Users/"))
        for path in entry.required_files
    )
    assert all(path.startswith("pl/pl_PL/") for path in identity["required_files"])
    serialized = json.dumps(identity, sort_keys=True)
    for private_path in ["C:\\", "D:\\", "/Users/", "C:/Users/"]:
        assert private_path not in serialized


def test_unknown_catalog_voice_is_rejected_before_runtime_loading() -> None:
    called = False

    def loader(_: dict[str, object]) -> RecordingBackend:
        nonlocal called
        called = True
        return RecordingBackend(_wav())

    with pytest.raises(PiperConfigurationError, match="Unknown Piper voice"):
        PiperTTSProvider(model_key="pl_PL-unknown-medium", model_loader=loader)

    assert not called


def test_catalog_validation_reports_missing_checksum() -> None:
    entry = get_piper_voice_catalog_entry("pl_PL-bass-high")
    broken = PiperVoiceCatalogEntry(
        provider_key=entry.provider_key,
        voice_name=entry.voice_name,
        language_id=entry.language_id,
        quality=entry.quality,
        expected_sample_rate_hz=entry.expected_sample_rate_hz,
        source_repository=entry.source_repository,
        source_revision=entry.source_revision,
        required_files=entry.required_files,
        checksums=entry.checksums[:-1],
        engine_license_identifier=entry.engine_license_identifier,
        model_license_identifier=entry.model_license_identifier,
        model_card_path=entry.model_card_path,
    )

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            piper_catalog_module,
            "_PIPER_VOICE_CATALOG",
            (broken,),
            raising=True,
        )
        monkeypatch.setattr(
            piper_catalog_module,
            "_PIPER_VOICE_BY_KEY",
            {broken.provider_key: broken},
            raising=True,
        )
        with pytest.raises(PiperCatalogError, match="missing its model card checksum"):
            piper_catalog_module.validate_piper_voice_catalog()
    finally:
        monkeypatch.undo()


def test_catalog_keys_remain_unique() -> None:
    keys = list_piper_voice_keys()
    assert len(keys) == len(set(keys))
