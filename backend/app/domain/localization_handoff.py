"""Truthful manual state for platform localization and custom-audio fallback."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import ClassVar

from app.domain.base import DomainEntity, DomainValidationError, new_id, utc_now
from app.domain.export_config import ExportConfig, LocalizationStrategy, normalize_language
from app.domain.types import JsonDict


_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|/|~/)")


@dataclass(frozen=True, slots=True)
class LocalizationDecision:
    id: str
    language: str
    decision: str
    reviewer_id: str
    comment: str
    created_at: datetime

    def to_payload(self) -> JsonDict:
        return {
            "id": self.id,
            "language": self.language,
            "decision": self.decision,
            "reviewerId": self.reviewer_id,
            "comment": self.comment,
            "createdAt": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class LocalizationTarget:
    language: str
    status: str = "pending_platform_processing"
    decision_history: list[LocalizationDecision] = field(default_factory=list)
    custom_audio: JsonDict | None = None

    def to_payload(self) -> JsonDict:
        return {
            "language": self.language,
            "status": self.status,
            "decisionHistory": [decision.to_payload() for decision in self.decision_history],
            "customAudio": dict(self.custom_audio) if self.custom_audio else None,
        }


@dataclass(slots=True)
class LocalizationHandoff(DomainEntity):
    VALID_STATUSES: ClassVar[set[str]] = {
        "pending_platform_processing",
        "available_for_review",
        "accepted",
        "rejected",
        "changes_requested",
        "unavailable",
        "custom_audio_required",
        "custom_audio_supplied",
    }
    REVIEW_DECISIONS: ClassVar[set[str]] = {"accept", "reject", "request_changes"}

    export_id: str = ""
    source_language: str = "en"
    strategy: LocalizationStrategy = LocalizationStrategy.PLATFORM_AUTO_DUB
    targets: tuple[str, ...] = ()
    manual_acceptance_required: bool = True
    custom_audio_fallback_enabled: bool = True
    provider: str = ""
    publish_ref: str = ""
    target_states: dict[str, LocalizationTarget] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        export_id: str,
        source_language: str,
        targets: Sequence[str],
        strategy: LocalizationStrategy | str,
        manual_acceptance_required: bool,
        custom_audio_fallback_enabled: bool,
        provider: str = "",
        publish_ref: str = "",
    ) -> "LocalizationHandoff":
        if not export_id.strip():
            raise DomainValidationError("LocalizationHandoff export_id is required.")
        config = ExportConfig.create(
            localization_strategy=strategy,
            localization_targets=targets,
            manual_acceptance_required=manual_acceptance_required,
            custom_audio_fallback_enabled=custom_audio_fallback_enabled,
            source_language=source_language,
        )
        normalized_source = normalize_language(source_language, field_name="source_language")
        states = {
            language: LocalizationTarget(language=language)
            for language in config.localization_targets
        }
        return cls(
            id=new_id("localization_handoff"),
            export_id=export_id.strip(),
            source_language=normalized_source,
            strategy=config.localization_strategy,
            targets=config.localization_targets,
            manual_acceptance_required=config.manual_acceptance_required,
            custom_audio_fallback_enabled=config.custom_audio_fallback_enabled,
            provider=provider.strip(),
            publish_ref=publish_ref.strip(),
            target_states=states,
        )

    def _target(self, language: str) -> LocalizationTarget:
        normalized = normalize_language(language, field_name="target language")
        try:
            return self.target_states[normalized]
        except KeyError as exc:
            raise DomainValidationError(
                f"Localization target {normalized} is not part of this handoff."
            ) from exc

    def update_platform_status(self, *, language: str, status: str) -> LocalizationTarget:
        if status not in {"pending_platform_processing", "available_for_review", "unavailable"}:
            raise DomainValidationError("Unsupported manual platform localization status.")
        target = self._target(language)
        if target.status == status:
            return target
        if target.status != "pending_platform_processing":
            raise DomainValidationError(
                f"Localization status {target.status} cannot transition to {status}."
            )
        target.status = status
        return target

    def decide(
        self,
        *,
        language: str,
        decision: str,
        reviewer_id: str,
        comment: str = "",
    ) -> LocalizationDecision:
        if decision not in self.REVIEW_DECISIONS:
            raise DomainValidationError(f"Unsupported localization decision: {decision}.")
        if not reviewer_id.strip():
            raise DomainValidationError("Localization reviewer_id is required.")
        target = self._target(language)
        status_by_decision = {
            "accept": "accepted",
            "reject": (
                "custom_audio_required"
                if self.custom_audio_fallback_enabled
                else "rejected"
            ),
            "request_changes": "changes_requested",
        }
        next_status = status_by_decision[decision]
        if target.decision_history:
            latest = target.decision_history[-1]
            if (
                target.status == next_status
                and latest.decision == decision
                and latest.reviewer_id == reviewer_id.strip()
                and latest.comment == comment.strip()
            ):
                return latest
        if target.status not in {"available_for_review", "changes_requested"}:
            raise DomainValidationError(
                f"Localization status {target.status} does not allow {decision}."
            )
        created_at = utc_now()
        identity = sha256(
            f"{self.id}|{target.language}|{decision}|{reviewer_id.strip()}|{comment.strip()}|{created_at.isoformat()}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        record = LocalizationDecision(
            id=f"localization_decision_{identity}",
            language=target.language,
            decision=decision,
            reviewer_id=reviewer_id.strip(),
            comment=comment.strip(),
            created_at=created_at,
        )
        target.decision_history.append(record)
        target.status = next_status
        return record

    def require_custom_audio(self, *, language: str) -> LocalizationTarget:
        if not self.custom_audio_fallback_enabled:
            raise DomainValidationError("Custom-audio fallback is disabled.")
        target = self._target(language)
        if target.status == "custom_audio_required":
            return target
        if target.status not in {"rejected", "unavailable", "changes_requested"}:
            raise DomainValidationError(
                f"Localization status {target.status} cannot require custom audio."
            )
        target.status = "custom_audio_required"
        return target

    def supply_custom_audio(
        self,
        *,
        language: str,
        artifact_reference: str,
        checksum: str,
        approved_label: str,
        provenance: str,
    ) -> LocalizationTarget:
        target = self._target(language)
        reference = artifact_reference.strip().replace("\\", "/")
        if (
            not reference
            or reference.startswith("/")
            or ":" in reference.split("/", 1)[0]
            or ".." in PurePosixPath(reference).parts
        ):
            raise DomainValidationError("Custom audio artifact reference must be relative.")
        if len(checksum) != 64 or any(char not in "0123456789abcdefABCDEF" for char in checksum):
            raise DomainValidationError("Custom audio checksum must be SHA-256.")
        if not approved_label.strip() or not provenance.strip():
            raise DomainValidationError(
                "Custom audio approved_label and provenance are required."
            )
        if _ABSOLUTE_PATH.search(approved_label.strip()) or _ABSOLUTE_PATH.search(
            provenance.strip()
        ):
            raise DomainValidationError(
                "Custom audio approval metadata cannot contain absolute private paths."
            )
        payload: JsonDict = {
            "targetLanguage": target.language,
            "artifactReference": reference,
            "checksum": checksum.lower(),
            "approvedLabel": approved_label.strip(),
            "provenance": provenance.strip(),
        }
        if target.status in {"rejected", "unavailable", "changes_requested"}:
            self.require_custom_audio(language=target.language)
        if target.status == "custom_audio_supplied":
            if target.custom_audio == payload:
                return target
            raise DomainValidationError("Approved custom audio cannot be overwritten.")
        if target.status != "custom_audio_required":
            raise DomainValidationError(
                f"Localization status {target.status} cannot accept custom audio."
            )
        target.custom_audio = payload
        target.status = "custom_audio_supplied"
        return target

    def to_payload(self) -> JsonDict:
        return {
            "id": self.id,
            "exportId": self.export_id,
            "sourceLanguage": self.source_language,
            "strategy": self.strategy.value,
            "targets": list(self.targets),
            "manualAcceptanceRequired": self.manual_acceptance_required,
            "customAudioFallbackEnabled": self.custom_audio_fallback_enabled,
            "provider": self.provider,
            "publishRef": self.publish_ref,
            "targetStates": {
                language: self.target_states[language].to_payload()
                for language in self.targets
            },
            "createdAt": self.created_at.isoformat(),
        }


__all__ = [
    "LocalizationDecision",
    "LocalizationHandoff",
    "LocalizationTarget",
]
