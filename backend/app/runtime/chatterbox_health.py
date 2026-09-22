"""Private-worker-only imports and fixed capability checks; no model downloads."""

from importlib import metadata
import inspect
import json
import os
from pathlib import Path
import sys

from .chatterbox_profile import ChatterboxHealth, PACKAGES, SOURCE_REVISION, profile_fingerprint


def check_private_runtime(device):
    root = Path(sys.executable).resolve().parent
    if (sys.platform != "win32" or sys.version.split()[0] != "3.11.9" or not sys.flags.isolated
            or not sys.flags.no_site or Path(sys.prefix).resolve() != root
            or any(not Path(p).resolve().is_relative_to(root) for p in sys.path)):
        raise ValueError("Chatterbox needs a private Windows CPython 3.11.9 runtime, not a development venv.")
    distribution_path = os.environ.get("AICS_CHATTERBOX_DISTRIBUTION")
    if distribution_path:
        from .chatterbox_distribution import distribution_from_payload
        profile_path = Path(distribution_path).resolve(strict=True)
        if not profile_path.is_relative_to(root):
            raise ValueError("Chatterbox distribution metadata escaped the private runtime.")
        distribution = distribution_from_payload(json.loads(profile_path.read_text(encoding="utf-8")))
        packages = distribution.package_versions.items()
    else:
        # Candidate-only diagnostic compatibility. Approved launches always carry
        # the full reviewed distribution metadata above.
        packages = PACKAGES
    for name, version in packages:
        dist = metadata.distribution(name)
        if dist.version != version or not Path(dist.locate_file("")).resolve().is_relative_to(root):
            raise ValueError("Chatterbox private package identity mismatch: " + name)
    # Version 0.1.7 is shared by incompatible upstream source revisions.
    source = json.loads(metadata.distribution("chatterbox-tts").read_text("direct_url.json") or "{}")
    if (source.get("url") != "https://github.com/resemble-ai/chatterbox.git"
            or source.get("vcs_info", {}).get("commit_id") != SOURCE_REVISION):
        raise ValueError("Chatterbox V3 source revision is not the pinned commit.")
    import torch
    import torchaudio
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    for module in (torch, torchaudio, sys.modules[ChatterboxMultilingualTTS.__module__]):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise ValueError("Chatterbox module escaped the private runtime.")
    if (torch.version.cuda != "12.4" or not torch.cuda.is_available()
            or not isinstance(device, str) or not device.startswith("cuda:")
            or not device[5:].isdigit() or int(device[5:]) >= torch.cuda.device_count()):
        raise ValueError("The requested CUDA 12.4 device is unavailable; no fallback was selected.")
    for method in (ChatterboxMultilingualTTS.from_local, ChatterboxMultilingualTTS.from_pretrained):
        parameters = inspect.signature(method).parameters
        if any(name not in parameters or parameters[name].kind == inspect.Parameter.POSITIONAL_ONLY
               for name in ("device", "t3_model")):
            raise ValueError("Chatterbox runtime does not expose the pinned V3 API.")
    if not {"en", "pl"}.issubset(ChatterboxMultilingualTTS.get_supported_languages()):
        raise ValueError("Chatterbox source language support differs from the profile.")
    properties = torch.cuda.get_device_properties(device)
    return ChatterboxHealth(profile_fingerprint(), device, ("en", "pl"), properties.name, properties.total_memory)
