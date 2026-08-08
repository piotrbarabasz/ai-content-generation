"""Provider-neutral export and localization configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.base import DomainValidationError
from app.domain.types import JsonDict


_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class LocalizationStrategy(StrEnum):
    NONE = "none"
    PLATFORM_AUTO_DUB = "platform_auto_dub"
    CUSTOM_AUDIO_TRACKS = "custom_audio_tracks"


def normalize_language(value: object, *, field_name: str) -> str:
    language = str(value).strip().lower()
    if not language:
        raise DomainValidationError(f"ExportConfig {field_name} cannot contain an empty language.")
    if not _LANGUAGE_TAG.fullmatch(language):
        raise DomainValidationError(
            f"ExportConfig {field_name} contains an invalid language tag: {language}."
        )
    return language


@dataclass(frozen=True, slots=True)
class ExportConfig:
    """Validated localization policy kept separate from source-language generation."""

    localization_strategy: LocalizationStrategy = LocalizationStrategy.NONE
    localization_targets: tuple[str, ...] = ()
    manual_acceptance_required: bool = False
    custom_audio_fallback_enabled: bool = False

    @classmethod
    def create(
        cls,
        *,
        localization_strategy: LocalizationStrategy | str = LocalizationStrategy.NONE,
        localization_targets: Sequence[str] | None = None,
        manual_acceptance_required: bool = False,
        custom_audio_fallback_enabled: bool = False,
        source_language: str | None = None,
    ) -> "ExportConfig":
        try:
            strategy = LocalizationStrategy(localization_strategy)
        except ValueError as exc:
            raise DomainValidationError(
                f"Unsupported localization strategy: {localization_strategy}."
            ) from exc

        if not isinstance(manual_acceptance_required, bool):
            raise DomainValidationError(
                "ExportConfig manual_acceptance_required must be a boolean."
            )
        if not isinstance(custom_audio_fallback_enabled, bool):
            raise DomainValidationError(
                "ExportConfig custom_audio_fallback_enabled must be a boolean."
            )
        if localization_targets is None:
            raw_targets: Sequence[str] = ()
        elif isinstance(localization_targets, (str, bytes)):
            raise DomainValidationError("ExportConfig localization_targets must be a list.")
        else:
            raw_targets = localization_targets

        targets = tuple(
            normalize_language(value, field_name="localization_targets")
            for value in raw_targets
        )
        if len(set(targets)) != len(targets):
            raise DomainValidationError("ExportConfig localization_targets must be unique.")

        if source_language is not None:
            normalized_source = normalize_language(source_language, field_name="source_language")
            if normalized_source in targets:
                raise DomainValidationError(
                    "ExportConfig localization_targets cannot include the source language."
                )

        if strategy is LocalizationStrategy.NONE and targets:
            raise DomainValidationError(
                "ExportConfig strategy none requires no localization targets."
            )
        if strategy is not LocalizationStrategy.NONE and not targets:
            raise DomainValidationError(
                f"ExportConfig strategy {strategy.value} requires at least one localization target."
            )

        return cls(
            localization_strategy=strategy,
            localization_targets=targets,
            manual_acceptance_required=manual_acceptance_required,
            custom_audio_fallback_enabled=custom_audio_fallback_enabled,
        )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | "ExportConfig" | None,
        *,
        source_language: str | None = None,
    ) -> "ExportConfig":
        if isinstance(payload, cls):
            return cls.create(
                localization_strategy=payload.localization_strategy,
                localization_targets=payload.localization_targets,
                manual_acceptance_required=payload.manual_acceptance_required,
                custom_audio_fallback_enabled=payload.custom_audio_fallback_enabled,
                source_language=source_language,
            )
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise DomainValidationError("WorkflowConfig export_config must be an object.")

        aliases = {
            "localizationStrategy": "localization_strategy",
            "localizationTargets": "localization_targets",
            "manualAcceptanceRequired": "manual_acceptance_required",
            "customAudioFallbackEnabled": "custom_audio_fallback_enabled",
        }
        normalized = {aliases.get(str(key), str(key)): value for key, value in payload.items()}
        allowed = {
            "localization_strategy",
            "localization_targets",
            "manual_acceptance_required",
            "custom_audio_fallback_enabled",
        }
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise DomainValidationError(
                "Unknown ExportConfig field(s): " + ", ".join(unknown) + "."
            )
        return cls.create(source_language=source_language, **normalized)

    def to_payload(self) -> JsonDict:
        return {
            "localizationStrategy": self.localization_strategy.value,
            "localizationTargets": list(self.localization_targets),
            "manualAcceptanceRequired": self.manual_acceptance_required,
            "customAudioFallbackEnabled": self.custom_audio_fallback_enabled,
        }


__all__ = ["ExportConfig", "LocalizationStrategy", "normalize_language"]
