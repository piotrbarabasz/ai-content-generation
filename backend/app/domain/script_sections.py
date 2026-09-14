"""Fixed desktop structured-output contract; no coercion or template fallback."""

from dataclasses import dataclass


SCRIPT_SECTIONS_SCHEMA_ID = "urn:aics:script-sections:v1"


def script_sections_schema():
    """A fresh schema per request: a provider cannot mutate later validation rules."""
    return {
        "$id": SCRIPT_SECTIONS_SCHEMA_ID,
        "type": "object",
        "additionalProperties": False,
        "required": ["sections"],
        "properties": {
            "sections": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["title", "role", "text"],
                    "properties": {name: {"type": "string", "minLength": 1, "pattern": r"\S"}
                                   for name in ("title", "role", "text")},
                },
            },
        },
    }


@dataclass(frozen=True)
class ScriptSectionInput:
    title: str
    role: str
    text: str

    def __post_init__(self):
        for name in ("title", "role", "text"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"Script section {name} must be nonempty text.")


def validate_script_sections(payload) -> tuple[ScriptSectionInput, ...]:
    """Validate the complete v1 contract and freeze all fields before any write.

    Array order is authoritative. Roles are editorial labels, not unique keys;
    no required hook/close/CTA, deduplication, trimming or role-driven rewriting.
    """
    if type(payload) is not dict or set(payload) != {"sections"}:
        raise ValueError("Structured script must contain only a sections array.")
    sections = payload["sections"]
    if type(sections) is not list or not sections:
        raise ValueError("Structured script requires a nonempty sections array.")
    result = []
    for section in sections:
        if type(section) is not dict or set(section) != {"title", "role", "text"}:
            raise ValueError("Every script section must contain exactly title, role and text.")
        result.append(ScriptSectionInput(**section))
    return tuple(result)
