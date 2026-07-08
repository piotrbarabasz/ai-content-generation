"""Canonical enum values for AI Content Studio."""

from enum import StrEnum


class WorkflowPreset(StrEnum):
    SHORT_VIDEO = "short_video"
    LONG_FORM_SCRIPT_VOICEOVER = "long_form_script_voiceover"


class ContentType(StrEnum):
    SHORT_VIDEO = "short_video"
    LONG_FORM_VIDEO = "long_form_video"
    AUDIO_ONLY = "audio_only"
    SCRIPT_ONLY = "script_only"


class ContentGenre(StrEnum):
    NEWS = "news"
    STORY = "story"
    DOCUMENTARY = "documentary"
    EDUCATIONAL = "educational"
    TUTORIAL = "tutorial"
    MARKETING = "marketing"
    COMMENTARY = "commentary"
    LISTICLE = "listicle"


class DurationProfile(StrEnum):
    SHORT_15_30S = "15_30s"
    SIXTY_SECONDS = "60s"
    THREE_FIVE_MINUTES = "3_5min"
    EIGHT_FIFTEEN_MINUTES = "8_15min"
    CUSTOM = "custom"


class TargetPlatform(StrEnum):
    TIKTOK = "tiktok"
    YOUTUBE_SHORTS = "youtube_shorts"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    GENERIC_EXPORT = "generic_export"


class ProviderType(StrEnum):
    LLM = "LLMProvider"
    TTS = "TTSProvider"
    TRANSCRIPTION = "TranscriptionProvider"
    CAPTION = "CaptionProvider"
    ASSET = "AssetProvider"
    VIDEO_RENDERER = "VideoRendererProvider"
    STORAGE = "StorageProvider"
    PUBLISHING = "PublishingProvider"
