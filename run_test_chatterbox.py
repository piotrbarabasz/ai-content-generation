from dataclasses import replace
from pathlib import Path
import shutil

from app.desktop.__main__ import main
from app.desktop.audio_composition import compose_candidate_chatterbox_audio
from app.runtime.chatterbox_assets import ChatterboxAssets
from app.runtime.chatterbox_audio import CandidateChatterboxAudio
from app.runtime.chatterbox_distribution import load_approved_chatterbox_distribution
from app.runtime.chatterbox_provisioning import ChatterboxProvisioner
from app.storage.local_store import LocalArtifactStore
from app.storage.reference_audio import ProjectReferenceAudio


REPO = Path(r"D:\Projects\ai-content-generation")

RUNTIME_ROOT = REPO / ".runtime" / "d029-managed-current"
MODEL_ROOT = REPO / ".runtime" / "d029-models"

# Wszystkie mutowalne cache testowego workera trzymamy
# POZA immutable managed runtime.
CACHE_ROOT = REPO / ".runtime" / "desktop-chatterbox-test"
WORKER_CACHE = CACHE_ROOT / "worker-cache"


distribution = load_approved_chatterbox_distribution()

print("Checking Chatterbox runtime...")
print("Runtime:", RUNTIME_ROOT)
print("Models:", MODEL_ROOT)

runtime = ChatterboxProvisioner(RUNTIME_ROOT).active(distribution)

if runtime is None:
    raise RuntimeError("Chatterbox runtime nie jest aktywny.")

models = ChatterboxAssets(MODEL_ROOT)

if models.installed() is None:
    raise RuntimeError("Modele Chatterbox nie przechodzą weryfikacji.")


# Chatterbox potrzebuje przygotowanego, offline'owego pkuseg.
# Kopiujemy zweryfikowany seed z immutable runtime do mutowalnego cache.
pkuseg_source = runtime.directory / "runtime-cache" / "pkuseg"

pkuseg_target = (
    WORKER_CACHE
    / "runtime-cache"
    / "pkuseg"
)

pkuseg_target.parent.mkdir(
    parents=True,
    exist_ok=True,
)

shutil.copytree(
    pkuseg_source,
    pkuseg_target,
    dirs_exist_ok=True,
)

# Numba/librosa nie respektuje PYTHONDONTWRITEBYTECODE dla swoich
# skompilowanych .nbc/.nbi, więc dostaje osobny cache.
numba_cache = WORKER_CACHE / "numba"
numba_cache.mkdir(parents=True, exist_ok=True)


print()
print("Chatterbox verified.")
print("Installed runtime:", runtime.directory)
print("Device:", runtime.health.device)
print("Languages:", runtime.health.languages)
print("Mutable worker cache:", WORKER_CACHE)


def audio_factory(session):
    project_cache = CACHE_ROOT / session.project.id

    references = ProjectReferenceAudio(
        session.repository,
        LocalArtifactStore.for_project(session.repository),
        project_cache / "reference-audio",
    )

    launch = runtime.worker_launch()

    launch = replace(
        launch,
        environment=launch.environment | {
            "NUMBA_CACHE_DIR": str(numba_cache),
        },
    )

    managed = CandidateChatterboxAudio(
        launch,
        runtime.health,
        runtime.distribution_fingerprint,
        models.root,
        project_cache / "section-audio",

        # WAŻNE:
        # wcześniej było runtime.cache_root, czyli immutable runtime.
        # Teraz wszystkie HF/Torch/pkuseg/temp cache idą tutaj.
        runtime_cache=WORKER_CACHE,

        references=references,
        reference_cache=references.runtime_root,
    )

    return compose_candidate_chatterbox_audio(
        session,
        managed=managed,
        preview_root=project_cache / "voice-previews",
        reference_audio=references,
    )


print()
print("Starting AI Content Studio...")

raise SystemExit(
    main(audio_factory=audio_factory)
)