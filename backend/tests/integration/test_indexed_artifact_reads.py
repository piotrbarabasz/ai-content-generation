"""D081: indexed reads and scoped history retain the existing verification gates."""

from collections import Counter
import json
import sqlite3

import pytest

from app.application.projects import ProjectSession
from app.storage.local_store import LocalArtifactStore
from app.storage.manifest import ArtifactManifest
from app.storage.narrative_reviews import ProjectNarrativeRecords
from app.storage.narration import ProjectNarration
from app.storage.project_repository import ProjectRepository
from app.storage.scene_plans import ProjectScenePlans
from tests.integration.test_timeline import setup, prepared  # noqa: F401


def no_catalog(*args, **kwargs):
    pytest.fail("Point/scoped reads must not enumerate the full catalog")


def test_id_key_reads_use_existing_indexes_and_survive_reopen(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    with ProjectSession.create(workspace, name="Indexed", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        manifest = store.save_artifact("retained.txt", b"retained")
        for n in range(12):
            store.save_artifact("other.txt", str(n))
        with store._index._connection() as connection:
            for column, value in (("artifact_id", manifest.artifact_id), ("storage_key", manifest.storage_key)):
                plan = connection.execute(f"EXPLAIN QUERY PLAN SELECT manifest_json FROM artifacts WHERE {column}=?",
                                          (value,)).fetchall()
                assert any("SEARCH artifacts USING INDEX" in row[3] for row in plan)
        for reader in (store._index, store):
            monkeypatch.setattr(reader, "manifests" if reader is store._index else "list_artifacts", no_catalog)
        assert store.manifest_by_id(manifest.artifact_id) == manifest
        assert store.manifest_by_key(manifest.storage_key) == manifest
        with store.open_artifact_id(manifest.artifact_id) as source:
            assert source.read() == b"retained"
        assert store.read_artifact(manifest.storage_key) == b"retained"
        assert store.manifest_by_id("unknown") is None
        assert store.manifest_by_key("unregistered.txt") is None
        (store.root / "unregistered.txt").write_bytes(b"not published")
        for action in (lambda: store.open_artifact_id("unknown"),
                       lambda: store.open_artifact("unregistered.txt"),
                       lambda: store.open_artifact_id("../../project.sqlite")):
            with pytest.raises(FileNotFoundError):
                action()
        with pytest.raises(ValueError):
            store.open_artifact("../project.sqlite")
    with pytest.raises(sqlite3.ProgrammingError):
        store.manifest_by_id(manifest.artifact_id)
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        assert store.manifest_by_id(manifest.artifact_id) == manifest
        assert store.read_artifact(manifest.storage_key) == b"retained"


@pytest.mark.parametrize("field", ["artifact_id", "storage_key", "checksum", "size_bytes"])
def test_indexed_point_reads_reject_manifest_row_mismatch(tmp_path, field):
    with ProjectSession.create(tmp_path, name="Corruption", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        manifest = store.save_artifact("one.txt", b"one")
        payload = manifest.to_payload()
        payload[field] = 999 if field == "size_bytes" else "different"
        with store._index._connection() as connection, connection:
            connection.execute("UPDATE artifacts SET manifest_json=? WHERE artifact_id=?",
                               (json.dumps(payload), manifest.artifact_id))
        for read in (lambda: store.manifest_by_id(manifest.artifact_id),
                     lambda: store.manifest_by_key(manifest.storage_key)):
            with pytest.raises(ValueError, match="registered index identity"):
                read()


def test_point_reads_recheck_project_owner(tmp_path):
    with ProjectSession.create(tmp_path, name="Owner", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        manifest = store.save_artifact("one.txt", b"one")
        with store._index._connection() as connection, connection:
            connection.execute("UPDATE owner SET project_id='foreign'")
        with pytest.raises(ValueError, match="different project"):
            store.open_artifact_id(manifest.artifact_id)
        with pytest.raises(ValueError, match="different project"):
            store.open_artifact(manifest.storage_key)


def test_legacy_sidecar_store_keeps_point_and_filtered_history_reads(tmp_path):
    store = LocalArtifactStore(tmp_path)
    old = store.save_artifact("old.txt", b"historical", {"artifact_type": "old", "scene_id": "one"})
    store.save_artifact("other.txt", b"other", {"artifact_type": "old", "scene_id": "two"})
    assert store.manifest_by_id(old.artifact_id) == old
    assert store.manifest_by_key(old.storage_key) == old
    assert store.find_manifests("old", metadata={"scene_id": "one"}) == (old,)
    with store.open_artifact_id(old.artifact_id) as stream:
        assert stream.read() == b"historical"


def test_compile_and_image_history_never_enumerate_catalog(setup, prepared, monkeypatch):
    section, _, _, _, _, _, intake, _, images, compiler, inputs = prepared
    store = setup[3]
    monkeypatch.setattr(store, "list_artifacts", no_catalog)
    monkeypatch.setattr(store._index, "manifests", no_catalog)
    assert len(compiler.compile(section.project_id, inputs).clips) == len(inputs)
    image, selection = images[0]
    assert intake.images.history(image.scene_id) == (image,)
    assert intake.images.selection_history(image.scene_id) == (selection,)


def test_resolution_reuses_verified_records_but_next_operation_checks_bytes(setup, prepared, monkeypatch):
    store, compiler, source = setup[3], prepared[9], prepared[10][0]
    opens = Counter()
    original = store.open_artifact
    def opened(key):
        opens[key] += 1
        return original(key)
    monkeypatch.setattr(store, "open_artifact", opened)
    media = compiler.media.resolve(source)
    # Timing, acceptance and proposal are each read once despite nested checks.
    keys = [m.storage_key for m in store.find_manifests()
            if m.artifact_type in ("desktop_scene_timing", "desktop_scene_acceptance", "desktop_scene_plan")]
    assert all(opens[key] == 1 for key in keys)
    assert compiler.media.plans._records is None and compiler.media.images._verified is None
    image = store.manifest_by_id(media.image.artifact_id)
    (store.root / image.storage_key).write_bytes(b"modified after resolution")
    with pytest.raises(ValueError, match="bytes differ"):
        compiler.media.resolve(source)


def test_scoped_scene_history_skips_unrelated_payloads_and_keeps_unknown_legacy_scope(setup, prepared, monkeypatch):
    store, accepted = setup[3], prepared[4]
    plans = ProjectScenePlans(setup[0].repository, store)
    foreign = store.save_artifact("unrelated.json", b"corrupt historical body", {
        "artifact_type": "desktop_scene_acceptance", "project_id": plans.project_id,
        "value_id": "unrelated", "section_id": "another-section", "revision_id": "another-revision"})
    # An old valid header without optional scope must still be resolved.
    manifest = store.find_manifests("desktop_scene_acceptance", metadata={"value_id": accepted.id})[0]
    payload = manifest.to_payload()
    payload["metadata"].pop("section_id")
    payload["metadata"].pop("revision_id")
    with store._index._connection() as connection, connection:
        connection.execute("UPDATE artifacts SET manifest_json=? WHERE artifact_id=?",
                           (json.dumps(payload), manifest.artifact_id))
    read = store.read_artifact
    def scoped(key):
        assert key != foreign.storage_key
        return read(key)
    monkeypatch.setattr(store, "read_artifact", scoped)
    assert plans.acceptances(accepted.plan.section_id, revision_id=accepted.plan.revision_id) == (accepted,)
    # Unknown-scope corruption still blocks, never falls back to an older choice.
    (store.root / manifest.storage_key).write_bytes(b"corrupt relevant body")
    with pytest.raises(ValueError, match="checksum"):
        plans.acceptances(accepted.plan.section_id, revision_id=accepted.plan.revision_id)


def test_scoped_narration_history_reconstructs_only_matching_headers(setup, monkeypatch):
    store = setup[3]
    records = ProjectNarrativeRecords(setup[0].repository, store)
    expected = {"version": 1, "id": "retained", "scope": "project"}
    records.save("narration_settings", expected)
    records.save("unrelated_review", {"version": 1})
    store.save_artifact("foreign.json", b"unreadable", {"artifact_type": "narrative_narration_settings",
                                                        "project_id": "foreign"})
    reconstructed = []
    parse = ArtifactManifest.from_payload
    def parsed(cls, payload):
        reconstructed.append(payload)
        return parse(payload)
    monkeypatch.setattr(ArtifactManifest, "from_payload", classmethod(parsed))
    monkeypatch.setattr(store, "list_artifacts", no_catalog)
    history = records.history("narration_settings")
    assert len(reconstructed) == len(history) == 1
    assert records.read(history[0]) == expected


def test_narration_scope_filters_before_reading_and_retains_legacy_headers(setup, monkeypatch):
    store, session = setup[3], setup[0]
    narration = ProjectNarration(session, store)
    retained = {"version": 1, "id": "old", "scope": "project"}
    # Original narration artifacts have project identity but no scope in metadata.
    store.save_artifact("old.json", json.dumps(retained), {
        "artifact_type": "narrative_narration_settings", "project_id": session.project.id})
    relevant = narration.save("settings", {"scope": "project"})
    unrelated = narration.save("settings", {"scope": "another-section"})
    manifest = store.find_manifests("narrative_narration_settings", metadata={"scope": "another-section"})[0]
    (store.root / manifest.storage_key).write_bytes(b"unreadable unrelated historical settings")
    assert narration.history("settings", scope="project") == (retained, relevant)
    with pytest.raises(ValueError, match="checksum"):
        narration.history("settings", scope=unrelated["scope"])
    monkeypatch.setattr(store, "list_artifacts", no_catalog)
    assert narration.history("settings", scope="project") == (retained, relevant)


def test_nested_metadata_filter_matches_legacy_and_project_catalogs(setup, tmp_path):
    for store in (setup[3], LocalArtifactStore(tmp_path / "sidecars")):
        old = store.save_artifact("old.wav", b"old", {"section_audio": {"section_id": "old-section"}})
        store.save_artifact("other.wav", b"other", {"section_audio": {"section_id": "other-section"}})
        assert store.find_manifests(metadata={"section_audio.section_id": "old-section"}) == (old,)
