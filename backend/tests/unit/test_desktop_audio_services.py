"""Existing preview/selection contracts and durable media adapter behavior."""

import asyncio
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.desktop.audio_services import AudioServices
from app.domain.dependencies import DependencyDeclaration, RequestFingerprint, canonical_json
from app.tts.assembly import inspect_pcm_wav
from app.tts.post_processing import TEMPO_PROCESSOR_VERSION
from tests.unit.test_t086 import _catalog, _wav, FakeProvider


def services(tmp_path):
    provider = FakeProvider()
    production = SimpleNamespace(voices=SimpleNamespace(prepare=lambda selection, words: {
        "effective_identity": {"synthesis": provider.effective_synthesis_identity({
            "voice_id": "builtin", "voice_mode": "builtin", "language_id": selection["language"]})}}))
    adapter = AudioServices(catalog=_catalog(), production=production, coordinator=None, supervisor=None,
                            index=None, store=None, preview_root=tmp_path,
                            preview_builder=lambda config: provider, providers=("mock",))
    return adapter, provider


def test_preview_validates_same_effective_identity_and_uses_existing_cache(tmp_path):
    adapter, provider = services(tmp_path)
    choice, = adapter.choices("en")
    result = asyncio.run(adapter.preview(choice, "Short example"))
    again = asyncio.run(adapter.preview(choice, "Short example"))
    assert result.payload == again.payload == _wav()
    assert provider.synthesis_count == 1
    assert adapter.choices("ja") == ()


def test_preview_rejects_effective_identity_mismatch_before_synthesis(tmp_path):
    adapter, provider = services(tmp_path)
    adapter.production.voices.prepare = lambda *args: {"effective_identity": {"synthesis": {"different": True}}}
    with pytest.raises(ValueError, match="composition failed"):
        asyncio.run(adapter.preview(adapter.choices("en")[0], "Example"))
    assert provider.synthesis_count == 0


@pytest.mark.parametrize("variant", ["original", "processed"])
@pytest.mark.parametrize("revision", ["B1", "B2"])
def test_retained_audio_is_playable_and_staleness_is_explicit(tmp_path, variant, revision):
    adapter, _ = services(tmp_path)
    choice = adapter.choices("en")[0]
    payload = _wav()
    parameters, _ = inspect_pcm_wav(payload)
    checksum = sha256(payload).hexdigest()
    dependency = DependencyDeclaration("section:B:audio:raw", RequestFingerprint.create(
        "section_audio.synthesize", "2", settings={"selection": adapter.selection(choice)})).to_metadata()
    metadata = {"section_audio": {"version": 1, "section_id": "B", "revision_id": "B1",
                 "checksum": checksum, "audio_parameters": parameters.to_payload(),
                 "duration_seconds": parameters.duration_seconds},
                "audio_derivative": {"raw_artifact_id": "raw", "processor_version": TEMPO_PROCESSOR_VERSION},
                **dependency}
    manifest = SimpleNamespace(artifact_id="raw" if variant == "original" else "tempo",
                               storage_key="audio.wav", metadata=metadata, checksum=checksum)
    raw_manifest = SimpleNamespace(artifact_id="raw", storage_key="raw.wav", metadata=metadata, checksum=checksum)
    heads = {"section:B:audio:raw": "raw", "section:B:audio:processed": "tempo"}
    adapter.index = SimpleNamespace(selected=lambda: heads, manifests=lambda: (raw_manifest, manifest))
    adapter.store = SimpleNamespace(read_artifact=lambda key: payload)
    section = SimpleNamespace(section_id="B", id=revision)
    result = adapter.playback(section, variant, choice)
    assert result.payload == payload and result.stale == (revision == "B2")
    if variant == "processed":
        heads["section:B:audio:raw"] = "new-raw"
        assert adapter.playback(section, variant, choice).stale
    adapter.store.read_artifact = lambda key: _wav(frames=801)
    with pytest.raises(ValueError, match="validation"):
        adapter.playback(section, variant, choice)


@pytest.mark.parametrize(("field", "value"), [
    ("voice", "previous-voice"),
    ("settings", {"volume": 0.5}),
])
def test_retained_audio_is_stale_after_voice_or_settings_change(tmp_path, field, value):
    adapter, _ = services(tmp_path)
    choice = adapter.choices("en")[0]
    payload = _wav()
    parameters, _ = inspect_pcm_wav(payload)
    checksum = sha256(payload).hexdigest()
    old_selection = adapter.selection(choice)
    old_selection[field] = value
    dependency = DependencyDeclaration("section:B:audio:raw", RequestFingerprint.create(
        "section_audio.synthesize", "2", settings={"selection": old_selection})).to_metadata()
    metadata = {"section_audio": {"version": 1, "section_id": "B", "revision_id": "B1",
                 "checksum": checksum, "audio_parameters": parameters.to_payload(),
                 "duration_seconds": parameters.duration_seconds}, **dependency}
    manifest = SimpleNamespace(artifact_id="raw", storage_key="audio.wav", metadata=metadata, checksum=checksum)
    adapter.index = SimpleNamespace(selected=lambda: {"section:B:audio:raw": "raw"},
                                    manifests=lambda: (manifest,))
    adapter.store = SimpleNamespace(read_artifact=lambda key: payload)
    result = adapter.playback(SimpleNamespace(section_id="B", id="B1"), "original", choice)
    assert result.payload == payload
    assert result.stale
    assert "STALE" in result.label


def test_retained_audio_uses_prepared_effective_identity_when_available(tmp_path):
    adapter, _ = services(tmp_path)
    choice = adapter.choices("en")[0]
    selection = adapter.selection(choice)
    payload = _wav()
    parameters, _ = inspect_pcm_wav(payload)
    checksum = sha256(payload).hexdigest()
    dependency = DependencyDeclaration("section:B:audio:raw", RequestFingerprint.create(
        "section_audio.synthesize", "2", settings={"selection": selection},
        effective_identity={"runtime": "old"})).to_metadata()
    metadata = {"section_audio": {"version": 1, "section_id": "B", "revision_id": "B1",
                 "checksum": checksum, "audio_parameters": parameters.to_payload(),
                 "duration_seconds": parameters.duration_seconds}, **dependency}
    manifest = SimpleNamespace(artifact_id="raw", storage_key="audio.wav", metadata=metadata, checksum=checksum)
    adapter.index = SimpleNamespace(selected=lambda: {"section:B:audio:raw": "raw"}, manifests=lambda: (manifest,))
    adapter.store = SimpleNamespace(read_artifact=lambda key: payload)
    adapter._prepared_identities[canonical_json(selection)] = {"runtime": "current"}
    assert adapter.playback(SimpleNamespace(section_id="B", id="B1"), "original", choice).stale


def test_unavailable_processed_audio_never_falls_back_to_raw(tmp_path):
    adapter, _ = services(tmp_path)
    adapter.index = SimpleNamespace(selected=lambda: {"section:B:audio:raw": "raw"})
    with pytest.raises(ValueError, match="No selected processed"):
        adapter.playback(SimpleNamespace(section_id="B"), "processed")
