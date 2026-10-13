"""Resumable orchestration decisions without external providers or Qt."""

import asyncio
from types import SimpleNamespace
from fractions import Fraction

import pytest

from app.application.automatic_workflow import (
    AutomaticWorkflow, AutomaticWorkflowBlocked, AutomaticWorkflowCanceled,
    AutomaticWorkflowConfig,
)
from app.application.plan_duration import plan_duration_result
from app.domain.section_audio import SectionAudio
from app.domain.video_plan import VideoFormat


class Driver:
    def __init__(self):
        self.calls = []
        self.sections = (SimpleNamespace(title="Opening"),)
        self.plan = SimpleNamespace(state="accepted", acceptance_id="accept-1")
        self.scene = SimpleNamespace(id="scene-1")
        self.ready = True
        self.fail_voice = False
        self.canceled = False

    def saved_sections(self): return self.sections
    def unsaved_script_draft(self): return False
    def voice_ready(self, *_): return self.ready
    async def generate_voice(self, *_):
        self.calls.append("voice")
        if self.fail_voice:
            raise ValueError("voice unavailable")
        self.ready = True
    def plan_state(self, *_): self.calls.append("plan-check"); return self.plan
    def timing_ready(self, *_): self.calls.append("timing-check"); return True
    def scenes(self, *_): return (self.scene,)
    def prompt_ready(self, *_): return True
    def image_ready(self, *_): return True
    def image_provider_identity(self, *_): return "openai"
    def final_image_ready(self, *_): return True
    def timeline_candidates(self, *_):
        return (SimpleNamespace(accepted=True, section_title="Opening", scene_id="scene-1",
                                audio_variant="original", source="media", reason=None),)
    def timeline_matches(self, *_): return True
    def current_timeline(self): return SimpleNamespace(timeline=SimpleNamespace(clips=(1,), duration=2))
    def cancel_current(self): self.canceled = True


def config():
    return AutomaticWorkflowConfig(SimpleNamespace(provider="chatterbox"), "openai-image", "landscape", "draft")


def test_automatic_workflow_reuses_every_current_artifact_without_provider_calls():
    driver = Driver()
    result = asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert result.sections == 1 and result.scenes == 1 and result.timeline_clips == 1
    assert result.skipped == 7  # voice, plan, timing, prompt, image, final image, timeline
    assert driver.calls == ["plan-check", "timing-check"]


def test_automatic_workflow_stops_after_voice_failure():
    driver = Driver()
    driver.ready = False
    driver.fail_voice = True
    with pytest.raises(AutomaticWorkflowBlocked, match="voice unavailable") as blocked:
        asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert blocked.value.stage == "VOICE"
    assert driver.calls == ["voice"]


def test_cancel_exception_is_not_converted_to_blocked_failure():
    class CancelDriver(Driver):
        async def generate_voice(self, *_):
            raise AutomaticWorkflowCanceled("VOICE")

    driver = CancelDriver()
    driver.ready = False
    with pytest.raises(AutomaticWorkflowCanceled):
        asyncio.run(AutomaticWorkflow(driver).run(config()))


class PlanDriver(Driver):
    def __init__(self, duration=600, fmt=VideoFormat.STANDARD):
        super().__init__()
        self.sections = tuple(SimpleNamespace(id=f"r{i}", section_id=f"s{i}", title=f"Section {i}") for i in range(2))
        self.video_plan = SimpleNamespace(format=fmt, target_duration_seconds=600, groups=(1, 2))
        self.audio = {}
        self.duration = Fraction(duration)
        self.on_voice = lambda: None
        self.images_ready = False

    def selected_video_plan(self): return self.video_plan
    def validate_plan_run(self, *_): pass
    def ensure_plan_visual_context(self, *_): pass
    def ensure_plan_script(self, *_): self.calls.append("script")
    def voice_ready(self, section, *_): return section.id in self.audio
    async def generate_voice(self, section, *_):
        self.calls.append(f"voice:{section.id}")
        seconds = Fraction(self.duration) / 2
        self.audio[section.id] = SectionAudio(section.id, section.section_id, section.id, "checksum",
                                             seconds.denominator, seconds.numerator)
        self.on_voice()
    def measure_plan_duration(self, plan, sections, _config):
        self.calls.append("duration")
        return plan_duration_result(plan, [self.audio[s.id] for s in sections if s.id in self.audio], len(sections))
    def prompt_ready(self, *_): return False
    async def generate_prompt(self, *_):
        self.calls.append("prompt")
        self.prompt_ready = lambda *_: True
    def image_ready(self, *_): return self.images_ready
    async def generate_image(self, *_): self.calls.append("image"); self.images_ready = True
    def timeline_candidates(self, *_):
        self.calls.append("timeline")
        return super().timeline_candidates() * 2


@pytest.mark.parametrize("duration", [540, 600, 660])
def test_standard_narration_completes_and_passes_before_any_scene_or_image_work(duration):
    driver = PlanDriver(duration)
    result = asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert result.sections == 2
    assert driver.calls[:4] == ["script", "voice:r0", "voice:r1", "duration"]
    assert driver.calls.index("duration") < driver.calls.index("plan-check") < driver.calls.index("image")


@pytest.mark.parametrize("duration", [489, 700, Fraction(540 * 48000 - 1, 48000), Fraction(660 * 48000 + 1, 48000)])
def test_standard_out_of_range_blocks_before_all_downstream_work(duration):
    driver = PlanDriver(duration)
    with pytest.raises(AutomaticWorkflowBlocked) as blocked:
        asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert blocked.value.stage == "PLAN_DURATION"
    assert "PLAN_DURATION BLOCKED" in blocked.value.reason
    assert "Run / Resume" in blocked.value.reason
    assert driver.calls == ["script", "voice:r0", "voice:r1", "duration"]
    assert len(driver.audio) == 2


def test_standard_resume_after_corrected_section_reuses_other_audio_and_downstream_artifacts():
    driver = PlanDriver(489)
    with pytest.raises(AutomaticWorkflowBlocked):
        asyncio.run(AutomaticWorkflow(driver).run(config()))
    unchanged = driver.audio["r1"]
    driver.sections = (SimpleNamespace(id="edited", section_id="s0", title="Section 0"), driver.sections[1])
    driver.duration = 711  # replacement 355.5 + retained 244.5 = 600
    driver.calls.clear()
    asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert driver.calls[:3] == ["script", "voice:edited", "duration"]
    assert driver.audio["r1"] is unchanged
    # Measurement uses only current revisions, like the desktop driver.
    driver.calls.clear()
    result = asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert result.skipped == 13
    assert not any(call.startswith("voice:") for call in driver.calls)
    assert "image" not in driver.calls and "prompt" not in driver.calls
    assert "duration" in driver.calls


def test_standard_cancel_during_narration_retains_completed_audio_for_resume():
    driver = PlanDriver()
    workflow = AutomaticWorkflow(driver)
    driver.on_voice = workflow.cancel
    with pytest.raises(AutomaticWorkflowCanceled):
        asyncio.run(workflow.run(config()))
    assert driver.calls == ["script", "voice:r0"]
    retained = driver.audio["r0"]
    driver.on_voice = lambda: None
    driver.calls.clear()
    asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert driver.calls[:3] == ["script", "voice:r1", "duration"]
    assert driver.audio["r0"] is retained


def test_social_plan_keeps_section_by_section_processing_without_duration_gate():
    driver = PlanDriver(45, VideoFormat.SOCIAL)
    asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert "duration" not in driver.calls
    assert driver.calls.index("image") < driver.calls.index("voice:r1")


def test_standard_pending_measurement_does_not_start_downstream_work():
    driver = PlanDriver()
    driver.measure_plan_duration = lambda plan, sections, _: plan_duration_result(plan, [], len(sections))
    with pytest.raises(AutomaticWorkflowBlocked) as blocked:
        asyncio.run(AutomaticWorkflow(driver).run(config()))
    assert blocked.value.stage == "PLAN_DURATION"
    assert "PLAN_DURATION PENDING" in blocked.value.reason
    assert "plan-check" not in driver.calls and "image" not in driver.calls
