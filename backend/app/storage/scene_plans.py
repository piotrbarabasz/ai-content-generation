"""D013 immutable project artifacts; explicit IDs, no automatic active-plan pointer."""

from hashlib import sha256
import json

from app.domain.scene_plan import AcceptedScenePlan, ScenePlan, SceneTimingSet
from app.domain.section_audio import SectionAudio
from app.tts.assembly import inspect_pcm_wav


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
        return self.store.save_artifact(kind + ".json", payload,
                                        {"artifact_type": kind, "project_id": self.project_id, "value_id": value.id,
                                         "module_name": "desktop_scene_planning"})

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

    def proposals(self, section_id):
        manifests = sorted(self.store.list_artifacts(), key=lambda m: (m.created_at, m.artifact_id))
        plans = (self.plan(m.metadata["value_id"]) for m in manifests if m.artifact_type == "desktop_scene_plan")
        return tuple(plan for plan in plans if plan.section_id == section_id)

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

    def acceptances(self, section_id):
        manifests = sorted(self.store.list_artifacts(), key=lambda m: (m.created_at, m.artifact_id))
        values = (self.acceptance(m.metadata["value_id"]) for m in manifests if m.artifact_type == "desktop_scene_acceptance")
        return tuple(value for value in values if value.plan.section_id == section_id)

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
