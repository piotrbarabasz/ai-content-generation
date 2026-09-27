"""D060 exact output dimensions, reusable sources and versioned final lineage."""

from io import BytesIO

from PIL import Image
import pytest

from app.application.image_intake import ImageIntakeService
from app.application.image_presets import final_dimensions, generation_dimensions
from app.application.image_upscale import ImageUpscaleService
from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.providers.image_upscale import ImageUpscaleCapabilities, ImageUpscaleResult
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_images import ProjectSceneImages
from app.storage.scene_plans import ProjectScenePlans
from app.tts.scene_sources import sentence_sources


def png(size):
    stream = BytesIO()
    Image.new("RGB", size, (50, 100, 150)).save(stream, format="PNG")
    return stream.getvalue()


class TargetProvider:
    def __init__(self):
        self.calls = []

    def capabilities(self):
        return ImageUpscaleCapabilities("fake", "realesr-general-x4v3", "profile-v2", "runtime-v2")

    def upscale(self, request):
        self.calls.append(request)
        native = (request.width * 4, request.height * 4)
        target = (request.target_width, request.target_height)
        output = png(target)
        resized = target != native
        return ImageUpscaleResult(output, "PNG", *target, {"diagnostics": {
            "native_model_scale": 4, "native_width": native[0], "native_height": native[1],
            "final_width": target[0], "final_height": target[1],
            "final_resize_occurred": resized, "final_resize_method": "Lanczos" if resized else None,
            "tile": 128, "dtype": "float32", "elapsed_inference_seconds": 1.0,
            "peak_vram_bytes": 100,
        }})


@pytest.fixture
def project(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="D060", repository_factory=ProjectRepository) as session:
        ScriptGenerationService(session).append_text(
            "A wide scene.", title="Section", expected_active_revision_id=session.active_script.id)
        store = LocalArtifactStore.for_project(session.repository)
        planning = ScenePlanningService(ProjectScenePlans(session.repository, store), sentence_sources)
        accepted = planning.accept(planning.suggest(session.active_script.sections[0]).id, reviewer_id="editor")
        images = ProjectSceneImages(session.repository, store)
        source_path = tmp_path / "landscape.png"
        source_path.write_bytes(png((640, 360)))
        source = images.import_file(accepted.id, accepted.plan.scenes[0].id, source_path)
        selection = ImageIntakeService(images).select(source.artifact_id, expected_selection_id=None)
        provider = TargetProvider()
        service = ImageUpscaleService(images, store, provider)
        yield session, store, images, source, selection, provider, service


@pytest.mark.parametrize("orientation,generation", [
    ("landscape", (640, 360)), ("portrait", (360, 640)),
])
def test_orientation_maps_to_bounded_generation_dimensions(orientation, generation):
    assert generation_dimensions(orientation) == generation


@pytest.mark.parametrize("orientation,profile,size", [
    ("landscape", "draft", (640, 360)), ("landscape", "fhd", (1920, 1080)),
    ("landscape", "qhd", (2560, 1440)), ("landscape", "uhd4k", (3840, 2160)),
    ("portrait", "draft", (360, 640)), ("portrait", "fhd", (1080, 1920)),
    ("portrait", "qhd", (1440, 2560)), ("portrait", "uhd4k", (2160, 3840)),
])
def test_final_resolution_mapping(orientation, profile, size):
    assert final_dimensions(orientation, profile) == size


def test_final_targets_use_single_native_x4_and_resolution_specific_resize(project):
    session, store, images, source, _, provider, service = project
    finals = {}
    for profile in ("fhd", "qhd", "uhd4k"):
        selected = images.selected(source.scene_id)
        prepared = service.prepare_final(selected.artifact_id, "landscape", profile)
        assert not hasattr(prepared[2], "target_profile")  # Resolution labels stay in the application layer.
        result = provider.upscale(prepared[2])
        artifact_id = service.publish_final(prepared, result)
        finals[profile] = images.image(artifact_id)
    assert len(provider.calls) == 3
    assert [(image.width, image.height) for image in finals.values()] == [
        (1920, 1080), (2560, 1440), (3840, 2160)]
    assert [finals[key].final_resize_method for key in ("fhd", "qhd", "uhd4k")] == ["Lanczos", None, "Lanczos"]
    assert all(image.provenance == "final" and image.lineage_version == 2 for image in finals.values())
    assert all(image.source_artifact_id == source.artifact_id and image.source_checksum == source.checksum
               for image in finals.values())
    assert all((image.native_width, image.native_height) == (2560, 1440) for image in finals.values())
    assert images.image(source.artifact_id) == source
    # Switching among output presets always reuses the retained source; a cached
    # target can be selected without another neural pass.
    current = images.selected(source.scene_id)
    prepared = service.prepare_final(current.artifact_id, "landscape", "fhd")
    cached = service.cached_final(prepared)
    assert cached == finals["fhd"].artifact_id
    service.intake.select(cached, expected_selection_id=current.id)
    assert len(provider.calls) == 3
    path = session.repository.workspace
    session.close()
    with ProjectSession.open(path, repository_factory=ProjectRepository) as reopened_session:
        reopened_store = LocalArtifactStore.for_project(reopened_session.repository)
        reopened = ProjectSceneImages(reopened_session.repository, reopened_store)
        assert reopened.selected(source.scene_id).artifact_id == cached
        assert {image.target_profile for image in reopened.history(source.scene_id)} == {None, "fhd", "qhd", "uhd4k"}


def test_imported_incompatible_aspect_rejected_without_selection_change(project, tmp_path):
    _, _, images, _, selected, provider, service = project
    portrait_path = tmp_path / "portrait.png"
    portrait_path.write_bytes(png((360, 640)))
    original = images.history(selected.scene_id)[0]
    imported = images.import_file(original.acceptance_id, selected.scene_id, portrait_path)
    selection = images.selected(selected.scene_id)
    ImageIntakeService(images).select(imported.artifact_id, expected_selection_id=selection.id)
    current = images.selected(selected.scene_id)
    with pytest.raises(ValueError, match="not compatible with the 16:9 Landscape preset"):
        service.prepare_final(imported.artifact_id, "landscape", "fhd")
    assert images.selected(selected.scene_id) == current and provider.calls == []


def test_failure_and_stale_selection_do_not_publish(project):
    _, store, images, source, choice, provider, service = project
    prepared = service.prepare_final(source.artifact_id, "landscape", "fhd")
    wrong = ImageUpscaleResult(png((1900, 1080)), "PNG", 1920, 1080, {"diagnostics": {}})
    before = store.list_artifacts()
    with pytest.raises(ValueError, match="Decoded final image"):
        service.publish_final(prepared, wrong)
    assert store.list_artifacts() == before and images.selected(source.scene_id) == choice
    alternative = images.import_file(source.acceptance_id, source.scene_id,
                                     _write_image(store.root.parent, png((640, 360))))
    ImageIntakeService(images).select(alternative.artifact_id, expected_selection_id=choice.id)
    result = provider.upscale(prepared[2])
    with pytest.raises(ValueError, match="stale result"):
        service.publish_final(prepared, result)
    assert images.selected(source.scene_id).artifact_id == alternative.artifact_id


def _write_image(directory, payload):
    path = directory / "alternative.png"
    path.write_bytes(payload)
    return path
