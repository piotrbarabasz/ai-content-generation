from __future__ import annotations

import pytest

from app.domain.enums import WorkflowPreset
from app.workflow.presets import (
    LONG_FORM_SCRIPT_VOICEOVER_PRESET,
    MVP_WORKFLOW_PRESETS,
    SHORT_VIDEO_PRESET,
)
from app.workflow.registry import (
    WorkflowPresetRegistry,
    WorkflowPresetRegistryError,
    build_mvp_workflow_preset_registry,
)


def test_mvp_workflow_preset_registry_registers_and_resolves_both_canonical_presets() -> None:
    registry = build_mvp_workflow_preset_registry()

    assert registry.list_presets() == MVP_WORKFLOW_PRESETS
    assert registry.has("short_video") is True
    assert registry.has(WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER) is True
    assert registry.get("short_video") is SHORT_VIDEO_PRESET
    assert registry.get(WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER) is LONG_FORM_SCRIPT_VOICEOVER_PRESET

    short_payload = registry.build_workflow_config_payload("short_video", project_id="project_1")
    long_payload = registry.build_workflow_config_payload(WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER)

    assert short_payload["workflowPreset"] == "short_video"
    assert short_payload["enabledModules"] == ["brief", "scenePlanning", "videoRendering", "export"]
    assert short_payload["disabledModules"] == ["voiceover", "captions"]
    assert short_payload["projectId"] == "project_1"
    assert long_payload["workflowPreset"] == "long_form_script_voiceover"
    assert long_payload["enabledModules"] == ["brief", "outline", "scriptGeneration", "postProcessing", "qa", "export"]
    assert long_payload["disabledModules"] == ["research", "dossier", "voiceover"]


def test_workflow_preset_registry_rejects_duplicate_registration_and_unknown_lookup() -> None:
    registry = WorkflowPresetRegistry([SHORT_VIDEO_PRESET])

    with pytest.raises(WorkflowPresetRegistryError, match="Duplicate workflow preset registration: short_video"):
        registry.register("short_video")

    with pytest.raises(WorkflowPresetRegistryError, match="Unknown workflow preset: not_a_preset"):
        registry.get("not_a_preset")
