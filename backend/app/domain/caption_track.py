"""Validated caption timing and persisted track references."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from app.domain.base import DomainEntity, DomainValidationError, new_id


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    id: str
    index: int
    start_ms: int
    end_ms: int
    text: str


def validate_caption_segments(
    values: Sequence[Mapping[str, object]],
) -> tuple[CaptionSegment, ...]:
    """Validate canonical structured timing before any subtitle persistence."""

    if not values:
        raise DomainValidationError("Caption segments cannot be empty.")
    segments: list[CaptionSegment] = []
    previous_end = 0
    for index, value in enumerate(values, start=1):
        if not isinstance(value, Mapping):
            raise DomainValidationError("Each caption segment must be an object.")
        raw_start = value.get("start_ms", value.get("startMs"))
        raw_end = value.get("end_ms", value.get("endMs"))
        if isinstance(raw_start, bool) or isinstance(raw_end, bool):
            raise DomainValidationError("Caption timestamps must be integer milliseconds.")
        if isinstance(raw_start, float) and not raw_start.is_integer():
            raise DomainValidationError("Caption timestamps must be integer milliseconds.")
        if isinstance(raw_end, float) and not raw_end.is_integer():
            raise DomainValidationError("Caption timestamps must be integer milliseconds.")
        try:
            start_ms = int(raw_start)
            end_ms = int(raw_end)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("Caption timestamps must be integer milliseconds.") from exc
        text = str(value.get("text", "")).strip()
        if not text:
            raise DomainValidationError("Caption segment text cannot be empty.")
        if start_ms < 0 or end_ms < 0:
            raise DomainValidationError("Caption timestamps cannot be negative.")
        if start_ms >= end_ms:
            raise DomainValidationError("Caption segment start must be before end.")
        if index > 1 and start_ms < previous_end:
            raise DomainValidationError("Caption segments must be monotonic and cannot overlap.")
        signature = sha256(
            f"{index}|{start_ms}|{end_ms}|{text}".encode("utf-8")
        ).hexdigest()[:12]
        segments.append(
            CaptionSegment(
                id=f"caption_{signature}",
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )
        previous_end = end_ms
    return tuple(segments)


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def serialize_srt(segments: Sequence[CaptionSegment]) -> str:
    """Serialize deterministic UTF-8-ready SRT with CRLF line endings."""

    blocks = [
        f"{segment.index}\r\n"
        f"{_srt_timestamp(segment.start_ms)} --> {_srt_timestamp(segment.end_ms)}\r\n"
        f"{segment.text}"
        for segment in segments
    ]
    return "\r\n\r\n".join(blocks) + "\r\n"


@dataclass(slots=True)
class CaptionTrack(DomainEntity):
    workflow_run_id: str = ""
    provider: str = ""
    caption_storage_key: str = ""
    srt_storage_key: str = ""
    language: str = "en"
    approved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        provider: str,
        caption_storage_key: str,
        srt_storage_key: str = "",
        language: str = "en",
        approved_at: datetime | None = None,
    ) -> "CaptionTrack":
        if not workflow_run_id.strip():
            raise DomainValidationError("CaptionTrack workflow_run_id is required.")
        if not provider.strip():
            raise DomainValidationError("CaptionTrack provider is required.")
        if not caption_storage_key.strip():
            raise DomainValidationError("CaptionTrack caption_storage_key is required.")
        if not language.strip():
            raise DomainValidationError("CaptionTrack language is required.")

        return cls(
            id=new_id("caption_track"),
            workflow_run_id=workflow_run_id,
            provider=provider,
            caption_storage_key=caption_storage_key,
            srt_storage_key=srt_storage_key,
            language=language.strip().lower(),
            approved_at=approved_at,
        )


__all__ = [
    "CaptionSegment",
    "CaptionTrack",
    "serialize_srt",
    "validate_caption_segments",
]
