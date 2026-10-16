"""D077: persisted repairs with real project storage and verified PCM, offline providers."""

import asyncio
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
import wave

import pytest

from app.application.automatic_workflow import AutomaticWorkflowCanceled
from app.application.duration_repair import DurationRepairService
from app.application.planned_script_generation import PlanScriptGenerationService
from app.application.projects import ProjectSession
from app.desktop.audio_services import AudioServices
from app.domain.base import new_id
from app.domain.dependencies import DependencyDeclaration, RequestFingerprint
from app.domain.duration_repair import DurationRepairPolicy, DurationRepairProposal
from app.domain.narrative_segment import SectionRevision
from app.domain.video_plan import PlannedSection, VideoFormat, VideoPlanGroup, VideoPlanRevision
from app.storage.local_store import LocalArtifactStore
from app.storage.plan_script import ProjectPlanScripts
from app.storage.project_repository import ProjectRepository
from app.storage.video_plans import ProjectVideoPlans
from app.tts.assembly import inspect_pcm_wav


class Provider:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.bad = None

    def generation_identity(self):
        return {"provider": "offline", "model": "repair-fixture", "api_key": "secret-never-retain"}

    def generate_structured(self, prompt, schema):
        value = json.loads(prompt)
        self.calls.append(value)
        if self.hook:
            self.hook()
        if self.bad == "timeout":
            raise RuntimeError("fixture timeout")
        slots = value["section_slots"]
        return {"sections": {key: self.text(slot) for key, slot in slots.items()}}

    def text(self, slot):
        slot = {**slot, "target_words": slot["target_word_count"]}
        if self.bad == "padding":
            return " ".join(["padding"] * slot["target_words"])
        if self.bad == "instructions":
            return "Voice-over: " + " ".join(f"instruction{i}" for i in range(slot["target_words"] - 1))
        if self.bad == "duplicate":
            return " ".join(f"repeated{i}" for i in range(slot["target_words"])) + "."
        # Unique fixture tokens let tests distinguish repetition from simple word budgets.
        return " ".join(f"expanded{slot['planned_section_id']}_{i}" for i in range(slot["target_words"])) + "."


class Environment:
    def __init__(self, root, seconds):
        self.root = root
        self.provider = Provider()
        self.session = ProjectSession.create(root, repository_factory=ProjectRepository, name="Duration test")
        self.attach()
        kinds = ["cold_open", "introduction", *(["chapter"] * 6), "conclusion"]
        durations = [20, 40, *([80] * 6), 60]
        groups = []
        for i, (kind, duration) in enumerate(zip(kinds, durations)):
            sections = tuple(PlannedSection(f"p{i}-{j}", f"Section {i}-{j}", kind,
                "Explain causes and consequences", duration // 2, round(duration / 2 * 2.2), 1) for j in range(2))
            groups.append(VideoPlanGroup(f"g{i}", kind, f"Political consequences {i}",
                "Military and British governance consequences", duration, sections))
        self.plan = VideoPlanRevision(new_id("video_plan_revision"), self.session.project.id, "en",
            VideoFormat.STANDARD, 600, "America 1776", "Alternate history", "Documentary", "Historical", tuple(groups))
        self.plans.save_revision(self.plan)
        self.plans.select(self.plan, expected_selection_id=None)
        self.scripts.start_or_resume(self.plan)
        old = self.session.active_script
        sections = tuple(SectionRevision.create_for_plan(project_id=self.plan.project_id,
            planned_section_id=p.id, title=p.title, role=p.role,
            text=" ".join(f"original{p.id}_{i}" for i in range(p.target_word_count)) + ".")
            for g in groups for p in g.sections)
        self.session.save_script(replace(old, id=new_id("script_revision"), parent_revision_id=old.id,
            sections=sections), expected_active_revision_id=old.id)
        self.heads, self.manifests, self.payloads = {}, [], {}
        self.audio = AudioServices.__new__(AudioServices)
        self.audio.index = SimpleNamespace(selected=lambda: self.heads, manifests=lambda: tuple(self.manifests))
        self.audio.store = SimpleNamespace(read_artifact=lambda key: self.payloads[key])
        self.audio.selection = lambda _: {"voice": "offline"}
        self.audio._prepared_identities = {}
        self.choice = SimpleNamespace(voice="offline")
        self.rate = 1320 / seconds
        for section in sections:
            self.publish(section, len(section.text.split()) / self.rate)

    def attach(self):
        store = LocalArtifactStore.for_project(self.session.repository)
        self.plans = ProjectVideoPlans(self.session.repository, store)
        self.artifacts = ProjectPlanScripts(self.session.repository, store, self.plans)
        self.scripts = PlanScriptGenerationService(self.session, self.provider, self.plans, self.artifacts)
        self.service = DurationRepairService(self.scripts)

    def reopen(self):
        self.session.close()
        self.session = ProjectSession.open(self.root, repository_factory=ProjectRepository)
        self.attach()

    def publish(self, section, seconds):
        stream = BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(1000)
            wav.writeframes(b"\0\0" * round(seconds * 1000))
        payload = stream.getvalue()
        pcm, _ = inspect_pcm_wav(payload)
        checksum, artifact_id = sha256(payload).hexdigest(), new_id("audio")
        key = f"section:{section.section_id}:audio:raw"
        dependency = DependencyDeclaration(key,
            RequestFingerprint.create("audio", "v1", settings={"selection": {"voice": "offline"}}))
        manifest = SimpleNamespace(artifact_id=artifact_id, checksum=checksum, storage_key=artifact_id, metadata={
            **dependency.to_metadata(), "section_audio": {"version": 1, "section_id": section.section_id,
                "revision_id": section.id, "checksum": checksum, "duration_seconds": pcm.frame_count / pcm.sample_rate,
                "audio_parameters": pcm.to_payload()}})
        self.payloads[artifact_id] = payload
        self.manifests.append(manifest)
        self.heads[key] = artifact_id

    def capture(self):
        return self.service.measure(self.plan, self.audio, self.choice)

    def enable(self, **limits):
        self.service.records.configure(self.plan.id, DurationRepairPolicy(enabled=True, **limits))

    def proposal(self):
        return self.service.propose(self.plan, self.capture())

    def execute(self, proposal, **kwargs):
        return asyncio.run(self.service.execute(self.plan, proposal, self.capture, **kwargs))

    def synthesize_changed(self, scale=1):
        changed = []
        for section in self.session.active_script.sections:
            try:
                from app.application.plan_duration import selected_narration
                selected_narration(self.audio, section, self.choice, "original")
            except ValueError:
                self.publish(section, len(section.text.split()) / self.rate * scale)
                changed.append(section.section_id)
        return changed


@pytest.fixture
def env(tmp_path):
    value = Environment(tmp_path / "project", 476.64)
    yield value
    value.session.close()


@pytest.mark.parametrize("seconds", [540, 600, 660])
def test_in_range_does_not_propose_or_spend(tmp_path, seconds):
    e = Environment(tmp_path / "p", seconds)
    try:
        assert e.proposal() is None
        assert e.provider.calls == []
    finally:
        e.session.close()


def test_calibrates_measured_frames_and_preserves_plan(env):
    snapshot, original = env.capture(), env.plan.to_payload()
    assert snapshot["actual_seconds"] == pytest.approx(476.64)
    assert snapshot["overall_words_per_minute"] == pytest.approx(1320 * 60 / 476.64)
    proposal = env.proposal()
    assert DurationRepairProposal.from_payload(json.loads(json.dumps(proposal.to_payload()))) == proposal
    assert len({a.group_id for a in proposal.adjustments}) == 3
    assert len(proposal.adjustments) == 6
    assert sum(a.expected_delta_seconds for a in proposal.adjustments) == pytest.approx(123.36)
    assert all(a.group_id in {g.id for g in env.plan.groups if g.kind == "chapter"} for a in proposal.adjustments)
    assert all(a.target_words > a.editorial_words * 1.25 for a in proposal.adjustments)
    assert env.plan.to_payload() == original and env.provider.calls == []


def test_explicit_opt_in_is_required_and_survives_restart(env):
    proposal = env.proposal()
    with pytest.raises(ValueError, match="Enable"):
        env.execute(proposal)
    env.enable()
    campaign = env.service.records.policy(env.plan.id)[1]
    env.reopen()
    assert env.service.records.policy(env.plan.id) == (DurationRepairPolicy(enabled=True), campaign)
    assert env.proposal() == proposal


def test_selective_revisions_audio_reuse_and_exact_remeasurement(env):
    env.enable()
    old, heads, plan = env.session.active_script, dict(env.heads), env.plan.to_payload()
    proposal = env.proposal()
    new = env.execute(proposal)
    assert len(env.provider.calls) == 3
    assert new.parent_revision_id == old.id
    assert [s.section_id for s in new.sections] == [s.section_id for s in old.sections]
    from app.application.narrative_continuity import NarrativeContinuityService
    review = NarrativeContinuityService(env.scripts)
    assert not any(f["issue_type"] == "word_budget" for f in review.deterministic_findings(env.plan))
    for call in env.provider.calls:
        for slot in call["section_slots"].values():
            assert slot["minimum_word_count"] <= slot["target_word_count"] <= slot["maximum_word_count"]
    assert "secret-never-retain" not in json.dumps(env.service.latest(env.plan))
    selected = {a.section_id for a in proposal.adjustments}
    for before, after in zip(old.sections, new.sections):
        if before.section_id in selected:
            assert after.parent_revision_id == before.id
            assert env.service.records.budget_for_revision(env.plan.id, after.id)
        else:
            assert before == after
    with pytest.raises(ValueError):
        env.capture()  # old selected audio cannot satisfy the changed revisions
    assert set(env.synthesize_changed()) == selected
    assert all(env.heads[k] == v for k, v in heads.items() if k.split(":")[1] not in selected)
    measured = env.capture()
    assert 590 <= measured["actual_seconds"] <= 610
    env.service.observe(env.plan, measured)
    assert env.proposal() is None
    assert env.plan.to_payload() == plan
    assert env.session.repository.get_script(old.id) == old
    assert len(env.manifests) == 24
    env.reopen()
    assert env.session.active_script == new
    assert env.proposal() is None and len(env.provider.calls) == 3
    changed = next(s for s in new.sections if s.section_id in selected)
    manual = env.session.edit_section(changed.section_id, text=changed.text + " Manual clarification.")
    edited = manual.section(changed.section_id)
    assert env.service.records.budget_for_revision(env.plan.id, edited.id) is None
    review = NarrativeContinuityService(env.scripts)
    assert any(f["issue_type"] == "word_budget" and f["revision_id"] == edited.id
               for f in review.deterministic_findings(env.plan))


def test_excess_narration_is_consolidated_not_expanded(tmp_path):
    e = Environment(tmp_path / "p", 700)
    try:
        e.enable()
        proposal = e.proposal()
        assert all(a.target_words < a.original_words for a in proposal.adjustments)
        e.execute(proposal)
        assert len(e.synthesize_changed()) == 6
        assert 590 <= e.capture()["actual_seconds"] <= 610
    finally:
        e.session.close()


@pytest.mark.parametrize("bad", ["padding", "instructions", "duplicate"])
def test_bad_quality_is_retained_without_mutating_script_and_is_bounded(env, bad):
    env.enable()
    old = env.session.active_script
    env.provider.bad = bad
    proposal = env.proposal()
    with pytest.raises(ValueError, match="budget exhausted"):
        env.execute(proposal)
    assert env.session.active_script == old
    assert len(env.provider.calls) == 2
    assert len(env.service._history("response", env.plan)) == 2
    env.reopen()
    with pytest.raises(ValueError, match="budget exhausted"):
        env.execute(env.proposal())
    assert len(env.provider.calls) == 2


def test_timeout_reservation_survives_restart_and_does_not_repeat_forever(env):
    env.enable()
    proposal = env.proposal()
    env.provider.bad = "timeout"
    for _ in range(2):
        with pytest.raises(RuntimeError, match="timeout"):
            env.execute(proposal)
        env.reopen()
    with pytest.raises(ValueError, match="budget exhausted"):
        env.execute(env.proposal())
    assert len(env.provider.calls) == 2
    assert len(env.service._history("request", env.plan)) == 2


def test_cancellation_retains_accepted_group_and_resume_spends_only_remaining(env):
    env.enable()
    proposal = env.proposal()
    canceled = [False]
    env.provider.hook = lambda: canceled.__setitem__(0, True)
    with pytest.raises(AutomaticWorkflowCanceled):
        env.execute(proposal, canceled=lambda: canceled[0])
    assert len(env.service._history("output", env.plan)) == 1
    assert len(env.provider.calls) == 1
    env.provider.hook = None
    env.reopen()
    env.execute(env.proposal())
    assert len(env.provider.calls) == 3


def test_commit_intent_recovers_same_revision_without_new_calls(env, monkeypatch):
    env.enable()
    proposal = env.proposal()
    with monkeypatch.context() as patch:
        def failed(*args, **kwargs):
            raise OSError("simulated crash before selection")
        patch.setattr(env.session, "save_script", failed)
        with pytest.raises(OSError, match="simulated crash"):
            env.execute(proposal)
    intent = env.service._history("commit", env.plan)[-1]
    env.reopen()
    revision = env.execute(env.proposal())
    assert revision.id == intent["script"]["id"]
    assert len(env.provider.calls) == 3
    assert env.execute(proposal) == revision


def test_stale_source_or_voice_blocks_before_paid_call(env):
    env.enable()
    proposal = env.proposal()
    env.audio.selection = lambda _: {"voice": "changed"}
    with pytest.raises(ValueError):
        env.execute(proposal)
    assert env.provider.calls == []


def test_manual_change_during_provider_call_retains_response_without_selection(env):
    env.enable()
    proposal = env.proposal()
    # Simulate a queued owner-thread edit when the completed response is inspected.
    original = env.service._validate_texts
    def edit(*args, **kwargs):
        result = original(*args, **kwargs)
        s = env.session.active_script.sections[0]
        env.session.edit_section(s.section_id, text=s.text + " Updated.")
        return result
    env.service._validate_texts = edit
    with pytest.raises(ValueError, match="script changed|snapshot changed"):
        env.execute(proposal)
    assert env.session.active_script.sections[0].text.endswith("Updated.")
    assert env.service._history("response", env.plan)


def test_two_rounds_can_converge(env):
    env.enable()
    env.execute(env.proposal())
    env.synthesize_changed(scale=.8)
    assert env.capture()["actual_seconds"] < 540
    second = env.proposal()
    assert second.round_number == 2
    env.execute(second)
    env.synthesize_changed()
    assert 590 <= env.capture()["actual_seconds"] <= 610
    env.service.observe(env.plan, env.capture())
    assert env.proposal() is None


def test_round_exhaustion_stops_after_two_even_after_reenable_and_restart(env):
    env.enable()
    for _ in range(2):
        env.execute(env.proposal())
        # Small genuine improvement, deliberately insufficient for acceptance.
        changed = env.synthesize_changed()
        for section in env.session.active_script.sections:
            if section.section_id in changed:
                env.publish(section, 33 if _ == 0 else 35)
    env.service.records.configure(env.plan.id, DurationRepairPolicy(enabled=False))
    env.enable()
    env.reopen()
    with pytest.raises(ValueError, match="exhausted"):
        env.proposal()
    assert len(env.provider.calls) == 6


def test_no_improvement_stops_further_paid_repair(env):
    env.enable()
    proposal = env.proposal()
    env.execute(proposal)
    durations = {a.section_id: a.actual_seconds for a in proposal.adjustments}
    for s in env.session.active_script.sections:
        if s.section_id in durations:
            env.publish(s, durations[s.section_id])
    with pytest.raises(ValueError, match="improvement"):
        env.proposal()
    env.reopen()
    with pytest.raises(ValueError, match="previous repair"):
        env.proposal()
    assert len(env.provider.calls) == 3


def test_request_cap_and_lowered_policy_limits_are_enforced(env):
    env.enable(max_requests=1)
    proposal = env.proposal()
    with pytest.raises(ValueError, match="budget exhausted"):
        env.execute(proposal)
    assert len(env.provider.calls) == 1
    env.enable(max_groups=1)
    with pytest.raises(ValueError, match="exceeds"):
        env.execute(proposal)


@pytest.mark.parametrize("values", [{"max_rounds": 3}, {"max_groups": 4}, {"max_requests": 13}, {"enabled": 1}])
def test_policy_contract_rejects_unbounded_or_implicit_authorization(values):
    with pytest.raises(ValueError):
        DurationRepairPolicy(**values)


def test_d078_cloud_measurements_drive_duration_repair_without_images(tmp_path):
    from app.desktop.cloud_audio import CloudAudioServices
    from app.providers.openai_tts import OpenAITTSProvider
    from app.providers.tts_result import TTSSynthesisResult
    from app.application.plan_duration import plan_duration_result, selected_narration
    class SyntheticSpeech(OpenAITTSProvider):
        def synthesize(self, text, voice_config=None):
            seconds = len(text.split()) / 1.5
            stream = BytesIO()
            with wave.open(stream, 'wb') as writer:
                writer.setparams((1,2,24000,0,'NONE','not compressed'))
                writer.writeframes(b'\x01\x00' * round(seconds*24000))
            payload=stream.getvalue()
            return TTSSynthesisResult(payload,24000,round(seconds*24000)/24000,'wav','openai')
    env=Environment(tmp_path/'duration-cloud',476)
    try:
        audio=CloudAudioServices(env.session,provider=SyntheticSpeech(environment={'OPENAI_API_KEY':'offline'}))
        choice=audio.choices(env.session.project.language)[0]
        sections=env.session.active_script.sections
        original_script=env.session.active_script
        audio.approve_operation(audio.estimate(sections,choice),5,max_attempts=1)
        async def generate_all():
            for section in sections:
                assert await audio.generate(section,choice)=='completed'
        asyncio.run(generate_all())
        measurements=[selected_narration(audio,s,choice,'original') for s in sections]
        duration=plan_duration_result(env.plan,measurements,len(sections))
        assert duration.state=='BLOCKED' and duration.actual>660
        snapshot=env.service.measure(env.plan,audio,choice)
        proposal=env.service.propose(env.plan,snapshot)
        assert proposal.adjustments and all(a.expected_delta_seconds<0 for a in proposal.adjustments)
        assert env.session.active_script==original_script and env.provider.calls==[]
        assert not any('image' in m.artifact_type for m in audio.index.manifests())
        assert audio.estimate(sections,choice)['reuse']==18
    finally:
        env.session.close()
