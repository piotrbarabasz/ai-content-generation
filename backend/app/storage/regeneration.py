"""Composition over retained project state; all writes use existing services."""

import json

from app.application.invalidation import evaluate_freshness
from app.application.regeneration import RegenerationService, RegenerationStep, StageState
from app.application.scene_planning import ScenePlanningService
from app.application.section_audio import OPERATION, VERSION
from app.domain.dependencies import ArtifactDependency, DependencyDeclaration, RequestFingerprint
from app.domain.publication import PublicationSnapshot
from app.domain.render_result import render_request
from app.jobs.coordinator import JobCoordinator
from app.storage.section_tempo import SectionTempoArtifacts
from app.tts.scene_sources import sentence_sources


class ProjectRegeneration:
    """Explicitly configured project stages. Optional services stay optional.

    Audio selection is supplied per section. Plans are suggested but never
    automatically accepted. Existing image choices and manual prompts require
    review on incompatible inputs. D023 snapshots are never silently rebuilt.
    """

    def __init__(self, session, index, store, *, audio=None, audio_selection=None,
                 run_audio=None, scenes=None, timeline=None, preview=None, render=None,
                 image_settings=None):
        self.session, self.index, self.store = session, index, store
        self.audio, self.audio_selection, self.run_audio = audio, audio_selection, run_audio
        self.scenes, self.timeline, self.preview, self.render = scenes, timeline, preview, render
        self.image_settings = image_settings or (lambda scene_id: {"width": 512, "height": 512})
        self.jobs = index.jobs
        self.coordinator = JobCoordinator(self.jobs)
        self.attempt = None
        self.service = RegenerationService(self.build, lambda: session.active_script.id, self.cancel)
        self.audio_media = SectionTempoArtifacts(index, store)

    def cancel(self):
        if self.attempt is not None:
            self.coordinator.cancel(self.attempt.id)
        if self.preview is not None:
            self.preview.cancel()

    def _queue_ready(self):
        if self.jobs.paused:
            raise ValueError("Job queue is paused.")
        if any(a.status in ("queued", "running") for job in self.jobs.jobs() for a in self.jobs.attempts(job.id)):
            raise ValueError("Resolve pending jobs before regeneration.")

    def _resume(self, key, request):
        """Retry only the latest exact request, preserving D010's chunk workspace."""
        if self.jobs.paused:
            raise ValueError("Job queue is paused.")
        jobs = self.jobs.jobs()
        latest = next((job for job in reversed(jobs) if job.output_key == key), None)
        if latest is None or latest.request != request:
            self._queue_ready()
            return None
        attempts = self.jobs.attempts(latest.id)
        last = attempts[-1]
        others = [a for job in jobs for a in self.jobs.attempts(job.id)
                  if a.status in ("queued", "running") and a.id != last.id]
        if others or last.status == "running":
            raise ValueError("Another job is active; wait for cleanup or reopen after a crash.")
        if last.status == "queued":
            return last
        if last.status in ("failed", "canceled", "interrupted"):
            return self.coordinator.retry(latest.id)
        return None

    async def _run_job(self, attempt, execute, *, worker=False):
        self.attempt = attempt
        try:
            if worker:
                await execute(attempt)
            else:
                claim = self.coordinator.claim_next("desktop-regeneration")
                if claim is None or claim.id != attempt.id:
                    raise ValueError("Regeneration could not claim its own job.")
                await execute(claim)
            actual = self.jobs.get_attempt(attempt.id)
            if actual.status != "completed":
                raise ValueError(f"{actual.status}: {actual.error}")
        except BaseException as exc:
            actual = self.jobs.get_attempt(attempt.id)
            if actual.status == "running":
                if actual.cancel_requested:
                    self.coordinator.acknowledge_cancel(actual)
                else:
                    self.coordinator.fail(actual, str(exc)[:1024] or type(exc).__name__)
            raise
        finally:
            actual = self.jobs.get_attempt(attempt.id)
            if actual.status == "queued":
                self.coordinator.cancel(actual.id)
            self.attempt = None

    def _bound(self, request, sections):
        return PublicationSnapshot(self.session.project.id, "regeneration-check", tuple(sections)).bind(request)

    def _fresh(self, key, request):
        selected = self.index.selected().get(key)
        manifest = next((m for m in self.store.list_artifacts() if m.artifact_id == selected), None)
        if manifest is None:
            return False
        declaration = DependencyDeclaration.from_payload(manifest.metadata["desktop_dependencies"])
        # D005 evaluates the current bound source inputs. Artifact-bearing
        # stages validate their actual selections separately before comparison.
        if any(edge.artifact_id is not None for edge in request.inputs):
            matches = declaration.request == request
        else:
            record = ArtifactDependency(manifest.artifact_id, manifest.checksum, declaration)
            result = evaluate_freshness(requests={key: request},
                sources={edge.key: edge.fingerprint for edge in request.inputs},
                selected={key: selected}, artifacts={selected: record})[key]
            matches = result.state == "fresh"
        if not matches:
            return False
        with self.store.open_artifact_id(selected) as source:
            from hashlib import file_digest
            return file_digest(source, "sha256").hexdigest() == manifest.checksum

    def _audio_request(self, section):
        selection = self.audio_selection(section)
        prepared = self.audio.voices.prepare(selection, 120)
        request = RequestFingerprint.create(OPERATION, VERSION, settings=prepared,
                                            effective_identity=prepared["effective_identity"])
        return selection, self._bound(request, (section,))

    def _audio_step(self, section):
        key = f"section:{section.section_id}:audio:raw"

        def inspect():
            if self.audio is None or self.audio_selection is None or self.run_audio is None:
                # Unconfigured generation may still consume a verified retained recording.
                retained = self.audio_media.selected(section, "original")
                return StageState("fresh" if retained else "unavailable", "Configure section audio to rebuild it.")
            _, request = self._audio_request(section)
            return StageState("fresh" if self._fresh(key, request) else "stale")

        async def execute():
            selection, request = self._audio_request(section)
            attempt = self._resume(key, request)
            if attempt is None:
                attempt = self.audio.enqueue(section, selection)
            await self._run_job(attempt, self.run_audio, worker=True)

        return RegenerationStep(key, (), inspect, execute)

    def _acceptance(self, section):
        return next((a for a in reversed(self.scenes.plans.acceptances(section.section_id))
                     if a.plan.revision_id == section.id), None)

    def _plan_step(self, section):
        key = f"section:{section.section_id}:scenes"

        def inspect():
            if self._acceptance(section) is not None:
                return StageState("fresh")
            proposals = [p for p in self.scenes.plans.proposals(section.section_id) if p.revision_id == section.id]
            return StageState("review", "Accept the retained scene proposal before rebuilding visuals.") if proposals else StageState("missing")

        async def execute():
            ScenePlanningService(self.scenes.plans, sentence_sources).suggest(section)

        return RegenerationStep(key, (), inspect, execute)

    def _timing(self, section, acceptance):
        audio = self.audio_media.selected(section, "original")
        if audio is None:
            return None
        for manifest in reversed(sorted(self.store.list_artifacts(), key=lambda m: (m.created_at, m.artifact_id))):
            if manifest.artifact_type != "desktop_scene_timing":
                continue
            timing = self.scenes.plans.timing(manifest.metadata["value_id"])
            if timing.acceptance_id == acceptance.id and (timing.audio_artifact_id, timing.audio_checksum) == (audio.artifact_id, audio.checksum):
                return timing
        return None

    def _timing_step(self, section, acceptance):
        key = f"section:{section.section_id}:timing"

        async def execute():
            audio = self.audio_media.selected(section, "original")
            ScenePlanningService(self.scenes.plans, sentence_sources).retime(acceptance.id, section, audio)

        return RegenerationStep(key, (f"section:{section.section_id}:audio:raw", f"section:{section.section_id}:scenes"),
            lambda: StageState("fresh" if self._timing(section, acceptance) else "missing"), execute)

    def _prompt_step(self, section, acceptance, scene):
        prompts = self.scenes.prompts
        key = f"scene:{scene.id}:visual_prompt"

        def contexts():
            selected = prompts.selected(scene.id)
            if self.scenes.context_resolver is not None:
                return tuple(self.scenes.context_resolver(scene.id))
            if selected is not None:
                return selected.inputs.brief_revision_id, selected.inputs.style_revision_id
            raise ValueError("Configure explicit film brief and visual style revisions.")

        def inspect():
            try:
                ids = contexts()
            except ValueError as exc:
                return StageState("unavailable", str(exc))
            freshness = prompts.freshness(acceptance.id, scene.id, *ids,
                                          generation_identity=json.loads(prompts.identity_json) if prompts.provider else None)
            if freshness.review_required:
                return StageState("review", "Retained manual prompt requires review.")
            if freshness.state == "fresh":
                return StageState("fresh")
            if prompts.provider is None:
                return StageState("unavailable", "Configure a prompt provider or create a manual prompt.")
            return StageState(str(freshness.state))

        async def execute():
            previous = prompts.prompts.selected(scene.id)
            revision = prompts.generate(acceptance.id, scene.id, *contexts())
            prompts.select(revision.id, expected_selection_id=previous.id if previous else None)

        return RegenerationStep(key, (f"section:{section.section_id}:scenes",), inspect, execute)

    def _image_step(self, scene):
        generation, images = self.scenes.generation, self.scenes.images
        key = f"scene:{scene.id}:image"

        def inspect():
            choice = images.selected(scene.id)
            prompt = self.scenes.prompts.selected(scene.id)
            if choice is not None:
                image = images.image(choice.artifact_id)
                if image.provenance == "imported":
                    return StageState("fresh", "Preserved imported image.")
                manifest = next(m for m in self.store.list_artifacts() if m.artifact_id == image.artifact_id)
                if prompt and manifest.metadata["image_generation"]["prompt_revision_id"] == prompt.id:
                    if generation.provider is not None:
                        _, desired = generation.prepare_request(prompt.id, **self.image_settings(scene.id))
                        retained = DependencyDeclaration.from_payload(manifest.metadata["desktop_dependencies"]).request
                        if (retained.settings_json != desired.settings_json
                                or retained.effective_identity_json != desired.effective_identity_json):
                            return StageState("review", "Selected image settings/provider differ; review the retained variant.")
                    return StageState("fresh", "Preserved selected image variant.")
                return StageState("review", "Selected image uses an older prompt; choose its replacement explicitly.")
            if generation.provider is None:
                return StageState("unavailable", "Configure image generation or import an image.")
            return StageState("missing")

        async def execute():
            previous = images.selected(scene.id)
            prompt = self.scenes.prompts.selected(scene.id)
            settings = dict(self.image_settings(scene.id))
            prepared, request = generation.prepare_request(prompt.id, **settings)
            section = self.session.repository.get_section(prepared["section_revision_id"])
            attempt = self._resume(f"scene:{scene.id}:image_candidate", self._bound(request, (section,)))
            artifact_id = None
            if attempt is None:
                submission = generation.enqueue(prompt.id, **settings)
                artifact_id, attempt = submission.cached_artifact_id, submission.attempt
            if artifact_id is None:
                async def run(claim):
                    generation.run(claim)
                await self._run_job(attempt, run)
                result = generation.publication.repository.result(self.jobs.get_attempt(attempt.id))
                if not result.selected_at_publication:
                    raise ValueError("Image result is obsolete and remains historical.")
                artifact_id = result.artifact_id
            generation.select(artifact_id, expected_selection_id=previous.id if previous else None)

        chosen = images.selected(scene.id)
        imported = chosen is not None and images.image(chosen.artifact_id).provenance == "imported"
        return RegenerationStep(key, () if imported else (f"scene:{scene.id}:visual_prompt",), inspect, execute)

    def build(self):
        steps = []
        for section in self.session.active_script.sections:
            steps.append(self._audio_step(section))
            if self.scenes is None:
                continue
            steps.append(self._plan_step(section))
            acceptance = self._acceptance(section)
            if acceptance is not None:
                steps.append(self._timing_step(section, acceptance))
                for scene in acceptance.plan.scenes:
                    steps.extend((self._prompt_step(section, acceptance, scene), self._image_step(scene)))
        if self.timeline is not None:
            def timeline_state():
                edit = self.timeline.current()
                if edit is None:
                    return StageState("review", "Create an explicit Timeline Lite selection.")
                if self.preview is not None and not self.preview.is_current(edit.timeline):
                    return StageState("review", "Timeline pins older media; review and replace clips explicitly.")
                return StageState("fresh")

            async def no_edit():
                raise ValueError("Timeline changes require explicit editing.")

            # A pinned timeline consumes its own media, not every project output.
            steps.append(RegenerationStep("project:timeline", (), timeline_state, no_edit))
            if self.preview is not None:
                def proxy_state():
                    from app.application.preview import proxy_cache_key
                    edit = self.timeline.current()
                    key = proxy_cache_key(edit.timeline, self.preview.renderer.identity())
                    return StageState("fresh" if self.preview.media.cached_proxy(key, edit.timeline) else "missing")

                async def proxy():
                    result = await self.preview.proxy(self.timeline.current().timeline)
                    if not result.current:
                        raise ValueError("Proxy is obsolete.")

                steps.append(RegenerationStep("project:proxy", ("project:timeline",), proxy_state, proxy))
            if self.render is not None:
                def render_state():
                    timeline = self.timeline.current().timeline
                    self.render.artifacts.current(timeline)
                    request = self._bound(render_request(timeline, self.render.renderer.identity()),
                        {c.media.section_id: self.session.repository.get_section(c.media.section_revision_id)
                         for c in timeline.clips}.values())
                    return StageState("fresh" if self._fresh("project:video_render", request) else "missing")

                async def render():
                    timeline = self.timeline.current().timeline
                    request = self._bound(render_request(timeline, self.render.renderer.identity()),
                        {c.media.section_id: self.session.repository.get_section(c.media.section_revision_id)
                         for c in timeline.clips}.values())
                    attempt = self._resume("project:video_render", request)
                    if attempt is None:
                        attempt = self.render.enqueue(timeline)
                    await self._run_job(attempt, self.render.run)

                steps.append(RegenerationStep("project:video_render", ("project:timeline",), render_state, render))
        return tuple(steps)
