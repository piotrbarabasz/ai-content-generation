"""Generate and retain immutable Social / Standard video plans."""

from app.domain.base import new_id
from app.domain.video_plan import (VIDEO_FORMATS, WORDS_PER_SECOND, PlannedSection,
                                   VideoFormat, VideoPlanGroup, VideoPlanRevision)
from app.domain.video_plan_schema import validate_video_plan_output, video_plan_schema


def _allocate(total, weights):
    """Largest remainder allocation; ties stay in original editorial order."""
    weight_sum = sum(weights)
    floors = [(total * weight) // weight_sum for weight in weights]
    remainder = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % weight_sum), i))
    for index in order[:remainder]:
        floors[index] += 1
    if any(value < 1 for value in floors):
        raise ValueError("Target duration is too small for the proposed plan structure.")
    return floors


def _planner_prompt(fmt, target_duration_seconds, topic):
    if fmt is VideoFormat.SOCIAL:
        guidance = (
            "Create 4–8 flat groups. Every group must contain exactly one section; "
            "do not create nested or multiple sections inside a Social group. The first group "
            "must be a hook, opening, or cold_open. Include at least one development, body, "
            "explanation, payoff, or fact group. Use concise beats suitable for roughly 4–8 "
            "second visual pacing. Return content matching the supplied strict schema."
        )
    else:
        guidance = (
            "Start with a cold_open, include an introduction, create 5–7 chapter groups, "
            "and include a conclusion; chapters may contain multiple planned sections. "
            "Shape this as a target long-form 9 to 11 minute explanatory structure. "
            "Return content matching the supplied strict schema."
        )
    return f"Create a {fmt.value} video plan of {target_duration_seconds} seconds about: {topic}\n{guidance}"


class VideoPlanningService:
    def __init__(self, plans, provider):
        self.plans, self.provider = plans, provider

    def generate(self, *, project_id, language, video_format, target_duration_seconds, topic, parent_revision_id=None):
        if project_id != self.plans.project_id:
            raise ValueError("Video plan belongs to a different project.")
        fmt = VideoFormat(video_format)
        spec = VIDEO_FORMATS[fmt]
        if type(target_duration_seconds) is not int or not spec.min_duration_seconds <= target_duration_seconds <= spec.max_duration_seconds:
            raise ValueError("Target duration is outside the selected format profile.")
        if type(topic) is not str or not topic.strip():
            raise ValueError("Topic is required.")
        semantic = self.provider.generate_structured(
            _planner_prompt(fmt, target_duration_seconds, topic), video_plan_schema(fmt))
        semantic = validate_video_plan_output(semantic, fmt)
        group_durations = _allocate(target_duration_seconds, [g["weight"] for g in semantic["groups"]])
        groups = []
        for g, duration in zip(semantic["groups"], group_durations):
            section_durations = _allocate(duration, [s["weight"] for s in g["sections"]])
            sections = tuple(PlannedSection(new_id("planned_section"), s["title"], s["role"], s["purpose"],
                                             seconds, max(1, round(seconds * WORDS_PER_SECOND)),
                                             max(1, round(seconds / spec.preferred_scene_seconds)))
                             for s, seconds in zip(g["sections"], section_durations))
            groups.append(VideoPlanGroup(new_id("video_plan_group"), g["kind"], g["title"], g["purpose"], duration, sections))
        revision = VideoPlanRevision(new_id("video_plan_revision"), project_id, language, fmt,
                                     target_duration_seconds, topic, semantic["working_title"],
                                     semantic["film_brief"], semantic["visual_style"], tuple(groups), parent_revision_id)
        self.plans.save_revision(revision)
        self.plans.select(revision, expected_selection_id=self.plans.selected_id())
        return revision
