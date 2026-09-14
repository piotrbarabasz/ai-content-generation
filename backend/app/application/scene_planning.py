"""Explicit proposal/accept/retime commands; no providers, worker or UI imports."""

from hashlib import sha256
import re
from typing import Protocol

from app.domain.base import new_id
from app.domain.render_scene import ProjectRenderScene
from app.domain.scene_plan import AcceptedScenePlan, ScenePlan, SceneTiming, SceneTimingSet


class ScenePlansPort(Protocol):
    def current(self, section): ...
    def verify_audio(self, section, audio): ...
    def save_plan(self, section, plan): ...
    def plan(self, plan_id): ...
    def save_acceptance(self, acceptance): ...
    def acceptance(self, acceptance_id): ...
    def save_timing(self, section, timing): ...


def _sources(section, source_reader):
    sources = tuple(source_reader(section.text))
    cursor, seen = 0, set()
    for source in sources:
        if (source.start != cursor or source.start != source.sentence_start or source.end != source.sentence_end
                or source.end <= source.start or source.sentence_id in seen):
            raise ValueError("Planning requires complete ordered sentence source spans.")
        cursor = source.end
        seen.add(source.sentence_id)
    if not sources or cursor != len(section.text):
        raise ValueError("Sentence sources must cover the full section.")
    return sources


def _measured(section, audio, sources):
    if (audio.section_id, audio.revision_id) != (section.section_id, section.id) or audio.speech_boundary_map is None:
        raise ValueError("Scene timing requires mapped audio for this exact section revision.")
    boundary = audio.speech_boundary_map
    boundary.validate_source(section.text, audio.checksum, audio.sample_rate, audio.frame_count)
    if [(b.sentence_id, b.source_start, b.source_end) for b in boundary.blocks] != [
            (s.sentence_id, s.start, s.end) for s in sources]:
        raise ValueError("Measured sentence identities differ from the semantic source.")
    return boundary


def _timed_groups(sources, blocks, sample_rate):
    """Minimize duration deviation within one semantic group; never split a block.

    Dynamic programming avoids leaving a tiny last scene when a better grouping
    exists. The target is soft: a coherent 13 s scene can beat two short scenes.
    """
    count = len(sources)
    costs, next_index = [float("inf")] * count + [0.0], [count] * count
    for start in range(count - 1, -1, -1):
        for end in range(start + 1, count + 1):
            duration = (blocks[end - 1].end_frame - blocks[start].start_frame) / sample_rate
            outside = max(8 - duration, duration - 12, 0)
            penalty = outside ** 2 + 0.01 * (duration - 10) ** 2
            if duration > 12 and penalty >= costs[start]:
                break  # Larger durations cannot beat this bound, even with zero suffix cost.
            cost = penalty + costs[end]
            if cost < costs[start]:
                costs[start], next_index[start] = cost, end
    groups, start = [], 0
    while start < count:
        end = next_index[start]
        groups.append(sources[start:end])
        start = end
    return groups


class ScenePlanningService:
    def __init__(self, plans: ScenePlansPort, sentence_sources):
        self.plans, self.sentence_sources = plans, sentence_sources

    def suggest(self, section, audio=None, *, semantic_breaks=()):
        """Paragraphs/editorial topic breaks outrank duration; before TTS use only meaning."""
        self.plans.current(section)
        sources = _sources(section, self.sentence_sources)
        if isinstance(semantic_breaks, str):
            raise ValueError("Semantic breaks must be sentence IDs, not a text string.")
        breaks = set(semantic_breaks)
        if not breaks <= {s.sentence_id for s in sources[1:]}:
            raise ValueError("Topic breaks must precede a known non-first complete sentence.")
        boundary = None
        if audio is not None:
            self.plans.verify_audio(section, audio)
            boundary = _measured(section, audio, sources)
        semantic_groups, group = [], []
        for index, source in enumerate(sources):
            paragraph_break = index > 0 and re.search(r"(?:\r?\n\s*){2,}", section.text[sources[index - 1].start:source.start])
            if group and (source.sentence_id in breaks or paragraph_break):
                semantic_groups.append(tuple(group))
                group = []
            group.append(source)
        semantic_groups.append(tuple(group))
        groups, offset = [], 0
        for group in semantic_groups:
            groups.extend(_timed_groups(group, boundary.blocks[offset:offset + len(group)], audio.sample_rate)
                          if boundary is not None else [group])
            offset += len(group)
        scenes = tuple(ProjectRenderScene(new_id("render_scene"), section.project_id, section.section_id, section.id,
                                          group[0].start, group[-1].end, tuple(s.sentence_id for s in group),
                                          section.text[group[0].start:group[-1].end].strip()) for group in groups)
        plan = ScenePlan(new_id("scene_plan"), section.project_id, section.section_id, section.id,
                         sha256(section.text.encode()).hexdigest(), len(section.text), scenes,
                         planning_audio_id=audio.artifact_id if audio is not None else None)
        self.plans.save_plan(section, plan)
        return plan

    def accept(self, plan_id, *, reviewer_id):
        """An explicit command retains the exact reviewed plan; suggestion never accepts."""
        accepted = AcceptedScenePlan(new_id("scene_acceptance"), self.plans.plan(plan_id), reviewer_id)
        self.plans.save_acceptance(accepted)
        return accepted

    def retime(self, acceptance_id, section, audio):
        self.plans.current(section)
        accepted = self.plans.acceptance(acceptance_id)
        plan = accepted.plan
        plan.validate_section(section)
        self.plans.verify_audio(section, audio)
        sources = _sources(section, self.sentence_sources)
        boundary = _measured(section, audio, sources)
        by_id = {b.sentence_id: b for b in boundary.blocks}
        cursor, timings = 0, []
        for scene in plan.scenes:
            expected = boundary.blocks[cursor:cursor + len(scene.sentence_ids)]
            if (tuple(b.sentence_id for b in expected) != scene.sentence_ids
                    or scene.source_start != expected[0].source_start or scene.source_end != expected[-1].source_end):
                raise ValueError("Accepted scenes no longer match the complete sentence map.")
            timings.append(SceneTiming(scene.id, by_id[scene.sentence_ids[0]].start_frame,
                                       by_id[scene.sentence_ids[-1]].end_frame))
            cursor += len(scene.sentence_ids)
        result = SceneTimingSet(new_id("scene_timing"), accepted.id, plan.id, audio.artifact_id, audio.checksum,
                                audio.sample_rate, audio.frame_count, boundary.method, boundary.quality, tuple(timings))
        self.plans.save_timing(section, result)
        return result
