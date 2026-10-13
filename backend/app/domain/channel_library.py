"""Pinned shared branding values; no project or provider dependencies."""
from dataclasses import asdict, dataclass
import re

from app.domain.branding import BrandingConfig


def identifier(value):
    if type(value) is not str or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
        raise ValueError("Invalid channel/library identity.")
    return value


@dataclass(frozen=True, slots=True)
class ChannelProfile:
    channel_id: str
    revision_id: str
    channel_name: str
    tagline: str = ""
    cta: str = "Subscribe for more"
    link: str = ""
    logo_version: str | None = None
    intro_version: str | None = None
    outro_version: str | None = None
    placement: str = "after_cold_open"
    intro: bool = True
    outro: bool = True
    background: str = "#10141C"
    accent: str = "#7CA6BB"
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        identifier(self.channel_id); identifier(self.revision_id)
        BrandingConfig(channel_name=self.channel_name, tagline=self.tagline, cta=self.cta,
            placement=self.placement, intro=self.intro, outro=self.outro,
            background=self.background, accent=self.accent)
        if not self.channel_name.strip() or type(self.link) is not str or len(self.link) > 500 or (self.link and not re.fullmatch(r"https?://[^\s<>]+", self.link)):
            raise ValueError("Channel requires a name and an optional HTTP(S) link; never store credentials.")
        for value in (self.logo_version, self.intro_version, self.outro_version):
            if value is not None: identifier(value)

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        if type(value) is not dict or value.get("version") != 1 or set(value) != {"version", *cls.__dataclass_fields__}:
            raise ValueError("Malformed channel profile.")
        return cls(**{k:v for k,v in value.items() if k != "version"})


@dataclass(frozen=True, slots=True)
class ReusableBrandingConfig:
    channel_id: str
    profile_revision: str
    channel_name: str
    intro_asset: dict | None = None
    outro_asset: dict | None = None
    logo_asset: dict | None = None
    intro: bool = True
    outro: bool = True
    placement: str = "after_cold_open"
    fit: str = "contain"

    def __post_init__(self):
        identifier(self.channel_id); identifier(self.profile_revision)
        if type(self.channel_name) is not str or not self.channel_name.strip() or len(self.channel_name) > 80:
            raise ValueError("Invalid selected channel name.")
        if type(self.intro) is not bool or type(self.outro) is not bool or self.placement not in {"before", "after_cold_open"} or self.fit not in {"contain", "reject"}:
            raise ValueError("Invalid reusable clip placement/fit preferences.")
        for field in ("intro_asset", "outro_asset", "logo_asset"):
            value = getattr(self, field)
            if value is not None:
                if type(value) is not dict or set(value) != {"version_id", "checksum", "duration"}:
                    raise ValueError("Asset selection requires an exact version, checksum and measured duration.")
                identifier(value["version_id"])
                if not re.fullmatch(r"[a-f0-9]{64}", value["checksum"]): raise ValueError("Invalid asset checksum.")
                from fractions import Fraction
                if type(value["duration"]) is not list or len(value["duration"]) != 2 or any(type(n) is not int for n in value["duration"]) or value["duration"][1] <= 0 or Fraction(*value["duration"]) < 0:
                    raise ValueError("Invalid measured asset duration.")
                object.__setattr__(self, field, {**value, "duration": list(value["duration"])})

    @property
    def enabled(self): return self.intro or self.outro
    @property
    def logo_id(self): return None  # Shared logo is not a project artifact alias.
    @property
    def intro_seconds(self):
        from fractions import Fraction
        return Fraction(*self.intro_asset["duration"]) if self.intro_asset else Fraction(0)
    @property
    def outro_seconds(self):
        from fractions import Fraction
        return Fraction(*self.outro_asset["duration"]) if self.outro_asset else Fraction(0)
    @property
    def extra_seconds(self): return (self.intro_seconds if self.intro else 0) + (self.outro_seconds if self.outro else 0)
    def to_payload(self): return {"version": 2, "mode": "reusable_clips", **asdict(self)}
    @classmethod
    def from_payload(cls, value):
        if type(value) is not dict or value.get("version") != 2 or value.get("mode") != "reusable_clips" or set(value) != {"version", "mode", *cls.__dataclass_fields__}:
            raise ValueError("Malformed reusable branding selection.")
        return cls(**{k:v for k,v in value.items() if k not in {"version", "mode"}})


def branding_config(value):
    return ReusableBrandingConfig.from_payload(value) if value.get("version") == 2 else BrandingConfig.from_payload(value)
