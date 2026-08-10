"""Curated Piper voice catalog for Polish voices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.types import JsonDict


PIPER_VOICE_SOURCE_REPOSITORY = "rhasspy/piper-voices"
PIPER_VOICE_KEYS = (
    "pl_PL-bass-high",
    "pl_PL-darkman-medium",
    "pl_PL-gosia-medium",
    "pl_PL-mc_speech-medium",
    "pl_PL-mls_6892-low",
)


class PiperCatalogError(ValueError):
    """Raised when the curated Piper catalog is misconfigured or incomplete."""


def _download_url(source_revision: str, relative_path: str) -> str:
    return f"https://huggingface.co/{PIPER_VOICE_SOURCE_REPOSITORY}/resolve/{source_revision}/{relative_path}"


def _model_card_url(source_revision: str, relative_path: str) -> str:
    return f"https://huggingface.co/{PIPER_VOICE_SOURCE_REPOSITORY}/blob/{source_revision}/{relative_path}"


@dataclass(frozen=True, slots=True)
class PiperVoiceCatalogEntry:
    """Immutable metadata for one curated Piper voice asset."""

    provider_key: str
    voice_name: str
    language_id: str
    quality: str
    expected_sample_rate_hz: int
    source_repository: str
    source_revision: str
    required_files: tuple[str, ...]
    checksums: tuple[tuple[str, str], ...]
    engine_license_identifier: str
    model_license_identifier: str
    model_card_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_key, str) or not self.provider_key.strip():
            raise PiperCatalogError("Piper catalog provider_key must be a non-empty string.")
        if not isinstance(self.voice_name, str) or not self.voice_name.strip():
            raise PiperCatalogError("Piper catalog voice_name must be a non-empty string.")
        if not isinstance(self.language_id, str) or not self.language_id.strip():
            raise PiperCatalogError("Piper catalog language_id must be a non-empty string.")
        if not isinstance(self.quality, str) or not self.quality.strip():
            raise PiperCatalogError("Piper catalog quality must be a non-empty string.")
        if not isinstance(self.expected_sample_rate_hz, int) or self.expected_sample_rate_hz <= 0:
            raise PiperCatalogError("Piper catalog expected_sample_rate_hz must be a positive integer.")
        if not isinstance(self.source_repository, str) or not self.source_repository.strip():
            raise PiperCatalogError("Piper catalog source_repository must be a non-empty string.")
        if not isinstance(self.source_revision, str) or not self.source_revision.strip():
            raise PiperCatalogError("Piper catalog source_revision must be a non-empty string.")
        if not self.required_files:
            raise PiperCatalogError("Piper catalog required_files cannot be empty.")
        if not self.checksums:
            raise PiperCatalogError("Piper catalog checksums cannot be empty.")
        if not isinstance(self.engine_license_identifier, str) or not self.engine_license_identifier.strip():
            raise PiperCatalogError("Piper catalog engine_license_identifier must be a non-empty string.")
        if not isinstance(self.model_license_identifier, str) or not self.model_license_identifier.strip():
            raise PiperCatalogError("Piper catalog model_license_identifier must be a non-empty string.")
        if not isinstance(self.model_card_path, str) or not self.model_card_path.strip():
            raise PiperCatalogError("Piper catalog model_card_path must be a non-empty string.")

    def download_urls(self) -> JsonDict:
        """Return reproducible download URLs for the curated asset files."""

        return {
            path: _download_url(self.source_revision, path)
            for path in self.required_files
        }

    def model_card_url(self) -> str:
        """Return the review artifact URL for the model card."""

        return _model_card_url(self.source_revision, self.model_card_path)

    def checksums_payload(self) -> JsonDict:
        return {path: checksum for path, checksum in self.checksums}

    def to_identity_payload(self) -> JsonDict:
        """Return JSON-friendly identity evidence for cache keys and manifests."""

        return {
            "provider_key": self.provider_key,
            "voice_name": self.voice_name,
            "language_id": self.language_id,
            "quality": self.quality,
            "expected_sample_rate_hz": self.expected_sample_rate_hz,
            "source_repository": self.source_repository,
            "source_revision": self.source_revision,
            "required_files": list(self.required_files),
            "checksums": self.checksums_payload(),
            "license_identifier": {
                "engine": self.engine_license_identifier,
                "model": self.model_license_identifier,
            },
        }

    def to_catalog_payload(self) -> JsonDict:
        """Return the complete catalog record, including download and review links."""

        payload = self.to_identity_payload()
        payload["download_urls"] = self.download_urls()
        payload["model_card_url"] = self.model_card_url()
        return payload


def _catalog_entry(
    *,
    provider_key: str,
    voice_name: str,
    quality: str,
    expected_sample_rate_hz: int,
    source_revision: str,
    relative_dir: str,
    onnx_checksum: str,
    onnx_json_checksum: str,
    model_card_checksum: str,
    model_license_identifier: str,
) -> PiperVoiceCatalogEntry:
    model_path = f"{relative_dir}/{provider_key}.onnx"
    config_path = f"{relative_dir}/{provider_key}.onnx.json"
    model_card_path = f"{relative_dir}/MODEL_CARD"
    return PiperVoiceCatalogEntry(
        provider_key=provider_key,
        voice_name=voice_name,
        language_id="pl_PL",
        quality=quality,
        expected_sample_rate_hz=expected_sample_rate_hz,
        source_repository=PIPER_VOICE_SOURCE_REPOSITORY,
        source_revision=source_revision,
        required_files=(model_path, config_path, model_card_path),
        checksums=(
            (model_path, onnx_checksum),
            (config_path, onnx_json_checksum),
            (model_card_path, model_card_checksum),
        ),
        engine_license_identifier="MIT",
        model_license_identifier=model_license_identifier,
        model_card_path=model_card_path,
    )


_PIPER_VOICE_CATALOG = (
    _catalog_entry(
        provider_key="pl_PL-bass-high",
        voice_name="bass",
        quality="high",
        expected_sample_rate_hz=22_050,
        source_revision="834f23262168a7e809179465e4113f23f5a7d1f7",
        relative_dir="pl/pl_PL/bass/high",
        onnx_checksum="427c7c0975ee21cea29db0f58f827883",
        onnx_json_checksum="0a121543c2a697ddb48a74bbdd0fbbe9",
        model_card_checksum="53b729f8209e4fc98d55c299055d79b5",
        model_license_identifier="Apache-2.0",
    ),
    _catalog_entry(
        provider_key="pl_PL-darkman-medium",
        voice_name="darkman",
        quality="medium",
        expected_sample_rate_hz=22_050,
        source_revision="e9ef9dd",
        relative_dir="pl/pl_PL/darkman/medium",
        onnx_checksum="27bf2d71e934b112657544fd0b100a7a",
        onnx_json_checksum="1c13180312cca98cb75ca39b31972056",
        model_card_checksum="1a570e4294182ab00ca0e62f343f7279",
        model_license_identifier="CC0",
    ),
    _catalog_entry(
        provider_key="pl_PL-gosia-medium",
        voice_name="gosia",
        quality="medium",
        expected_sample_rate_hz=22_050,
        source_revision="5e74c24a88ed7d31e308633fa1542433ce2b28d4",
        relative_dir="pl/pl_PL/gosia/medium",
        onnx_checksum="ecf817530e575025166e454adde1f382",
        onnx_json_checksum="82fe5f840c3af4c98e8a1430431ecdbd",
        model_card_checksum="e1355330fe5fab166e6f2e20af7e91e9",
        model_license_identifier="CC0",
    ),
    _catalog_entry(
        provider_key="pl_PL-mc_speech-medium",
        voice_name="mc_speech",
        quality="medium",
        expected_sample_rate_hz=22_050,
        source_revision="441d4ac",
        relative_dir="pl/pl_PL/mc_speech/medium",
        onnx_checksum="a927e2f2c882bb40cbc2e5f3356ce19b",
        onnx_json_checksum="3f506e68bb9531b11e94e5f5dda5dd21",
        model_card_checksum="affe6073af7777237f73d0768103547e",
        model_license_identifier="CC0",
    ),
    _catalog_entry(
        provider_key="pl_PL-mls_6892-low",
        voice_name="mls_6892",
        quality="low",
        expected_sample_rate_hz=16_000,
        source_revision="5227e41",
        relative_dir="pl/pl_PL/mls_6892/low",
        onnx_checksum="8590d8e979292ca35d20e6e123bfa612",
        onnx_json_checksum="7da3504b7726d6a7143a9265d9295fa1",
        model_card_checksum="74ebc618d120896113449ad2f957b7a4",
        model_license_identifier="CC-BY-4.0",
    ),
)

_PIPER_VOICE_BY_KEY = {entry.provider_key: entry for entry in _PIPER_VOICE_CATALOG}

if len(_PIPER_VOICE_BY_KEY) != len(_PIPER_VOICE_CATALOG):
    raise PiperCatalogError("Piper voice catalog contains duplicate provider keys.")


def list_piper_voice_keys() -> tuple[str, ...]:
    """Return the curated Polish Piper voice keys in reproducible order."""

    return PIPER_VOICE_KEYS


def list_piper_voice_catalog() -> tuple[PiperVoiceCatalogEntry, ...]:
    """Return all curated voice entries in reproducible order."""

    return _PIPER_VOICE_CATALOG


def get_piper_voice_catalog_entry(provider_key: str) -> PiperVoiceCatalogEntry:
    """Resolve one curated voice entry or raise a descriptive error."""

    if not isinstance(provider_key, str) or not provider_key.strip():
        raise PiperCatalogError("Piper voice provider_key must be a non-empty string.")
    try:
        return _PIPER_VOICE_BY_KEY[provider_key.strip()]
    except KeyError as exc:
        supported = ", ".join(PIPER_VOICE_KEYS)
        raise PiperCatalogError(
            f"Unknown Piper voice '{provider_key}'. Supported Polish voices: {supported}."
        ) from exc


def validate_piper_voice_catalog() -> None:
    """Validate the curated catalog and surface configuration mistakes early."""

    for entry in _PIPER_VOICE_CATALOG:
        if entry.provider_key not in _PIPER_VOICE_BY_KEY:
            raise PiperCatalogError(f"Missing catalog key for voice '{entry.provider_key}'.")
        if not entry.required_files:
            raise PiperCatalogError(f"Voice '{entry.provider_key}' does not define required files.")
        if not entry.checksums:
            raise PiperCatalogError(f"Voice '{entry.provider_key}' does not define checksums.")
        if entry.model_card_path not in entry.checksums_payload():
            raise PiperCatalogError(f"Voice '{entry.provider_key}' is missing its model card checksum.")
        if entry.expected_sample_rate_hz not in {16_000, 22_050}:
            raise PiperCatalogError(
                f"Voice '{entry.provider_key}' has an unsupported sample rate."
            )


validate_piper_voice_catalog()
