"""Dependency closure and truthful regeneration decisions."""

import asyncio

import pytest

from app.application.regeneration import RegenerationService, RegenerationStep, StageState


def test_target_closure_reuses_fresh_inputs_and_blocks_on_manual_review():
    states = {"audio": "fresh", "prompt": "review", "image": "missing", "other": "missing"}
    calls = []
    def step(key, dependencies):
        async def execute():
            calls.append(key)
            states[key] = "fresh"
        return RegenerationStep(key, dependencies, lambda: StageState(states[key]), execute)
    steps = [step("audio", ()), step("prompt", ()), step("image", ("prompt",)), step("other", ())]
    service = RegenerationService(lambda: steps, lambda: "revision")
    result = asyncio.run(service.run("image"))
    assert [(r.key, r.status) for r in result] == [("prompt", "review"), ("image", "blocked")]
    assert not calls
    states["prompt"] = "fresh"
    result = asyncio.run(service.run("image"))
    assert calls == ["image"] and result[-1].status == "rebuilt"


def test_cycle_and_unknown_target_fail_before_execution():
    async def execute():
        raise AssertionError("must not execute")
    service = RegenerationService(lambda: (
        RegenerationStep("a", ("b",), lambda: StageState("missing"), execute),
        RegenerationStep("b", ("a",), lambda: StageState("missing"), execute)), lambda: "revision")
    with pytest.raises(ValueError, match="cycle"):
        asyncio.run(service.run())
    with pytest.raises(ValueError, match="Unknown"):
        asyncio.run(service.run("absent"))
    assert not service.busy
