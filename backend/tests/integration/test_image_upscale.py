"""D059 offline contract, retained lineage, cache and selection safety."""

from hashlib import sha256
from io import BytesIO

from PIL import Image
import pytest

from app.application.image_intake import ImageIntakeService
from app.application.image_upscale import ImageUpscaleService
from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.providers.image_upscale import ImageUpscaleCapabilities, ImageUpscaleRequest, ImageUpscaleResult
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_images import ProjectSceneImages
from app.storage.scene_plans import ProjectScenePlans
from app.tts.scene_sources import sentence_sources


def png(size):
    stream = BytesIO()
    Image.new("RGB", size, "red").save(stream, format="PNG")
    return stream.getvalue()


class FakeUpscaler:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def capabilities(self):
        return ImageUpscaleCapabilities("fake", "realesr-general-x4v3", "v1", "runtime-1")

    def upscale(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("GPU OOM")
        return ImageUpscaleResult(png((request.width * request.factor, request.height * request.factor)),
                                  "PNG", request.width * request.factor, request.height * request.factor,
                                  {"diagnostics": {"tile": 128}})


@pytest.fixture
def setup(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="Upscale", repository_factory=ProjectRepository) as session:
        scripts = ScriptGenerationService(session)
        scripts.append_text("A scene.", title="Section", expected_active_revision_id=session.active_script.id)
        store = LocalArtifactStore.for_project(session.repository)
        planning = ScenePlanningService(ProjectScenePlans(session.repository, store), sentence_sources)
        accepted = planning.accept(planning.suggest(session.active_script.sections[0]).id, reviewer_id="editor")
        images = ProjectSceneImages(session.repository, store)
        source_path = tmp_path / "source.png"
        source_path.write_bytes(png((12, 8)))
        source = images.import_file(accepted.id, accepted.plan.scenes[0].id, source_path)
        choice = ImageIntakeService(images).select(source.artifact_id, expected_selection_id=None)
        provider = FakeUpscaler()
        yield session, store, images, source, choice, provider, ImageUpscaleService(images, store, provider)


@pytest.mark.parametrize("factor,dimensions", [(2, (24, 16)), (4, (48, 32))])
def test_imported_source_retained_with_upscaled_lineage_and_reopen(setup, factor, dimensions):
    session, store, images, source, choice, provider, service = setup
    derivative_id = service.upscale_selected(source.scene_id, factor)
    derivative = images.image(derivative_id)
    assert (derivative.width, derivative.height) == dimensions
    assert derivative.provenance == "upscaled" and derivative.source_artifact_id == source.artifact_id
    assert derivative.source_checksum == source.checksum and derivative.source_width == 12
    assert images.image(source.artifact_id) == source
    assert images.selected(source.scene_id).artifact_id == derivative_id
    path = session.repository.workspace
    session.close()
    with ProjectSession.open(path, repository_factory=ProjectRepository) as reopened_session:
        reopened_store = LocalArtifactStore.for_project(reopened_session.repository)
        reopened = ProjectSceneImages(reopened_session.repository, reopened_store)
        assert reopened.history(source.scene_id) == (source, derivative)
        assert reopened.selected(source.scene_id).artifact_id == derivative_id
        ImageIntakeService(reopened).select(source.artifact_id, expected_selection_id=reopened.selected(source.scene_id).id)
        assert reopened.selected(source.scene_id).artifact_id == source.artifact_id


def test_cache_reuse_and_source_checksum_miss(setup, tmp_path):
    _, store, images, source, _, provider, service = setup
    first = service.upscale_selected(source.scene_id, 2)
    ImageIntakeService(images).select(source.artifact_id, expected_selection_id=images.selected(source.scene_id).id)
    assert service.upscale_selected(source.scene_id, 2) == first
    assert provider.calls == 1
    second_path = tmp_path / "second.png"
    second_path.write_bytes(png((13, 8)))
    other = images.import_file(source.acceptance_id, source.scene_id, second_path)
    ImageIntakeService(images).select(other.artifact_id, expected_selection_id=images.selected(source.scene_id).id)
    assert service.upscale_selected(source.scene_id, 2) != first
    assert provider.calls == 2 and other.checksum != source.checksum


def test_generated_source_can_be_upscaled_without_mutation(setup):
    _, store, images, imported, choice, provider, service = setup
    data = png((12, 8))
    fields = imported.to_payload()
    for key in ("artifact_id", "checksum", "size_bytes"):
        fields.pop(key)
    fields.update(source_name="generated-image.png", provenance="generated")
    manifest = store.save_artifact("generated-image.png", data,
        {"artifact_type": "scene_image", "project_id": imported.project_id,
         "scene_id": imported.scene_id, "scene_image": fields})
    generated = images.image(manifest.artifact_id)
    ImageIntakeService(images).select(generated.artifact_id, expected_selection_id=choice.id)
    derivative = images.image(service.upscale_selected(generated.scene_id, 4))
    assert derivative.source_artifact_id == generated.artifact_id
    assert images.image(generated.artifact_id) == generated
    assert images.image(imported.artifact_id) == imported


def test_failure_invalid_dimensions_and_stale_selection_preserve_source(setup):
    _, store, images, source, choice, provider, service = setup
    provider.fail = True
    with pytest.raises(RuntimeError, match="OOM"):
        service.upscale_selected(source.scene_id, 2)
    assert images.selected(source.scene_id) == choice
    provider.fail = False
    prepared = service.prepare(source.artifact_id, 2)
    wrong = ImageUpscaleResult(png((20, 16)), "PNG", 24, 16)
    with pytest.raises(ValueError, match="Decoded upscale output"):
        service.publish(prepared, wrong)
    assert images.history(source.scene_id) == (source,)
    alternative = images.import_file(source.acceptance_id, source.scene_id,
                                    _write_other(store.root.parent, png((14, 8))))
    ImageIntakeService(images).select(alternative.artifact_id, expected_selection_id=choice.id)
    with pytest.raises(ValueError, match="stale"):
        service.publish(prepared, provider.upscale(prepared[2]))
    assert images.selected(source.scene_id).artifact_id == alternative.artifact_id


def _write_other(directory, payload):
    path = directory / "other.png"
    path.write_bytes(payload)
    return path


def test_contract_rejects_invalid_scale_and_capabilities():
    request = ImageUpscaleRequest(png((12, 8)), "PNG", 12, 8, 2)
    caps = ImageUpscaleCapabilities("fake", "model", "v1", "runtime")
    caps.validate(request)
    assert caps.factors == (2, 4)
    with pytest.raises(ValueError, match="Invalid image upscale request"):
        ImageUpscaleRequest(request.image_bytes, "PNG", 12, 8, 8)
    with pytest.raises(ValueError, match="supported dimensions"):
        caps.validate(ImageUpscaleRequest(request.image_bytes, "PNG", 8192, 8, 2))
