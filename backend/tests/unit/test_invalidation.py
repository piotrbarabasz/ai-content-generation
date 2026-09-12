"""Product dependency matrix; deterministic values, no providers or media I/O."""

from dataclasses import FrozenInstanceError, replace
import json
import math

import pytest

from app.application.invalidation import Freshness, evaluate_freshness
from app.domain.base import DomainValidationError
from app.domain.dependencies import (
    ArtifactDependency, DependencyDeclaration, FailedAttempt, InputEdge, Provenance,
    RequestFingerprint, content_fingerprint,
)


def pipeline():
    sources = {f"{section}:text": content_fingerprint(f"{section}1") for section in "ABC"}
    sources.update({"style": content_fingerprint("style1"), "order": content_fingerprint(list("ABC")),
                    "global": content_fingerprint(["A1", "B1", "C1"])})
    sources.update({f"{section}:tempo": content_fingerprint(1.0) for section in "ABC"})
    requests, selected, artifacts = {}, {}, {}

    def node(key, source_keys=(), artifact_keys=(), settings=None):
        edges = [InputEdge(name, name, sources[name]) for name in source_keys]
        edges += [InputEdge.artifact(name, name, selected[name], artifacts[selected[name]].checksum) for name in artifact_keys]
        request = RequestFingerprint.create(key.rsplit(":", 1)[-1], "1", inputs=edges, settings=settings)
        artifact = ArtifactDependency(f"{key}:v1", content_fingerprint(key), DependencyDeclaration(key, request))
        requests[key], selected[key], artifacts[artifact.artifact_id] = request, artifact.artifact_id, artifact

    for section in "ABC":
        node(f"{section}:raw", [f"{section}:text"], settings={"voice": "v1", "device": "cpu"})
        node(f"{section}:processed", [f"{section}:tempo"], [f"{section}:raw"])
        node(f"{section}:scene", [f"{section}:text"])
        node(f"{section}:prompt", ["style"], [f"{section}:scene"], {"prompt": "original"})
        node(f"{section}:image", artifact_keys=[f"{section}:prompt"])
        node(f"{section}:timing", artifact_keys=[f"{section}:processed", f"{section}:scene"])
    node("timeline", ["order"], [f"{section}:{kind}" for section in "ABC" for kind in ("image", "timing", "processed")])
    node("render", artifact_keys=["timeline"], settings={"fps": 24, "resolution": "1080p"})
    return dict(requests=requests, sources=sources, selected=selected, artifacts=artifacts)


@pytest.mark.parametrize("change, expected", [
    ("B-text", {"B:raw", "B:processed", "B:scene", "B:prompt", "B:image", "B:timing", "timeline", "render"}),
    ("voice", {"B:raw", "B:processed", "B:timing", "timeline", "render"}),
    ("tempo", {"B:processed", "B:timing", "timeline", "render"}),
    ("prompt", {"B:prompt", "B:image", "timeline", "render"}),
    ("image-variant", {"timeline", "render"}),
    ("order", {"timeline", "render"}),
    ("export", {"render"}),
    ("unchanged", set()),
])
def test_actual_dependency_matrix(change, expected):
    state = pipeline()
    if change == "B-text":
        state["sources"]["B:text"] = content_fingerprint("B2")
    elif change == "tempo":
        state["sources"]["B:tempo"] = content_fingerprint(1.2)
    elif change in ("voice", "prompt", "export"):
        key, settings = {"voice": ("B:raw", {"voice": "v2", "device": "cpu"}),
                         "prompt": ("B:prompt", {"prompt": "edited"}),
                         "export": ("render", {"fps": 30, "resolution": "4k"})}[change]
        old = state["requests"][key]
        state["requests"][key] = RequestFingerprint.create(old.operation, old.algorithm_version, inputs=old.inputs, settings=settings)
    elif change == "image-variant":
        old = state["artifacts"][state["selected"]["B:image"]]
        new = replace(old, artifact_id="B:image:v2")  # Same request/bytes can still be a distinct selected variant.
        state["artifacts"][new.artifact_id] = new
        state["selected"]["B:image"] = new.artifact_id
    elif change == "order":
        state["sources"]["order"] = content_fingerprint(list("CBA"))
    selected_before = dict(state["selected"])
    result = evaluate_freshness(**state)
    assert {key for key, value in result.items() if value.state != Freshness.FRESH} == expected
    assert state["selected"] == selected_before


def test_recorded_global_context_broadens_only_consuming_outputs():
    state = pipeline()
    original = state["requests"]["A:prompt"]
    global_request = replace(original, inputs=(*original.inputs, InputEdge("film context", "global", state["sources"]["global"])))
    state["requests"]["A:prompt"] = global_request
    selected_id = state["selected"]["A:prompt"]
    old = state["artifacts"][selected_id]
    state["artifacts"][selected_id] = replace(old, declaration=replace(old.declaration, request=global_request))
    state["sources"]["global"] = content_fingerprint(["A1", "B2", "C1"])
    result = evaluate_freshness(**state)
    assert {key for key, value in result.items() if value.state == Freshness.STALE} == {"A:prompt", "A:image", "timeline", "render"}
    assert result["A:raw"].state == result["C:prompt"].state == Freshness.FRESH


def test_manual_context_mismatch_requires_review_but_preserves_owned_bytes():
    state = pipeline()
    for section in "ABC":
        artifact_id = state["selected"][f"{section}:prompt"]
        old = state["artifacts"][artifact_id]
        state["artifacts"][artifact_id] = replace(old, declaration=replace(old.declaration, provenance=Provenance.MANUAL))
    before = dict(state["artifacts"])
    state["sources"]["style"] = content_fingerprint("changed style")
    result = evaluate_freshness(**state)
    assert {key for key, value in result.items() if value.state == Freshness.STALE} == {f"{s}:prompt" for s in "ABC"}
    assert all(result[f"{s}:prompt"].review_required for s in "ABC")
    assert result["render"].state == Freshness.FRESH
    assert state["artifacts"] == before


@pytest.mark.parametrize("condition, expected", [("fresh", Freshness.FRESH), ("stale", Freshness.STALE), ("missing", Freshness.MISSING)])
def test_failed_attempt_is_separate_from_selected_artifact_freshness(condition, expected):
    state = pipeline()
    if condition == "stale":
        state["sources"]["B:text"] = content_fingerprint("B2")
    elif condition == "missing":
        del state["selected"]["B:raw"]
    attempt = FailedAttempt("attempt1", "B:raw", state["requests"]["B:raw"].fingerprint, "synthesis failed")
    result = evaluate_freshness(**state, failed_attempts=[attempt])
    assert result["B:raw"].state == expected
    assert result["B:raw"].failed_attempts == (attempt,)
    assert result["A:raw"].failed_attempts == ()
    if condition != "missing":
        assert result["B:raw"].selected_artifact_id == "B:raw:v1"


def test_missing_required_input_makes_retained_output_stale():
    state = pipeline()
    del state["sources"]["B:text"]
    result = evaluate_freshness(**state)
    assert result["B:raw"].state == Freshness.STALE
    assert "missing input: B:text" in result["B:raw"].reasons
    state["selected"].clear()
    assert all(value.state == Freshness.MISSING for value in evaluate_freshness(**state).values())


def test_cycles_and_wrong_selected_binding_are_rejected():
    state = pipeline()
    state["requests"]["B:raw"] = replace(state["requests"]["B:raw"], inputs=(
        InputEdge.artifact("cycle", "B:processed", "B:processed:v1", content_fingerprint("x")),))
    with pytest.raises(DomainValidationError, match="cycle"):
        evaluate_freshness(**state)
    state = pipeline()
    state["selected"]["B:raw"] = state["selected"]["A:raw"]
    with pytest.raises(DomainValidationError, match="binding"):
        evaluate_freshness(**state)


def test_canonical_requests_freeze_content_and_ignore_only_declared_irrelevant_settings():
    settings = {"voice": "v1", "nested": {"b": 2, "a": 1}, "ui_zoom": 2}
    first = RequestFingerprint.create("tts", "1", settings=settings, relevant_settings=["voice", "nested"])
    same = RequestFingerprint.create("tts", "1", settings={"nested": {"a": 1, "b": 2}, "voice": "v1", "ui_zoom": 99},
                                     relevant_settings=["nested", "voice"])
    assert first.fingerprint == same.fingerprint
    settings["nested"]["a"] = 100
    assert first.fingerprint == same.fingerprint
    assert RequestFingerprint.from_payload(first.to_payload()) == first
    with pytest.raises(FrozenInstanceError):
        first.operation = "modified"
    for other in [replace(first, algorithm_version="2"),
                  replace(first, effective_identity_json='{"provider":"p","model_revision":"2","device":"cpu"}'),
                  replace(first, settings_json='{"voice":"v2"}')]:
        assert other.fingerprint != first.fingerprint


@pytest.mark.parametrize("value", [math.nan, math.inf, {1: "bad"}, {"a": object()}, {"a": (1, 2)}])
def test_noncanonical_values_are_rejected(value):
    with pytest.raises(DomainValidationError):
        content_fingerprint(value)


@pytest.mark.parametrize("field", ["provider", "model", "model_revision", "device", "reference_checksum"])
def test_effective_generation_identity_changes_only_consuming_audio_branch(field):
    state = pipeline()
    identity = {"provider": "mock", "model": "voice", "model_revision": "1", "device": "cpu",
                "reference_checksum": content_fingerprint("reference")}
    request = replace(state["requests"]["B:raw"], effective_identity_json=json.dumps(identity))
    artifact = state["artifacts"]["B:raw:v1"]
    state["artifacts"][artifact.artifact_id] = replace(artifact, declaration=replace(artifact.declaration, request=request))
    identity[field] = "changed"
    state["requests"]["B:raw"] = replace(request, effective_identity_json=json.dumps(identity))
    result = evaluate_freshness(**state)
    assert {key for key, value in result.items() if value.state == Freshness.STALE} == {
        "B:raw", "B:processed", "B:timing", "timeline", "render"}


def test_input_order_is_canonical_but_text_and_sequence_order_are_significant():
    a, b = InputEdge("a", "A", content_fingerprint("A")), InputEdge("b", "B", content_fingerprint("B"))
    assert RequestFingerprint.create("x", "1", inputs=[a, b]) == RequestFingerprint.create("x", "1", inputs=[b, a])
    assert content_fingerprint("text") != content_fingerprint("text ")
    assert content_fingerprint(["A", "B"]) != content_fingerprint(["B", "A"])
    with pytest.raises(DomainValidationError):
        RequestFingerprint.create("x", "1", inputs=[a, a])
    with pytest.raises(DomainValidationError):
        RequestFingerprint.create("x", "1", settings={}, relevant_settings=["voice"])
