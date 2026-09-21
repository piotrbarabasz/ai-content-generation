"""Bounded regeneration of explicit outputs using existing stage services."""

import asyncio
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class StageState:
    state: str  # fresh, missing, stale, review, unavailable
    detail: str = ""

    def __post_init__(self):
        if self.state not in ("fresh", "missing", "stale", "review", "unavailable"):
            raise ValueError("Unknown regeneration state.")


@dataclass(frozen=True)
class RegenerationStep:
    key: str
    dependencies: tuple[str, ...]
    inspect: Callable
    execute: Callable


@dataclass(frozen=True)
class StepOutcome:
    key: str
    status: str
    detail: str = ""


class RegenerationService:
    """Re-evaluate retained outputs on every run, including after restart.

    The builder returns a finite graph for the current project snapshot. Stage
    adapters own input fingerprints, durable attempts and conditional selection.
    A selected target includes only its prerequisite closure, never dependants.
    """

    def __init__(self, build, revision, cancel_active=lambda: None):
        self.build, self.revision, self.cancel_active = build, revision, cancel_active
        self.busy = self.canceled = False
        self.progress = "Ready"

    def steps(self):
        steps = tuple(self.build())
        keys = {step.key for step in steps}
        if len(keys) != len(steps):
            raise ValueError("Duplicate regeneration output.")
        for step in steps:
            if not set(step.dependencies) <= keys:
                raise ValueError("Regeneration dependency is unavailable.")
        return steps

    async def run(self, target=None):
        if self.busy:
            raise ValueError("Regeneration is already running.")
        self.busy, self.canceled = True, False
        outcomes = []
        try:
            revision = self.revision()
            steps = {step.key: step for step in self.steps()}
            ordered, visiting, seen = [], set(), set()

            def visit(key):
                if key not in steps:
                    raise ValueError("Unknown regeneration output.")
                if key in visiting:
                    raise ValueError("Regeneration dependency cycle.")
                if key in seen:
                    return
                visiting.add(key)
                for dependency in steps[key].dependencies:
                    visit(dependency)
                visiting.remove(key)
                seen.add(key)
                ordered.append(steps[key])

            for key in (steps if target is None else (target,)):
                visit(key)
            successful = set()
            for step in ordered:
                await asyncio.sleep(0)  # Return control to Qt between stages.
                if self.canceled:
                    outcomes.append(StepOutcome(step.key, "canceled"))
                    break
                if self.revision() != revision:
                    outcomes.append(StepOutcome(step.key, "obsolete", "Project changed; rebuild against current revisions."))
                    break
                if not set(step.dependencies) <= successful:
                    outcomes.append(StepOutcome(step.key, "blocked", "A prerequisite requires attention."))
                    continue
                self.progress = step.key
                try:
                    state = step.inspect()
                    if state.state == "fresh":
                        outcome = StepOutcome(step.key, "reused", state.detail)
                    elif state.state in ("review", "unavailable"):
                        outcome = StepOutcome(step.key, state.state, state.detail)
                    else:
                        await step.execute()
                        if self.canceled:
                            outcome = StepOutcome(step.key, "canceled")
                        elif self.revision() != revision:
                            outcome = StepOutcome(step.key, "obsolete", "Result retained; project changed.")
                        else:
                            after = step.inspect()
                            outcome = StepOutcome(step.key, "rebuilt" if after.state == "fresh" else after.state,
                                                  after.detail)
                    outcomes.append(outcome)
                    if outcome.status in ("rebuilt", "reused"):
                        successful.add(step.key)
                    if outcome.status in ("canceled", "obsolete"):
                        break
                except Exception as exc:
                    outcomes.append(StepOutcome(step.key, "canceled" if self.canceled else "failed", str(exc)))
                    if self.canceled:
                        break
            return tuple(outcomes)
        finally:
            self.busy = False
            self.progress = "Finished"

    def cancel(self):
        self.canceled = True
        self.cancel_active()
