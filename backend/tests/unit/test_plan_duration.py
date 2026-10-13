"""Offline guardrail measurements with the real retained-PCM playback validator."""

from fractions import Fraction
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
import wave

import pytest

from app.application.plan_duration import plan_duration_result, selected_narration
from app.application.pipeline_diagnostics import PipelineDiagnostics
from app.desktop.audio_services import AudioServices
from app.desktop.pipeline_driver import DesktopPipelineDriver
from app.domain.dependencies import DependencyDeclaration, RequestFingerprint
from app.domain.video_plan import VideoFormat
from app.tts.assembly import inspect_pcm_wav


def retained_audio(seconds=600):
    section = SimpleNamespace(id="revision", section_id="section", title="Narration")
    plan = SimpleNamespace(id="plan", format=VideoFormat.STANDARD, target_duration_seconds=600, groups=(1,))
    choice = SimpleNamespace(voice="selected-voice")
    stream = BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(100)
        wav.writeframes(b"\0\0" * (seconds * 100))
    payload = stream.getvalue()
    pcm, _ = inspect_pcm_wav(payload)
    checksum = sha256(payload).hexdigest()
    declaration = DependencyDeclaration("section:section:audio:raw",
        RequestFingerprint.create("audio", "v1", settings={"selection": {"voice": choice.voice}}))
    manifest = SimpleNamespace(artifact_id="audio", checksum=checksum, storage_key="audio.wav", metadata={
        **declaration.to_metadata(),
        "section_audio": {"version": 1, "section_id": section.section_id, "revision_id": section.id,
                          "checksum": checksum, "duration_seconds": pcm.frame_count / pcm.sample_rate,
                          "audio_parameters": pcm.to_payload()},
    })
    heads = {"section:section:audio:raw": "audio"}
    services = AudioServices.__new__(AudioServices)
    services.index = SimpleNamespace(selected=lambda: heads, manifests=lambda: (manifest,))
    services.store = SimpleNamespace(read_artifact=lambda _: payload)
    services.selection = lambda selected: {"voice": selected.voice}
    services._prepared_identities = {}
    editor = SimpleNamespace(audio=SimpleNamespace(services=services),
        video_plan=SimpleNamespace(plans=SimpleNamespace(selected=lambda: plan)),
        session=SimpleNamespace(active_script=SimpleNamespace(id="script", script_id="script-id", sections=(section,))))
    config = SimpleNamespace(voice_choice=choice, audio_variant="original")
    return editor, config, section, manifest, heads, plan


@pytest.mark.parametrize("seconds, state, target_delta, boundary_delta", [
    (540, "OK", -60, 0), (600, "OK", 0, 60), (660, "OK", 60, 0),
    (489, "BLOCKED", -111, -51), (700, "BLOCKED", 100, 40),
])
def test_driver_uses_selected_validated_pcm_measurements(seconds, state, target_delta, boundary_delta):
    editor, config, section, _, _, plan = retained_audio(seconds)
    result = DesktopPipelineDriver(editor, config).measure_plan_duration(plan, (section,), config)
    assert result.state == state
    assert result.actual == Fraction(seconds)
    assert result.target_difference == target_delta and result.boundary_difference == boundary_delta
    assert "Target: 600s (10:00)" in result.summary
    assert "Allowed: 540–660s (9:00–11:00)" in result.summary
    if seconds == 489:
        assert "Measured narration: 489s (8:09)" in result.summary
        assert "Minimum additional narration needed: 51s" in result.summary
    if seconds == 700:
        assert "Minimum narration reduction needed: 40s" in result.summary


@pytest.mark.parametrize("problem", ["missing", "revision", "section", "voice", "checksum", "frames", "rate"])
def test_missing_stale_or_invalid_audio_is_pending_not_a_success(problem):
    editor, config, section, manifest, heads, plan = retained_audio()
    if problem == "missing":
        heads.clear()
    elif problem in {"revision", "section"}:
        manifest.metadata["section_audio"][f"{problem}_id"] = "historical"
    elif problem == "voice":
        config.voice_choice = SimpleNamespace(voice="another-voice")
    elif problem == "checksum":
        editor.audio.services.store.read_artifact = lambda _: b"corrupt"
    elif problem == "frames":
        manifest.metadata["section_audio"]["audio_parameters"]["frame_count"] += 100
        manifest.metadata["section_audio"]["duration_seconds"] += 1
    elif problem == "rate":
        manifest.metadata["section_audio"]["audio_parameters"]["sample_rate"] = 0
    result = DesktopPipelineDriver(editor, config).measure_plan_duration(plan, (section,), config)
    assert result.state == "PENDING" and result.actual is None
    assert result.target_difference is result.boundary_difference is None


@pytest.mark.parametrize("changed", ["plan", "script"])
def test_measurement_fences_changed_plan_or_script(changed):
    editor, config, section, _, _, plan = retained_audio()
    driver = DesktopPipelineDriver(editor, config)
    if changed == "script":
        editor.session.active_script.sections = ()
    else:
        editor.video_plan.plans.selected = lambda: SimpleNamespace(id="another-plan")
    with pytest.raises(ValueError, match="plan or script changed"):
        driver.measure_plan_duration(plan, (section,), config)


def test_unselected_historical_audio_does_not_contribute_to_current_duration():
    editor, config, section, manifest, _, plan = retained_audio()
    historical = SimpleNamespace(artifact_id="old-audio", metadata={}, checksum="historical")
    editor.audio.services.index.manifests = lambda: (historical, manifest)
    result = DesktopPipelineDriver(editor, config).measure_plan_duration(plan, (section,), config)
    assert result.state == "OK" and result.actual == 600


@pytest.mark.parametrize("seconds, state", [(600, "OK"), (489, "BLOCKED"), (700, "BLOCKED")])
def test_diagnostics_show_guardrail_result_in_existing_summary(seconds, state):
    editor, config, _, _, _, plan = retained_audio(seconds)
    binding = SimpleNamespace(video_plan_revision_id=plan.id, script_id="script-id")
    scripts = SimpleNamespace(artifacts=SimpleNamespace(active_binding=lambda: binding),
                              progress=lambda _: ("complete",))
    report = PipelineDiagnostics().inspect_project(editor.session, audio=editor.audio,
        audio_choice=config.voice_choice, video_plans=editor.video_plan.plans, plan_script=scripts)
    duration = next(d for d in report.diagnostics if d.stage == "PLAN_DURATION")
    assert duration.state == state
    assert f"PLAN_DURATION {state}" in report.summary
    assert f"Measured narration: {seconds}s" in report.summary


def test_diagnostics_missing_narration_displays_pending_instead_of_zero():
    editor, config, _, _, heads, plan = retained_audio()
    heads.clear()
    report = PipelineDiagnostics().inspect_project(editor.session, audio=editor.audio,
        audio_choice=config.voice_choice, video_plans=editor.video_plan.plans)
    assert "PLAN_DURATION PENDING" in report.summary
    assert "Measured narration: pending" in report.summary
    assert "Measured narration: 0" not in report.summary
