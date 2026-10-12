"""Optional finishing preferences, independent of narrative and timeline values."""

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True, slots=True)
class BrandingConfig:
    channel_name: str = ""
    tagline: str = ""
    cta: str = "Subscribe for more"
    background: str = "#10141C"
    accent: str = "#7CA6BB"
    logo_id: str | None = None
    intro: bool = False
    intro_seconds: int = 3
    placement: str = "before"
    outro: bool = False
    outro_seconds: int = 15
    placeholders: bool = True

    def __post_init__(self):
        for field, limit in (("channel_name", 80), ("tagline", 120), ("cta", 160)):
            value = getattr(self, field)
            if type(value) is not str or len(value) > limit or any(ord(c) < 32 for c in value):
                raise ValueError(f"Invalid branding {field}; use a single line of at most {limit} characters.")
        for field in ("background", "accent"):
            if type(getattr(self, field)) is not str or not re.fullmatch(r"#[0-9a-fA-F]{6}", getattr(self, field)):
                raise ValueError("Branding colors must use #RRGGBB.")
        if (any(type(getattr(self, field)) is not bool for field in ("intro", "outro", "placeholders"))
                or type(self.intro_seconds) is not int or not 2 <= self.intro_seconds <= 4
                or type(self.outro_seconds) is not int or not 10 <= self.outro_seconds <= 20
                or self.placement not in {"before", "after_cold_open"}
                or self.logo_id is not None and (type(self.logo_id) is not str or not self.logo_id)):
            raise ValueError("Invalid branding duration, placement or logo selection.")

    @property
    def enabled(self):
        return self.intro or self.outro

    @property
    def extra_seconds(self):
        return (self.intro_seconds if self.intro else 0) + (self.outro_seconds if self.outro else 0)

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, payload):
        if type(payload) is not dict or payload.get("version") != 1 or set(payload) != set(cls().to_payload()):
            raise ValueError("Invalid branding settings payload.")
        return cls(**{k: v for k, v in payload.items() if k != "version"})
