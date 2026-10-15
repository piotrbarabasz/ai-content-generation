"""Strict response contract for generating one planned group at a time."""


def planned_script_group_schema(section_count=1, *, version=2):
    if version == 2:
        slots = {f"section_{i + 1}": {"type": "string", "minLength": 1, "pattern": r"\S"}
                 for i in range(section_count)}
        return {"$id": "urn:aics:planned-script-group:v2", "type": "object", "additionalProperties": False,
                "required": ["sections"], "properties": {"sections": {"type": "object",
                    "additionalProperties": False, "required": list(slots), "properties": slots}}}
    if version != 1:
        raise ValueError("Unsupported planned script response version.")
    return {"$id": "urn:aics:planned-script-group:v1", "type": "object", "additionalProperties": False,
            "required": ["sections"], "properties": {"sections": {"type": "array", "minItems": section_count, "maxItems": section_count,
                "items": {"type": "object", "additionalProperties": False,
                    "required": ["planned_section_id", "text"],
                    "properties": {"planned_section_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                                   "text": {"type": "string", "minLength": 1, "pattern": r"\S"}}}}}}


def validate_planned_script_group(payload, expected_ids, *, version=1):
    if version == 2:
        slots = tuple(f"section_{i + 1}" for i in range(len(expected_ids)))
        if (type(payload) is not dict or set(payload) != {"sections"}
                or type(payload["sections"]) is not dict or set(payload["sections"]) != set(slots)):
            raise ValueError("Generated group must contain exactly the expected section slots: " + ", ".join(slots))
        texts = tuple(payload["sections"][slot] for slot in slots)
        if any(type(text) is not str or not text.strip() for text in texts):
            raise ValueError("Generated group section text must be nonempty narration.")
        if len({" ".join(text.split()).casefold() for text in texts}) != len(texts):
            raise ValueError("Generated group contains duplicate narration across section slots.")
        return texts
    if version != 1:
        raise ValueError("Unsupported planned script response version.")
    if type(payload) is not dict or set(payload) != {"sections"} or type(payload["sections"]) is not list:
        raise ValueError("Generated group must contain only a sections array.")
    sections = payload["sections"]
    if len(sections) != len(expected_ids):
        raise ValueError("Generated group has missing or extra planned sections.")
    result = []
    for value, expected_id in zip(sections, expected_ids):
        if type(value) is not dict or set(value) != {"planned_section_id", "text"}:
            raise ValueError("Generated group section has unknown or missing fields.")
        if type(value["planned_section_id"]) is not str or value["planned_section_id"] != expected_id:
            raise ValueError("Generated group planned section identities or order differ from the plan.")
        if type(value["text"]) is not str or not value["text"].strip():
            raise ValueError("Generated group section text must be nonempty.")
        result.append(value["text"])
    return tuple(result)
