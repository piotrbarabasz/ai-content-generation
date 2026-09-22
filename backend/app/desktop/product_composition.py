"""Installed-product composition over the managed per-user CPU runtime."""

from __future__ import annotations

import os
import platform

from app.desktop.audio_composition import compose_audio
from app.desktop.deployment import UserDataPaths
from app.providers.tts_catalog import build_tts_catalog
from app.runtime.profile_catalog import load_approved_profile
from app.runtime.profiles import HostCapabilities
from app.runtime.provisioning import PiperProvisioner
from app.runtime.section_audio import ManagedSectionAudio


def compose_installed_audio(session, *, paths: UserDataPaths | None = None):
    """Use an already activated D008 runtime; never provision or download implicitly."""

    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        return None
    paths = (paths or UserDataPaths.discover()).prepare()
    host = HostCapabilities("windows", "x86_64", ("cpu",))
    runtime = PiperProvisioner(paths.runtimes / "piper-cpu", host)
    profile = load_approved_profile("piper-cpu-windows-x64", host)
    if runtime.active(profile) is None:
        return None
    managed = ManagedSectionAudio(runtime, paths.models / "piper-voices", paths.cache / "section-audio")
    return compose_audio(
        session,
        catalog=build_tts_catalog(),
        voices=managed,
        outputs=managed,
        launch=managed.worker_launch(),
        preview_root=paths.cache / "voice-previews",
        providers=("piper",),
        settings={"piper": {"device": "cpu"}},
    )


def compose_installed_chatterbox_audio(session, *, paths: UserDataPaths | None = None):
    """Select only a fully provisioned/activated D029 runtime and model set.

    This is deliberately explicit: the existing installed Piper composition and
    its D045 distribution remain unchanged.
    """
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        return None
    from app.desktop.audio_composition import compose_candidate_chatterbox_audio
    from app.runtime.chatterbox_assets import ChatterboxAssets
    from app.runtime.chatterbox_audio import CandidateChatterboxAudio
    from app.runtime.chatterbox_distribution import load_approved_chatterbox_distribution
    from app.runtime.chatterbox_provisioning import ChatterboxProvisioner

    paths = (paths or UserDataPaths.discover()).prepare()
    distribution = load_approved_chatterbox_distribution()
    runtime = ChatterboxProvisioner(paths.runtimes / "chatterbox-v3").active(distribution)
    models = ChatterboxAssets(paths.models / "chatterbox-v3")
    if runtime is None or models.installed() is None:
        return None
    managed = CandidateChatterboxAudio(
        runtime.worker_launch(), runtime.health, runtime.distribution_fingerprint,
        models.root, paths.cache / "section-audio", runtime_cache=runtime.cache_root,
    )
    return compose_candidate_chatterbox_audio(
        session, managed=managed, preview_root=paths.cache / "voice-previews")


__all__ = ["compose_installed_audio", "compose_installed_chatterbox_audio"]
