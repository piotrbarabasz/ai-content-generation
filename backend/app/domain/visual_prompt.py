"""Immutable visual prompt/context revisions and explicit scene selections."""

from dataclasses import asdict, dataclass
import json

from .dependencies import InputEdge, Provenance, RequestFingerprint, canonical_json, content_fingerprint


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("Visual prompt identities and text must be nonempty strings.")


def visual_prompt_schema():
    return {"$id": "urn:aics:visual-prompt:v1", "type": "object", "additionalProperties": False,
            "required": ["prompt"], "properties": {"prompt": {"type": "string", "minLength": 1, "pattern": r"\S"}}}


def validate_visual_prompt(payload):
    if type(payload) is not dict or set(payload) != {"prompt"}:
        raise ValueError("Visual prompt response must contain exactly prompt.")
    _text(payload["prompt"])
    return payload["prompt"]


@dataclass(frozen=True)
class PromptContextRevision:
    id: str
    context_id: str
    project_id: str
    kind: str
    text: str
    parent_revision_id: str | None = None

    def __post_init__(self):
        for value in (self.id, self.context_id, self.project_id, self.text):
            _text(value)
        if self.kind not in ("film_brief", "visual_style"):
            raise ValueError("Prompt context must be a film brief or visual style.")
        if self.parent_revision_id is not None:
            _text(self.parent_revision_id)
            if self.parent_revision_id == self.id:
                raise ValueError("Context cannot be its own parent.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if type(data.pop("version")) is not int or value["version"] != 1:
            raise ValueError("Unsupported prompt context version.")
        return cls(**data)


@dataclass(frozen=True)
class PromptInputs:
    project_id: str
    acceptance_id: str
    scene_id: str
    section_id: str
    section_revision_id: str
    brief_revision_id: str
    style_revision_id: str
    payload_json: str

    def __post_init__(self):
        for name in ("project_id", "acceptance_id", "scene_id", "section_id", "section_revision_id",
                     "brief_revision_id", "style_revision_id"):
            _text(getattr(self, name))
        payload = json.loads(self.payload_json)
        if type(payload) is not dict or set(payload) != {"scene", "section_context", "film_brief", "visual_style"}:
            raise ValueError("All visual prompt inputs must be explicitly pinned.")
        if (payload["scene"]["id"] != self.scene_id or payload["scene"]["acceptance_id"] != self.acceptance_id
                or payload["section_context"]["id"] != self.section_revision_id
                or payload["film_brief"]["id"] != self.brief_revision_id
                or payload["visual_style"]["id"] != self.style_revision_id):
            raise ValueError("Prompt input identity mismatch.")
        object.__setattr__(self, "payload_json", canonical_json(payload))

    @property
    def payload(self):
        return json.loads(self.payload_json)


def prompt_request(inputs: PromptInputs, identity: dict):
    payload = inputs.payload
    keys = {"scene": f"scene:{inputs.scene_id}:content", "section_context": f"section:{inputs.section_id}:context",
            "film_brief": "context:" + payload["film_brief"]["context_id"],
            "visual_style": "context:" + payload["visual_style"]["context_id"]}
    return RequestFingerprint.create("visual_prompt.generate", "1",
                                     inputs=[InputEdge(name, keys[name], content_fingerprint(value)) for name, value in payload.items()],
                                     settings={"schema": visual_prompt_schema()["$id"]}, effective_identity=identity)


@dataclass(frozen=True)
class VisualPromptRevision:
    id: str
    prompt: str
    inputs: PromptInputs
    request: RequestFingerprint
    provenance: Provenance
    parent_revision_id: str | None = None

    def __post_init__(self):
        _text(self.id)
        _text(self.prompt)
        object.__setattr__(self, "provenance", Provenance(self.provenance))
        if self.request != prompt_request(self.inputs, json.loads(self.request.effective_identity_json)):
            raise ValueError("Prompt request does not describe the pinned inputs.")
        if self.parent_revision_id is not None:
            _text(self.parent_revision_id)
            if self.parent_revision_id == self.id:
                raise ValueError("Prompt cannot be its own parent.")

    @property
    def output_key(self):
        return f"scene:{self.inputs.scene_id}:visual_prompt"

    def to_payload(self):
        return {"version": 1, "id": self.id, "prompt": self.prompt, "inputs": asdict(self.inputs),
                "request": self.request.to_payload(), "provenance": self.provenance.value,
                "parent_revision_id": self.parent_revision_id}

    @classmethod
    def from_payload(cls, value):
        if type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("Unsupported visual prompt version.")
        return cls(value["id"], value["prompt"], PromptInputs(**value["inputs"]),
                   RequestFingerprint.from_payload(value["request"]), value["provenance"], value["parent_revision_id"])


@dataclass(frozen=True)
class PromptSelection:
    id: str
    project_id: str
    scene_id: str
    revision_id: str
    parent_selection_id: str | None

    def __post_init__(self):
        for value in (self.id, self.project_id, self.scene_id, self.revision_id):
            _text(value)
        if self.parent_selection_id is not None:
            _text(self.parent_selection_id)
            if self.parent_selection_id == self.id:
                raise ValueError("Selection cannot be its own parent.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if type(data.pop("version")) is not int or value["version"] != 1:
            raise ValueError("Unsupported prompt selection version.")
        return cls(**data)
