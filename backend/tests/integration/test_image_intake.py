"""D016: real PNG/JPEG fixtures, project ownership, failures and relocation."""

from dataclasses import replace
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image
import pytest

from app.application.image_intake import ImageIntakeService
from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.storage import scene_images
from app.storage.image_decoder import ImageLimits
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_images import ProjectSceneImages
from app.storage.scene_plans import ProjectScenePlans
from app.tts.scene_sources import sentence_sources


def encoded(format="PNG", *, size=(12, 8), mode="RGB", **kwargs):
    output = io.BytesIO()
    Image.new(mode, size).save(output, format=format, **kwargs)
    return output.getvalue()


@pytest.fixture
def project(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="Images", repository_factory=ProjectRepository) as session:
        scripts = ScriptGenerationService(session)
        scripts.append_text("First scene.\n\nSecond scene.", title="Section", expected_active_revision_id=session.active_script.id)
        store = LocalArtifactStore.for_project(session.repository)
        plans = ScenePlanningService(ProjectScenePlans(session.repository, store), sentence_sources)
        accepted = plans.accept(plans.suggest(session.active_script.sections[0]).id, reviewer_id="editor")
        adapter = ProjectSceneImages(session.repository, store)
        source = tmp_path / "Źródło ze spacją.png"
        source.write_bytes(encoded())
        yield session, store, adapter, ImageIntakeService(adapter), accepted, source


def inputs(project, scene=0):
    accepted = project[4]
    return accepted.id, accepted.plan.scenes[scene].id, project[5]


def selected(project, scene=0, expected=None):
    return project[3].import_and_select(*inputs(project, scene), expected_selection_id=expected)


@pytest.mark.parametrize("format,mode", [("PNG", "RGB"), ("PNG", "RGBA"), ("PNG", "P"), ("JPEG", "RGB"), ("JPEG", "L"), ("JPEG", "CMYK")])
def test_measured_format_and_original_source_survive_reopen_and_move(project, tmp_path, format, mode):
    session, store, adapter, service, accepted, source = project
    raw = encoded(format, size=(17, 11), mode=mode)
    source.write_bytes(raw)  # Extension is deliberately misleading for JPEG.
    image, choice = selected(project)
    assert (image.format, image.width, image.height, image.mode) == (format, 17, 11, mode)
    assert image.checksum == sha256(raw).hexdigest() and image.size_bytes == len(raw)
    assert image.source_name == source.name and image.provenance == "imported"
    assert image.section_revision_id == accepted.plan.revision_id
    public = json.dumps(image.to_payload())
    assert str(tmp_path) not in public and "source_path" not in public
    with store.open_artifact_id(image.artifact_id) as stream:
        assert stream.read() == raw
    original = session.repository.workspace
    session.close()
    source.unlink()  # The project must own the source bytes now.
    moved = tmp_path / "Przeniesiony projekt Żółć"
    shutil.move(str(original), moved)
    with ProjectSession.open(moved, repository_factory=ProjectRepository) as reopened:
        new_store = LocalArtifactStore.for_project(reopened.repository)
        restored = ProjectSceneImages(reopened.repository, new_store)
        assert restored.image(image.artifact_id) == image
        assert restored.selected(image.scene_id) == choice
        with new_store.open_artifact_id(image.artifact_id) as stream:
            assert stream.read() == raw


def test_exif_orientation_is_retained_without_rotating_original(project):
    exif = Image.Exif()
    exif[274] = 6
    project[5].write_bytes(encoded("JPEG", exif=exif))
    image, _ = selected(project)
    assert (image.width, image.height, image.orientation) == (12, 8, 6)
    assert image.checksum == sha256(project[5].read_bytes()).hexdigest()


def test_import_and_reselection_retain_other_scenes_audio_and_history(project):
    _, store, adapter, service, _, source = project
    audio = store.save_artifact("old.wav", b"retained audio")
    first, initial = selected(project)
    other, _ = selected(project, 1)
    before = {m.storage_key: store.read_artifact(m.storage_key) for m in store.list_artifacts()}
    source.write_bytes(encoded(size=(7, 5)))
    candidate = service.import_file(*inputs(project))
    assert service.selected(first.scene_id) == first
    chosen = service.select(candidate.artifact_id, expected_selection_id=initial.id)
    restored = service.select(first.artifact_id, expected_selection_id=chosen.id)
    assert adapter.selection_history(first.scene_id) == (initial, chosen, restored)
    assert adapter.history(first.scene_id) == (first, candidate)
    assert service.selected(other.scene_id) == other
    assert store.read_artifact(audio.storage_key) == b"retained audio"
    assert all(store.read_artifact(key) == payload for key, payload in before.items())
    with pytest.raises(ValueError, match="changed"):
        service.select(candidate.artifact_id, expected_selection_id=initial.id)
    assert adapter.selected(first.scene_id) == restored


@pytest.mark.parametrize("bad", [b"", b"not an image", encoded("GIF"), encoded("BMP"), encoded("JPEG")[:-12], encoded("PNG")[:-12]])
def test_invalid_import_keeps_previous_image_and_catalog(project, bad):
    first, initial = selected(project)
    before = project[1].list_artifacts()
    project[5].write_bytes(bad)
    with pytest.raises(ValueError):
        selected(project, expected=initial.id)
    assert project[3].selected(first.scene_id) == first and project[2].selected(first.scene_id) == initial
    assert project[1].list_artifacts() == before


@pytest.mark.parametrize("limits", [ImageLimits(max_bytes=64), ImageLimits(max_dimension=10), ImageLimits(max_pixels=50)])
def test_limits_reject_before_publication(project, limits):
    first, initial = selected(project)
    before = project[1].list_artifacts()
    strict = ImageIntakeService(ProjectSceneImages(project[0].repository, project[1], limits=limits))
    with pytest.raises(ValueError):
        strict.import_and_select(*inputs(project), expected_selection_id=initial.id)
    assert project[1].list_artifacts() == before and project[3].selected(first.scene_id) == first


def test_animated_png_is_rejected(project):
    output = io.BytesIO()
    Image.new("RGB", (5, 5), "red").save(output, format="PNG", save_all=True,
                                          append_images=[Image.new("RGB", (5, 5), "blue")], duration=100)
    project[5].write_bytes(output.getvalue())
    before = project[1].list_artifacts()
    with pytest.raises(ValueError):
        selected(project)
    assert project[1].list_artifacts() == before


@pytest.mark.parametrize("kind", ["scene", "acceptance", "traversal", "ads", "absolute-outside"])
def test_foreign_scene_and_unsafe_sources_are_rejected_without_changing_selection(project, tmp_path, kind):
    first, initial = selected(project)
    accepted, scene, source = inputs(project)
    root = tmp_path / "chosen"
    root.mkdir()
    if kind == "scene": scene = "../foreign-scene"
    if kind == "acceptance": accepted = "unknown-plan"
    if kind == "traversal": source = "../private.png"
    if kind == "ads": source = "image.png:private"
    before = project[1].list_artifacts()
    with pytest.raises(ValueError):
        project[3].import_and_select(accepted, scene, source, source_root=root, expected_selection_id=initial.id)
    assert project[1].list_artifacts() == before and project[3].selected(first.scene_id) == first


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction test")
def test_redirected_source_directory_cannot_be_imported(project, tmp_path):
    import _winapi
    root = tmp_path / "chosen"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "image.png").write_bytes(encoded())
    link = root / "redirect"
    _winapi.CreateJunction(str(outside), str(link))
    before = project[1].list_artifacts()
    try:
        with pytest.raises(ValueError):
            project[3].import_file(*inputs(project)[:2], "redirect/image.png", source_root=root)
        assert project[1].list_artifacts() == before
    finally:
        link.rmdir()


def test_foreign_project_image_and_selection_are_rejected(project, tmp_path):
    image, choice = selected(project)
    with ProjectSession.create(tmp_path / "other", name="Other", repository_factory=ProjectRepository) as other:
        store = LocalArtifactStore.for_project(other.repository)
        adapter = ProjectSceneImages(other.repository, store)
        with pytest.raises(ValueError): adapter.image(image.artifact_id)
        with pytest.raises(ValueError): adapter.save_selection(choice)
        assert store.list_artifacts() == ()
    with pytest.raises(ValueError):
        project[2].save_selection(replace(choice, id="foreign", scene_id=project[4].plan.scenes[1].id))
    assert project[2].selected(image.scene_id) == choice


def test_scene_edit_during_decode_cannot_publish_or_replace_image(project, monkeypatch):
    first, initial = selected(project)
    before = project[1].list_artifacts()
    decode = scene_images.decode_image
    def editing(payload, limits):
        result = decode(payload, limits)
        project[0].edit_section(project[0].active_script.sections[0].section_id, text="Changed scene.")
        return result
    monkeypatch.setattr(scene_images, "decode_image", editing)
    with pytest.raises(ValueError): selected(project, expected=initial.id)
    assert project[1].list_artifacts() == before and project[2].selected(first.scene_id) == initial
    with pytest.raises(ValueError): project[3].select(first.artifact_id, expected_selection_id=initial.id)


def test_validated_source_bytes_are_frozen_before_publication(project, monkeypatch):
    original = project[5].read_bytes()
    decode = scene_images.decode_image
    def replacing(payload, limits):
        result = decode(payload, limits)
        project[5].write_bytes(b"changed after validation")
        return result
    monkeypatch.setattr(scene_images, "decode_image", replacing)
    image, _ = selected(project)
    with project[1].open_artifact_id(image.artifact_id) as stream:
        assert stream.read() == original


@pytest.mark.parametrize("phase", ["image", "selection", "cleanup"])
def test_publication_failures_preserve_the_actual_committed_selection(project, monkeypatch, phase):
    first, initial = selected(project)
    store = project[1]
    insert = store._index._insert
    def fail_insert(connection, manifest, payload):
        insert(connection, manifest, payload)
        if manifest.artifact_type == ("scene_image" if phase == "image" else "scene_image_selection"):
            raise OSError("injected index failure")
    if phase == "cleanup":
        def fail_cleanup(stage): raise OSError("injected post-commit cleanup failure")
        monkeypatch.setattr(store, "_discard_stage", fail_cleanup)
        imported, choice = selected(project, expected=initial.id)
        assert project[2].selected(first.scene_id) == choice
        assert project[3].selected(first.scene_id) == imported
    else:
        monkeypatch.setattr(store._index, "_insert", fail_insert)
        with pytest.raises(OSError): selected(project, expected=initial.id)
        assert project[2].selected(first.scene_id) == initial
        assert project[3].selected(first.scene_id) == first
        assert len(project[2].history(first.scene_id)) == (1 if phase == "image" else 2)


def test_stale_selection_token_retains_candidate_without_overwriting_choice(project):
    first, choice = selected(project)
    with pytest.raises(ValueError, match="changed"):
        selected(project, expected=None)
    assert len(project[2].history(first.scene_id)) == 2
    assert project[2].selected(first.scene_id) == choice


def test_corrupt_candidate_cannot_replace_selected_image(project):
    first, choice = selected(project)
    candidate = project[3].import_file(*inputs(project))
    manifest = next(m for m in project[1].list_artifacts() if m.artifact_id == candidate.artifact_id)
    (project[1].root / manifest.storage_key).write_bytes(b"corrupt")
    with pytest.raises(ValueError): project[3].select(candidate.artifact_id, expected_selection_id=choice.id)
    assert project[2].selected(first.scene_id) == choice


def test_application_service_does_not_import_decoder_storage_or_ui():
    code = "import sys; import app.application.image_intake; assert not any(n.startswith(('PIL', 'app.storage', 'app.providers', 'sqlite3', 'PySide6')) for n in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_source_reads_are_bounded_by_the_compressed_limit(project, monkeypatch):
    project[2].limits = ImageLimits(max_bytes=64)
    project[5].write_bytes(b"x" * 10000)
    original_open = Path.open
    reads = []
    class Reader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def read(self, size):
            reads.append(size)
            assert 0 < size <= 65
            return self.stream.read(size)
    def opening(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return Reader(stream) if path == project[5] else stream
    monkeypatch.setattr(Path, "open", opening)
    with pytest.raises(ValueError): selected(project)
    assert reads == [65]


def test_relative_file_selection_with_source_root_works(project):
    image = project[3].import_file(*inputs(project)[:2], project[5].name, source_root=project[5].parent)
    assert image.source_name == project[5].name
