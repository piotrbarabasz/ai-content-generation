"""D013 whole-sentence grouping and immutable semantics versus measured time."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
import subprocess
import sys

import pytest

from app.application.scene_planning import ScenePlanningService
from app.domain.narrative_segment import SectionRevision
from app.domain.scene_plan import AcceptedScenePlan, ScenePlan, SceneTimingSet
from app.domain.section_audio import SectionAudio
from app.domain.speech_boundary import SpeechBoundaryMap, SpeechChunkBoundary
from app.tts.chunking import sentence_chunks
from app.tts.scene_sources import sentence_sources


class Plans:
    def __init__(self, section):
        self.section = section
        self.proposals, self.accepted, self.timings = {}, {}, {}

    def current(self, section):
        if section != self.section: raise ValueError("Not the current section.")

    def verify_audio(self, section, audio):
        self.current(section)

    def save_plan(self, section, plan): self.proposals[plan.id] = plan
    def plan(self, plan_id): return self.proposals[plan_id]
    def save_acceptance(self, accepted): self.accepted[accepted.id] = accepted
    def acceptance(self, acceptance_id): return self.accepted[acceptance_id]
    def save_timing(self, section, timing): self.timings[timing.id] = timing


def make(text="First topic. Second detail. Third detail. Fourth detail."):
    section = SectionRevision.create(project_id="project", title="Section", text=text, role="body")
    plans = Plans(section)
    return section, plans, ScenePlanningService(plans, sentence_sources)


def audio(section, durations, *, artifact_id="raw", max_words=120):
    chunks = sentence_chunks(section.text, max_words=max_words)
    by_sentence = dict(zip((s.sentence_id for s in sentence_sources(section.text)), durations, strict=True))
    totals = {key: sum(c.source_span.sentence_id == key for c in chunks) for key in by_sentence}
    counters, boundaries, cursor = {}, [], 0
    for chunk in chunks:
        key = chunk.source_span.sentence_id
        frames = round(by_sentence[key] * 8000)
        index = counters.get(key, 0)
        size = (index + 1) * frames // totals[key] - index * frames // totals[key]
        counters[key] = index + 1
        boundaries.append(SpeechChunkBoundary(chunk.id, chunk.source_span, cursor, cursor + size))
        cursor += size
    checksum = sha256(artifact_id.encode()).hexdigest()
    boundary = SpeechBoundaryMap(sha256(section.text.encode()).hexdigest(), len(section.text), checksum, 8000, cursor, tuple(boundaries))
    return SectionAudio(artifact_id, section.section_id, section.id, checksum, 8000, cursor, boundary)


@pytest.mark.parametrize("durations,expected", [([4, 4, 4, 4], [2, 2]), ([3, 3, 3, 3], [4]),
                                               ([6.5, 6.5, 6.5, 6.5], [2, 2]),
                                               ([1, 1, 1, 1], [4]), ([25, 3, 3, 3], [1, 3]),
                                               ([10, 10, 10, 10], [1, 1, 1, 1])])
def test_measured_preference_never_cuts_sentences_and_avoids_tiny_tail(durations, expected):
    section, plans, service = make()
    measured = audio(section, durations)
    plan = service.suggest(section, measured)
    assert [len(s.sentence_ids) for s in plan.scenes] == expected
    assert "".join(section.text[s.source_start:s.source_end] for s in plan.scenes) == section.text
    assert plans.accepted == {} and plan.planning_audio_id == measured.artifact_id


def test_semantic_paragraph_and_editorial_breaks_outrank_duration():
    section, _, service = make("First topic. Second detail.\n\nThird topic. Fourth detail.")
    measured = audio(section, [2, 2, 2, 2])
    paragraph = service.suggest(section, measured)
    assert [len(s.sentence_ids) for s in paragraph.scenes] == [2, 2]
    breaks = (sentence_sources(section.text)[1].sentence_id,)
    explicit = service.suggest(section, measured, semantic_breaks=breaks)
    assert [len(s.sentence_ids) for s in explicit.scenes] == [1, 1, 2]
    before_tts = service.suggest(section, semantic_breaks=breaks)
    assert before_tts.planning_audio_id is None
    assert [s.sentence_ids for s in before_tts.scenes] == [s.sentence_ids for s in explicit.scenes]


def test_very_long_sentence_keeps_one_visual_scene_across_technical_subchunks():
    section, _, service = make("One two three four five six seven eight nine ten eleven twelve.")
    measured = audio(section, [35], max_words=2)
    assert len(measured.speech_boundary_map.chunks) == 6
    plan = service.suggest(section, measured)
    accepted = service.accept(plan.id, reviewer_id="editor")
    timed = service.retime(accepted.id, section, measured)
    assert len(plan.scenes) == len(timed.scenes) == 1 and timed.scenes[0].end_frame == 35 * 8000


def test_voice_and_tempo_retime_accepted_plan_without_changing_visuals_or_replanning():
    section, plans, service = make()
    original = audio(section, [4, 4, 4, 4])
    plan = service.suggest(section, original)
    accepted = service.accept(plan.id, reviewer_id="editor")
    visuals = {scene.id: "retained-image-" + str(index) for index, scene in enumerate(plan.scenes)}
    initial = service.retime(accepted.id, section, original)
    changed_voice = audio(section, [10, 11, 12, 13], artifact_id="voice-two", max_words=1)
    updated = service.retime(accepted.id, section, changed_voice)
    boundary = changed_voice.speech_boundary_map.retime(checksum="c" * 64, sample_rate=8000, frame_count=300001)
    processed = replace(changed_voice, artifact_id="processed", checksum="c" * 64, frame_count=300001, speech_boundary_map=boundary)
    tempo = service.retime(accepted.id, section, processed)
    assert len(plans.proposals) == len(plans.accepted) == 1 and len(plans.timings) == 3
    assert plans.acceptance(accepted.id) == accepted and accepted.plan == plan
    assert [s.scene_id for s in updated.scenes] == [s.scene_id for s in tempo.scenes] == list(visuals)
    assert initial.scenes[0].end_frame == 8 * 8000
    assert updated.scenes[0].end_frame == 21 * 8000 and updated.scenes[-1].end_frame == 46 * 8000
    assert tempo.scenes[-1].end_frame == 300001 and tempo.quality == "approximate_internal_positions"
    assert tuple(s.visual_description for s in plan.scenes) == tuple(s.visual_description for s in accepted.plan.scenes)
    new_proposal = service.suggest(section, changed_voice)
    assert len(new_proposal.scenes) == 4 and plans.acceptance(accepted.id).plan == plan


def test_unaccepted_plan_cannot_be_used_as_acceptance():
    section, _, service = make()
    plan = service.suggest(section)
    with pytest.raises(KeyError): service.retime(plan.id, section, audio(section, [4] * 4))
    with pytest.raises(ValueError): service.accept(plan.id, reviewer_id=" ")


@pytest.mark.parametrize("mutation", ["no_map", "revision", "section", "checksum", "sentence_identity"])
def test_rejects_missing_or_incompatible_audio_before_creating_any_plan(mutation):
    section, plans, service = make()
    measured = audio(section, [4] * 4)
    if mutation == "no_map": measured = replace(measured, speech_boundary_map=None)
    if mutation == "revision": measured = replace(measured, revision_id="old")
    if mutation == "section": measured = replace(measured, section_id="other")
    if mutation == "checksum": measured = replace(measured, checksum="0" * 64)
    if mutation == "sentence_identity":
        boundary = measured.speech_boundary_map
        first = boundary.chunks[0]
        boundary = replace(boundary, chunks=(replace(first, source=replace(first.source, sentence_id="foreign")),) + boundary.chunks[1:])
        measured = replace(measured, speech_boundary_map=boundary)
    with pytest.raises(ValueError): service.suggest(section, measured)
    assert plans.proposals == {}


@pytest.mark.parametrize("breaks", ["sentence", ("unknown",), ("",)])
def test_invalid_semantic_breaks_are_rejected(breaks):
    section, plans, service = make()
    with pytest.raises(ValueError): service.suggest(section, semantic_breaks=breaks)
    assert plans.proposals == {}


def test_first_sentence_break_and_partial_source_reader_are_rejected():
    section, plans, service = make()
    with pytest.raises(ValueError): service.suggest(section, semantic_breaks=(sentence_sources(section.text)[0].sentence_id,))
    bad = ScenePlanningService(plans, lambda text: sentence_sources(text)[1:])
    with pytest.raises(ValueError): bad.suggest(section)


def test_values_are_immutable_and_roundtrip_without_changing_identities():
    section, _, service = make()
    plan = service.suggest(section, audio(section, [4] * 4))
    accepted = service.accept(plan.id, reviewer_id="editor")
    timing = service.retime(accepted.id, section, audio(section, [4] * 4))
    for value, cls in ((plan, ScenePlan), (accepted, AcceptedScenePlan), (timing, SceneTimingSet)):
        assert cls.from_payload(json.loads(json.dumps(value.to_payload()))) == value
        with pytest.raises(FrozenInstanceError): value.id = "changed"
    with pytest.raises(FrozenInstanceError): plan.scenes[0].visual_description = "changed"


@pytest.mark.parametrize("mutation", ["gap", "overlap", "duplicate", "foreign_project", "missing_tail"])
def test_plan_rejects_incomplete_or_inconsistent_scene_values(mutation):
    section, _, service = make()
    plan = service.suggest(section, audio(section, [4] * 4))
    scenes = list(plan.scenes)
    if mutation == "gap": scenes[1] = replace(scenes[1], source_start=scenes[1].source_start + 1)
    if mutation == "overlap": scenes[1] = replace(scenes[1], source_start=0)
    if mutation == "duplicate": scenes[1] = replace(scenes[1], id=scenes[0].id)
    if mutation == "foreign_project": scenes[1] = replace(scenes[1], project_id="other")
    if mutation == "missing_tail": scenes.pop()
    with pytest.raises(ValueError): replace(plan, scenes=tuple(scenes))


def test_application_import_does_not_load_optional_providers_storage_or_tts():
    script = "import sys; import app.application.scene_planning; assert not any(n.startswith(('app.providers', 'app.storage', 'app.tts', 'PySide6', 'sqlite3')) for n in sys.modules)"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
