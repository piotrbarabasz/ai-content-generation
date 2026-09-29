"""Immutable plan/script bindings and validated group-result cache artifacts."""

from hashlib import sha256
import json

from app.domain.dependencies import canonical_json
from app.domain.plan_script import (GeneratedScriptGroup, PlanScriptBinding,
                                    PlanScriptBindingSelection)


class ProjectPlanScripts:
    BINDING = "plan_script_binding"
    SELECTION = "plan_script_binding_selection"
    GROUP = "generated_script_group"

    def __init__(self, repository, store, video_plans):
        self.repository, self.store, self.video_plans = repository, store, video_plans
        self.project_id = repository.project().id
        if store._index is None or store._index.repository is not repository:
            raise ValueError("Plan-script artifacts require the owning project artifact store.")

    def _manifest(self, kind, *, value_id=None, cache_key=None):
        matches = [m for m in self.store.list_artifacts() if m.artifact_type == kind
                   and m.metadata.get("project_id") == self.project_id
                   and (value_id is None or m.metadata.get("value_id") == value_id)
                   and (cache_key is None or m.metadata.get("cache_key") == cache_key)]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous plan-script artifact.")
        return matches[0]

    def _read(self, kind, value_id, cls):
        manifest = self._manifest(kind, value_id=value_id)
        raw = self.store.read_artifact(manifest.storage_key)
        if sha256(raw).hexdigest() != manifest.checksum:
            raise ValueError("Plan-script artifact checksum mismatch.")
        value = cls.from_payload(json.loads(raw))
        if value.id != value_id or value.project_id != self.project_id:
            raise ValueError("Plan-script artifact identity or project mismatch.")
        return value

    def _write(self, kind, value, metadata):
        payload = canonical_json(value.to_payload())
        return self.store.save_artifact(kind + ".json", payload,
            {"artifact_type": kind, "project_id": self.project_id,
             "value_id": value.id, "module_name": "desktop_plan_script", **metadata})

    def binding(self, binding_id):
        value = self._read(self.BINDING, binding_id, PlanScriptBinding)
        self.video_plans.revision(value.video_plan_revision_id)
        return value

    def _selection_history(self):
        events = [self._read(self.SELECTION, m.metadata["value_id"], PlanScriptBindingSelection)
                  for m in self.store.list_artifacts() if m.artifact_type == self.SELECTION
                  and m.metadata.get("project_id") == self.project_id]
        children = {}
        for event in events:
            self.binding(event.binding_id)
            if event.parent_selection_id in children:
                raise ValueError("Plan-script binding selection history branches.")
            children[event.parent_selection_id] = event
        result, parent = [], None
        while parent in children:
            event = children.pop(parent)
            result.append(event)
            parent = event.id
        if children:
            raise ValueError("Plan-script binding selection history is incomplete.")
        return tuple(result)

    def active_binding(self):
        history = self._selection_history()
        return self.binding(history[-1].binding_id) if history else None

    def save_binding(self, binding, *, expected_selection_id):
        if binding.project_id != self.project_id:
            raise ValueError("Plan-script binding belongs to a different project.")
        self.video_plans.revision(binding.video_plan_revision_id)
        active = self._selection_history()
        current_id = active[-1].id if active else None
        if current_id != expected_selection_id:
            raise ValueError("Plan-script binding changed; refresh before selecting.")
        self._write(self.BINDING, binding, {"plan_id": binding.video_plan_revision_id})
        from app.domain.base import new_id
        event = PlanScriptBindingSelection(new_id("plan_script_binding_selection"), self.project_id,
                                           binding.id, current_id)
        self._write(self.SELECTION, event, {"binding_id": binding.id})
        return event

    def cached_group(self, cache_key):
        try:
            manifest = self._manifest(self.GROUP, cache_key=cache_key)
        except ValueError as exc:
            # No match means cache miss; ambiguity remains an integrity failure.
            matches = [m for m in self.store.list_artifacts() if m.artifact_type == self.GROUP
                       and m.metadata.get("project_id") == self.project_id
                       and m.metadata.get("cache_key") == cache_key]
            if not matches:
                return None
            raise exc
        value = self._read(self.GROUP, manifest.metadata["value_id"], GeneratedScriptGroup)
        if value.cache_key != cache_key:
            raise ValueError("Generated group cache identity mismatch.")
        return value

    def save_group(self, result):
        if result.project_id != self.project_id:
            raise ValueError("Generated group belongs to a different project.")
        if self.video_plans.revision(result.video_plan_revision_id).project_id != result.project_id:
            raise ValueError("Generated group plan belongs to a different project.")
        if self.cached_group(result.cache_key) is not None:
            raise ValueError("Validated generated group cache already exists.")
        self._write(self.GROUP, result, {"plan_id": result.video_plan_revision_id,
                                         "group_id": result.group_id, "cache_key": result.cache_key})

