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


__all__ = ["compose_installed_audio"]
