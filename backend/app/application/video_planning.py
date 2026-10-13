"""Generate and retain immutable Social / Standard video plans."""

from app.domain.base import new_id
from app.domain.video_plan import (VIDEO_FORMATS, WORDS_PER_SECOND, PlannedSection,
                                   VideoFormat, VideoPlanGroup, VideoPlanRevision, NarrativeMetadata)
from app.domain.video_plan_schema import (MissingStandardPlanGroupsError,
                                         validate_video_plan_output, video_plan_schema)


def _allocate(total, weights):
    """Largest remainder allocation; ties stay in original editorial order."""
    weight_sum = sum(weights)
    floors = [(total * weight) // weight_sum for weight in weights]
    remainder = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % weight_sum), i))
    for index in order[:remainder]:
        floors[index] += 1
    if any(value < 1 for value in floors):
        raise ValueError("Target duration cannot fit the proposed sections. Reduce section counts or rebalance chapter/section weights.")
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
            "For new plans use cold_open, introduction, five to seven chapters, conclusion in that exact order. "
            "Historical kind values are cold_open, opening, introduction, chapter, conclusion; opening is legacy only. "
            "Include exactly one introduction and exactly one conclusion "
            "as separate top-level groups, not sections inside a chapter. Create 5–7 chapter groups "
            "within 8–10 groups overall; chapters may contain multiple planned sections. "
            "Do not use synonyms such as intro, outro, wrap_up, closing, or final_thoughts. "
            "Shape this as a target long-form 9 to 11 minute explanatory structure. "
            "Do not calculate duration budgets; the application allocates them. "
            "Plan the central question, narrative promise, hook curiosity, introduction context and assumptions, "
            "each chapter's new contribution and dependencies on earlier chapters, conclusion resolution and final takeaway "
            "in narrative metadata. Metadata is editorial intent, never invented evidence or citations. "
            "Hook guidance: 15-25s; introduction: 35-60s; conclusion: 40-70s; chapters use the remainder. "
            "Avoid greetings, channel branding and subscription requests. Return content matching the supplied strict schema."
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
        prompt = _planner_prompt(fmt, target_duration_seconds, topic) + f"\nWrite the plan in language: {language}."
        schema = video_plan_schema(fmt, narrative_policy=True)
        semantic = self.provider.generate_structured(prompt, schema)
        try:
            semantic = validate_video_plan_output(semantic, fmt, narrative_policy=True)
        except MissingStandardPlanGroupsError:
            if fmt is not VideoFormat.STANDARD:
                raise
            semantic = self.provider.generate_structured(
                prompt + "\nThe previous response lacked a top-level introduction or conclusion. "
                "Return a complete replacement plan matching the strict schema, with separate "
                "top-level groups whose kind values are exactly introduction and conclusion. "
                "Return the entire plan, not a patch or partial response.", schema)
            semantic = validate_video_plan_output(semantic, fmt, narrative_policy=True)
        if fmt is VideoFormat.STANDARD:
            # Fixed recommended midpoints reserve room for context and resolution.
            chapter_durations = _allocate(target_duration_seconds - 120,
                [g["weight"] for g in semantic["groups"] if g["kind"] == "chapter"])
            group_durations = [20, 45, *chapter_durations, 55]
        else:
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
                                     semantic["film_brief"], semantic["visual_style"], tuple(groups), parent_revision_id,
                                     NarrativeMetadata.from_payload(semantic["narrative"]) if fmt is VideoFormat.STANDARD else None)
        self.plans.save_revision(revision)
        self.plans.select(revision, expected_selection_id=self.plans.selected_id())
        return revision
