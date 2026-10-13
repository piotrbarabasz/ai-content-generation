"""D013 immutable project artifacts; explicit IDs, no automatic active-plan pointer."""

from hashlib import sha256
import json
import logging

from app.domain.scene_plan import AcceptedScenePlan, ScenePlan, SceneTimingSet
from app.domain.section_audio import SectionAudio
from app.tts.assembly import inspect_pcm_wav

logger = logging.getLogger(__name__)


class ProjectScenePlans:
    def __init__(self, repository, store):
        if store._index is None or store._index.repository is not repository:
            raise ValueError("Scene plans require the owning project artifact store.")
        self.repository, self.store = repository, store
        self.project_id = repository.project().id

    def current(self, section):
        if section.project_id != self.project_id or self.repository.active_script().section(section.section_id) != section:
            raise ValueError("Scene operation requires the expected current project section.")

    def verify_audio(self, section, audio):
        self.current(section)
        if (audio.section_id, audio.revision_id) != (section.section_id, section.id):
            raise ValueError("Scene audio belongs to another section revision.")
        manifest = next((m for m in self.store.list_artifacts() if m.artifact_id == audio.artifact_id), None)
        if manifest is None or SectionAudio.from_manifest(manifest) != audio:
            raise ValueError("Scene audio is not the registered artifact measurement.")
        payload = self.store.read_artifact(manifest.storage_key)
        measured, _ = inspect_pcm_wav(payload)
        if (sha256(payload).hexdigest() != audio.checksum
                or measured.to_payload() != manifest.metadata["section_audio"]["audio_parameters"]):
            raise ValueError("Scene audio bytes differ from registered measurements.")

    def _read(self, kind, value_id, value_type):
        matches = [m for m in self.store.list_artifacts() if m.artifact_type == kind
                   and m.metadata.get("value_id") == value_id]
        if len(matches) != 1 or matches[0].metadata.get("project_id") != self.project_id:
            raise ValueError("Unknown or ambiguous project scene artifact.")
        manifest = matches[0]
        payload = self.store.read_artifact(manifest.storage_key)
        if sha256(payload).hexdigest() != manifest.checksum:
            raise ValueError("Scene artifact checksum mismatch.")
        value = value_type.from_payload(json.loads(payload))
        if value.id != value_id:
            raise ValueError("Scene artifact identity differs from the index.")
        return value

    def _save(self, kind, value):
        payload = json.dumps(value.to_payload(), ensure_ascii=False, sort_keys=True)
        plan = (value.plan if isinstance(value, AcceptedScenePlan) else value if isinstance(value, ScenePlan)
                else self.acceptance(value.acceptance_id).plan)
        return self.store.save_artifact(kind + ".json", payload,
                                        {"artifact_type": kind, "project_id": self.project_id, "value_id": value.id,
                                         "section_id": plan.section_id, "revision_id": plan.revision_id,
                                         **({"acceptance_id": value.acceptance_id} if isinstance(value, SceneTimingSet) else {}),
                                         "module_name": "desktop_scene_planning"})

    def history_records(self, kind, value_type):
        """Read individual immutable headers without resolving historical dependencies."""
        for m in sorted(self.store.list_artifacts(), key=lambda m: (m.created_at, m.artifact_id)):
            if m.artifact_type != kind: continue
            try:
                value = self._read(kind, m.metadata["value_id"], value_type)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                yield m, None, f"Unavailable {m.artifact_id}: {exc}. Inspect project history or restore a verified backup."
            else: yield m, value, None

    def _history(self, kind, value_type, load, *, section_id=None, revision_id=None, acceptance_id=None):
        values = []
        for m, value, error in self.history_records(kind, value_type):
            plan = value.plan if isinstance(value, AcceptedScenePlan) else value
            scope = {"section_id": getattr(plan, "section_id", None), "revision_id": getattr(plan, "revision_id", None),
                     "acceptance_id": getattr(value, "acceptance_id", None)} if value else m.metadata
            filters = {"section_id": section_id, "revision_id": revision_id, "acceptance_id": acceptance_id}
            if any(expected is not None and scope.get(key) is not None and scope[key] != expected for key, expected in filters.items()):
                if error: logger.warning(error)
                continue
            if error: raise ValueError(error)  # Unknown/current corruption must never select an older fallback.
            values.append(load(value.id))
        return tuple(values)

    def save_plan(self, section, plan):
        self.current(section)
        plan.validate_section(section)
        self._save("desktop_scene_plan", plan)

    def plan(self, plan_id):
        plan = self._read("desktop_scene_plan", plan_id, ScenePlan)
        if plan.project_id != self.project_id:
            raise ValueError("Scene plan belongs to a different project.")
        plan.validate_section(self.repository.get_section(plan.revision_id))
        return plan  # Historical proposals remain readable, but cannot be accepted as current.

    def proposals(self, section_id, *, revision_id=None):
        return self._history("desktop_scene_plan", ScenePlan, self.plan, section_id=section_id, revision_id=revision_id)

    def save_acceptance(self, acceptance):
        plan = self.plan(acceptance.plan.id)
        if plan != acceptance.plan:
            raise ValueError("Acceptance differs from the retained proposal.")
        self.current(self.repository.get_section(plan.revision_id))
        self._save("desktop_scene_acceptance", acceptance)

    def acceptance(self, acceptance_id):
        accepted = self._read("desktop_scene_acceptance", acceptance_id, AcceptedScenePlan)
        if self.plan(accepted.plan.id) != accepted.plan:
            raise ValueError("Accepted scene plan differs from the retained proposal.")
        return accepted

    def acceptances(self, section_id, *, revision_id=None):
        return self._history("desktop_scene_acceptance", AcceptedScenePlan, self.acceptance, section_id=section_id, revision_id=revision_id)

    def timings(self, acceptance_id):
        return self._history("desktop_scene_timing", SceneTimingSet, self.timing, acceptance_id=acceptance_id)

    def save_timing(self, section, timing):
        self.current(section)
        accepted = self.acceptance(timing.acceptance_id)
        accepted.plan.validate_section(section)
        if timing.plan_id != accepted.plan.id or tuple(s.scene_id for s in timing.scenes) != tuple(s.id for s in accepted.plan.scenes):
            raise ValueError("Timing must retain the exact accepted scene identities.")
        self._save("desktop_scene_timing", timing)

    def timing(self, timing_id):
        timing = self._read("desktop_scene_timing", timing_id, SceneTimingSet)
        accepted = self.acceptance(timing.acceptance_id)
        if timing.plan_id != accepted.plan.id or tuple(s.scene_id for s in timing.scenes) != tuple(s.id for s in accepted.plan.scenes):
            raise ValueError("Timing differs from its retained acceptance.")
        return timing
