"""Explicit shipped allowlist; discovery reads metadata, never imports runtimes."""

from importlib.resources import files

from .profiles import HostCapabilities, ProfileError, RuntimeProfile


_APPROVED = {
    "piper-cpu-windows-x64": (
        "piper_cpu_windows_x64.json",
        "4b4ac9e5c0f6f8d650e66b41ec69580e45736cebead2ea42e8e421eb00fbede0",
    ),
}


def approved_profile_ids() -> tuple[str, ...]:
    return tuple(sorted(_APPROVED))


def require_approved(profile: RuntimeProfile, host: HostCapabilities) -> None:
    """D008 must call this before installation; a valid schema alone is not approval."""
    profile.ensure_compatible(host)
    approved = _APPROVED.get(profile.profile_id)
    if approved is None or profile.fingerprint != approved[1]:
        raise ProfileError("Profile content is not in the shipped approved allowlist.")


def load_approved_profile(profile_id: str, host: HostCapabilities) -> RuntimeProfile:
    approved = _APPROVED.get(profile_id)
    if approved is None:
        raise ProfileError("Unknown approved runtime profile.")
    content = files("app.runtime").joinpath(approved[0]).read_text(encoding="utf-8")
    profile = RuntimeProfile.from_json(content)
    require_approved(profile, host)
    return profile
