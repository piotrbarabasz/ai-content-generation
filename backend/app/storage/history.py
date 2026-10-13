"""History adapter over retained D003/D015/D016/D019/D040 records."""

from hashlib import sha256, file_digest
import json
from contextlib import contextmanager

from app.application.history import HistoryChoice, HistoryService
from app.application.invalidation import evaluate_freshness
from app.application.image_intake import ImageIntakeService
from app.application.visual_prompts import VisualPromptService
from app.jobs.repository import JobRepository
from .local_store import LocalArtifactStore
from app.domain.publication import PublicationSnapshot
from app.domain.scene_plan import ScenePlan, AcceptedScenePlan, SceneTimingSet
from .video_render import ProjectVideoRender, RenderResultIndex
from .scene_images import ProjectSceneImages
from .timeline_edits import ProjectTimelineEdits
from .visual_prompts import ProjectVisualPrompts


class ProjectHistory:
    def __init__(self, repository):
        self.repository = repository
        self.index = RenderResultIndex(repository, JobRepository(repository))
        self.store = LocalArtifactStore(self.index.root, index=self.index)
        self.prompts = ProjectVisualPrompts(repository, self.store)
        self.images = ProjectSceneImages(repository, self.store)
        self.timelines = ProjectTimelineEdits(repository, self.store)

    def choices(self, active):
        rows = []
        def add(kind, identity, scope, title, description, selected, expected,
                manifest=None, source="Not recorded", freshness="Current provider settings are checked by the pipeline."):
            rows.append(HistoryChoice(kind, identity, scope, title, description,
                                      manifest.created_at.isoformat() if manifest else "Not recorded",
                                      source, selected, expected, active.id, freshness))
        @contextmanager
        def checked(kind, identity, scope, manifest=None):
            try: yield
            except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
                reason = str(exc) or type(exc).__name__
                rows.append(HistoryChoice(kind, identity, scope, "UNAVAILABLE · " + identity,
                    reason + "\nInspect the missing source identity and a verified project backup; no source data was reconstructed.",
                    manifest.created_at.isoformat() if manifest else "Not recorded", "Unavailable provenance", False,
                    active.id if kind == "script" else None, active.id, "UNAVAILABLE", reason))
        for number, identity in enumerate(self.repository.script_revision_ids(), 1):
            with checked("script", identity, "project"):
                script = self.repository.get_script(identity)
                add("script", script.id, "project", f"Script snapshot {number} · {len(script.sections)} sections",
                    "\n\n".join(s.title + "\n" + s.text for s in script.sections), script.id == active.id, active.id)
        for section in active.sections:
            for revision in self.repository.section_history(section.section_id):
                add("section", revision.id, section.section_id, revision.title, revision.text,
                    revision.id == section.id, section.id)
        manifests = self.store.list_artifacts()
        by_value = {m.metadata.get("value_id"): m for m in manifests if m.metadata.get("value_id")}
        for kind, value_type, load in (("desktop_scene_plan", ScenePlan, self.images.scenes.plan),
                                      ("desktop_scene_acceptance", AcceptedScenePlan, self.images.scenes.acceptance),
                                      ("desktop_scene_timing", SceneTimingSet, self.images.scenes.timing)):
            for manifest, value, error in self.images.scenes.history_records(kind, value_type):
                with checked(kind, manifest.metadata.get("value_id", manifest.artifact_id), "project", manifest):
                    if error: raise ValueError(error)
                    load(value.id)
        for manifest in manifests:
            if manifest.artifact_type != "visual_prompt_revision": continue
            scene_id = manifest.metadata.get("scene_id")
            with checked("prompt", manifest.metadata.get("value_id", manifest.artifact_id), scene_id, manifest):
                revision = self.prompts.revision(manifest.metadata["value_id"])
                selection = self.prompts.selected(scene_id)
                identity = json.loads(revision.request.effective_identity_json)
                source = str(revision.provenance) + " " + " / ".join(str(identity[k]) for k in ("provider", "model") if k in identity)
                dependency = self.prompts.dependency(revision.id)
                state = evaluate_freshness(requests={revision.output_key: revision.request},
                    sources=self.prompts.freshness_sources(revision.inputs),
                    selected={revision.output_key: dependency.artifact_id},
                    artifacts={dependency.artifact_id: dependency})[revision.output_key]
                section = self.repository.get_section(revision.inputs.section_revision_id)
                add("prompt", revision.id, scene_id, "Visual prompt · " + section.title, revision.prompt,
                    bool(selection and selection.revision_id == revision.id), selection.id if selection else None,
                    by_value.get(revision.id), source.strip(),
                    "Editorial inputs: " + state.state.value + (" · review required" if state.review_required else ""))
        for manifest in manifests:
            if manifest.artifact_type != "scene_image": continue
            scene_id = manifest.metadata.get("scene_id")
            with checked("image", manifest.artifact_id, scene_id, manifest):
                image = self.images.image(manifest.artifact_id)
                image_selection = self.images.selected(scene_id)
                provider = image.upscaler or manifest.metadata.get("provider_identity", {})
                section = self.repository.get_section(image.section_revision_id)
                compatible = any(s.id == section.id for s in active.sections)
                add("image", image.artifact_id, scene_id, image.source_name + " · " + section.title,
                    f"{image.width} × {image.height} · {image.format}",
                    bool(image_selection and image_selection.artifact_id == image.artifact_id),
                    image_selection.id if image_selection else None, manifest,
                    image.provenance + " " + " / ".join(str(provider[k]) for k in ("provider", "model") if k in provider),
                    "Section inputs: " + ("current" if compatible else "historical; restore the section first"))
        with checked("timeline", "timeline-history", "project"):
            timeline_history = self.timelines.history()
        if any(r.kind == "timeline" and r.unavailable_reason for r in rows): timeline_history = ()
        current = timeline_history[-1] if timeline_history else None
        for edit in timeline_history:
            try:
                ProjectVideoRender(self.index, self.store).current(edit.timeline)
                state = "current"
            except (OSError, KeyError) as exc:
                with checked("timeline", edit.id, "project", by_value.get(edit.id)):
                    raise ValueError(str(exc))
                continue
            except ValueError:
                state = "stale; refresh the timeline before rendering"
            add("timeline", edit.id, "project", "Timeline snapshot", f"{len(edit.timeline.clips)} clips · {edit.timeline.duration} seconds",
                bool(current and current.id == edit.id), current.id if current else None,
                by_value.get(edit.id), "Edited", "Timeline input bindings: " + state)
        heads = self.index.selected()
        keys = {row[0] for row in self._result_keys()}
        for key in sorted(keys):
            for result in self.index.history(key):
                with checked("result", result.artifact_id, key):
                    self._result_choice(active, manifests, heads, key, result, add)
        return tuple(rows)

    def _result_choice(self, active, manifests, heads, key, result, add):
        manifest = next(m for m in manifests if m.artifact_id == result.artifact_id)
        metadata = manifest.metadata
        if "section_audio" not in metadata and "render" not in key and "preview" not in key:
            return
        with self.store.open_artifact_id(manifest.artifact_id) as stream:
            if file_digest(stream, "sha256").hexdigest() != manifest.checksum:
                raise ValueError("Retained media checksum differs; history entry is unavailable.")
        if self.store._artifact_path(manifest.storage_key).stat().st_size != manifest.size_bytes:
            raise ValueError("Retained media size differs; history entry is unavailable.")
        measurements = metadata.get("section_audio", {})
        rendered = metadata.get("render", {})
        detail = (f"{measurements['duration_seconds']:.2f} seconds" if "duration_seconds" in measurements else
                  f"{rendered.get('width', '?')} × {rendered.get('height', '?')} · {manifest.name}")
        declaration = metadata.get("desktop_dependencies", {})
        identity = declaration.get("request", {}).get("effective_identity", {})
        identity = identity.get("synthesis", identity)
        job = self.index.jobs.get_job(result.job_id)
        snapshot = PublicationSnapshot.from_job(job)
        with self.index._connection() as connection:
            compatible = self.index._matches(snapshot) and self.index._artifact_inputs_match(connection, job.request)
        if measurements:
            title = "Audio · " + self.repository.get_section(measurements["revision_id"]).title
        else:
            title = "Render result"
        add("result", manifest.artifact_id, key, title, detail,
            heads.get(key) == manifest.artifact_id, heads.get(key), manifest,
            "Generated " + " / ".join(str(identity[k]) for k in ("provider", "model") if k in identity),
            "Retained input bindings: " + ("current" if compatible else "stale") + "; current settings are checked by the pipeline.")

    def _result_keys(self):
        with self.index._connection() as connection:
            return connection.execute("SELECT DISTINCT output_key FROM d040_results").fetchall()

    def restore(self, choice):
        if choice.kind == "prompt":
            VisualPromptService(self.prompts).select(choice.identity, expected_selection_id=choice.expected)
        elif choice.kind == "image":
            ImageIntakeService(self.images).select(choice.identity, expected_selection_id=choice.expected)
        elif choice.kind == "timeline":
            edit = next(e for e in self.timelines.history() if e.id == choice.identity)
            self.timelines.save(edit.timeline, choice.expected)
        elif choice.kind == "result":
            manifest = next(m for m in self.store.list_artifacts() if m.artifact_id == choice.identity)
            if sha256(self.store.read_artifact(manifest.storage_key)).hexdigest() != manifest.checksum:
                raise ValueError("Retained media checksum differs; cannot restore.")
            self.index.restore(choice.scope, choice.identity, expected_artifact_id=choice.expected)
        else:
            raise ValueError("Unknown history choice.")


def compose_history(session):
    return HistoryService(session, ProjectHistory(session.repository))
