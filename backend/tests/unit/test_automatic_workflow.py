"""Resumable orchestration decisions without external providers or Qt."""

import asyncio
from types import SimpleNamespace

import pytest

from app.application.automatic_workflow import (
    AutomaticWorkflow, AutomaticWorkflowBlocked, AutomaticWorkflowCanceled,
    AutomaticWorkflowConfig,
)


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
