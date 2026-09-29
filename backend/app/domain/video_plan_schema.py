"""Strict semantic structured output contract for Video Plan generation."""


VIDEO_PLAN_SCHEMA_ID = "urn:aics:video-plan:v1"


def video_plan_schema(video_format=None):
    from app.domain.video_plan import VideoFormat
    fmt = VideoFormat(video_format) if video_format is not None else None
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}
    section = {"type": "object", "additionalProperties": False, "required": ["title", "role", "purpose", "weight"],
               "properties": {"title": text, "role": text, "purpose": text, "weight": {"type": "integer", "minimum": 1}}}
    group = {"type": "object", "additionalProperties": False,
             "required": ["kind", "title", "purpose", "weight", "sections"],
             "properties": {"kind": text, "title": text, "purpose": text,
                            "weight": {"type": "integer", "minimum": 1},
                            "sections": {"type": "array", "minItems": 1, "items": section}}}
    return {"$id": VIDEO_PLAN_SCHEMA_ID, "type": "object", "additionalProperties": False,
            "required": ["working_title", "film_brief", "visual_style", "groups"],
            "properties": {"working_title": text, "film_brief": text, "visual_style": text,
                           "groups": {"type": "array", "minItems": 4 if fmt is not VideoFormat.STANDARD else 7,
                                      "maxItems": 8 if fmt is not VideoFormat.STANDARD else 10, "items": group}}}


def validate_video_plan_output(payload, video_format):
    from app.domain.video_plan import VideoFormat
    fmt = VideoFormat(video_format)
    if type(payload) is not dict or set(payload) != {"working_title", "film_brief", "visual_style", "groups"}:
        raise ValueError("Video plan response has unknown or missing fields.")
    for key in ("working_title", "film_brief", "visual_style"):
        if type(payload[key]) is not str or not payload[key].strip():
            raise ValueError(f"Video plan {key} must be nonempty text.")
    groups = payload["groups"]
    limits = (7, 10) if fmt is VideoFormat.STANDARD else (4, 8)
    if type(groups) is not list or not limits[0] <= len(groups) <= limits[1]:
        raise ValueError("Video plan group count is outside the format profile.")
    if fmt is VideoFormat.STANDARD and sum(g.get("kind") == "chapter" for g in groups if type(g) is dict) not in range(5, 8):
        raise ValueError("Standard plan requires five to seven chapter groups.")
    clean = []
    for g in groups:
        if type(g) is not dict or set(g) != {"kind", "title", "purpose", "weight", "sections"}:
            raise ValueError("Malformed video plan group.")
        if any(type(g[k]) is not str or not g[k].strip() for k in ("kind", "title", "purpose")) or type(g["weight"]) is not int or g["weight"] <= 0:
            raise ValueError("Invalid video plan group content or weight.")
        if type(g["sections"]) is not list or not g["sections"]:
            raise ValueError("Video plan group requires sections.")
        if fmt is VideoFormat.SOCIAL and len(g["sections"]) != 1:
            raise ValueError("Social groups must each contain one section.")
        sections = []
        for s in g["sections"]:
            if type(s) is not dict or set(s) != {"title", "role", "purpose", "weight"}:
                raise ValueError("Malformed planned section.")
            if any(type(s[k]) is not str or not s[k].strip() for k in ("title", "role", "purpose")) or type(s["weight"]) is not int or s["weight"] <= 0:
                raise ValueError("Invalid planned section content or weight.")
            sections.append(dict(s))
        clean.append({**g, "sections": sections})
    if fmt is VideoFormat.SOCIAL and groups[0]["kind"] not in {"hook", "opening", "cold_open"}:
        raise ValueError("Social plan must begin with an opening group.")
    if fmt is VideoFormat.SOCIAL and not any(g["kind"] in {"body", "development", "explanation", "payoff", "fact"} for g in clean[1:]):
        raise ValueError("Social plan requires a development group.")
    if fmt is VideoFormat.STANDARD and groups[0]["kind"] not in {"cold_open", "opening"}:
        raise ValueError("Standard plan must begin with a cold open.")
    if fmt is VideoFormat.STANDARD:
        kinds = [g["kind"] for g in clean]
        if "introduction" not in kinds or "conclusion" not in kinds:
            raise ValueError("Standard plan requires an introduction and conclusion.")
    return {**payload, "groups": clean}
